"""セッションの始まりに、使う前の準備がそろっているかを点検する。

Claude Code の SessionStart フックから呼ばれ、結果を Claude への補足として返す。
Claude はこの結果を見て、足りない準備を保護者に 1 つずつ案内する。

子どもの記録の中身は出さない。ぴよログから取れた件数だけを返す。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import local_env

TIMEOUT_SEC = 8
CODMON_URL = "https://parents.codmon.com/"
PIYOLOG_FEED_PREFIX = "https://feed.piyolog.com/v1/feed/"

# ぴよログは使わない家庭もあるので、PIYOLOG_FEED_URL は無くてもよい
REQUIRED_ENV = {
    "CODMON_EMAIL": "コドモンにログインするメールアドレス",
    "CODMON_PASSWORD": "コドモンのパスワード",
}


def fetch(url: str) -> tuple[str, int | None, bytes]:
    """URL を GET して (状態, HTTP ステータス, 本文) を返す。

    状態は ok / http_error / blocked / unreachable のどれか。
    blocked は、クラウド環境のネットワーク設定で外へ出られなかった場合。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "codmon4claude-setup-check"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
            return "ok", res.status, res.read()
    except urllib.error.HTTPError as e:
        return "http_error", e.code, e.read()
    except urllib.error.URLError as e:
        reason = str(e.reason)
        # クラウド環境のプロキシに止められると、CONNECT が 403 で返る
        if "Tunnel connection failed" in reason or "403" in reason:
            return "blocked", None, b""
        return "unreachable", None, b""
    except OSError:
        return "unreachable", None, b""


def check_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]


def check_piyolog() -> dict:
    url = os.environ.get("PIYOLOG_FEED_URL", "").strip()
    if not url:
        return {"state": "not_used"}
    if not url.startswith(PIYOLOG_FEED_PREFIX):
        return {"state": "bad_url"}
    state, status, body = fetch(url)
    if state == "ok":
        try:
            data = json.loads(body)
            return {"state": "ok", "records": len(data.get("records", []))}
        except ValueError:
            return {"state": "bad_response"}
    if state == "http_error" and status == 404:
        # URL の間違い・期限切れ・失効がすべて 404 になる
        return {"state": "feed_unavailable"}
    return {"state": state, "status": status}


def check_codmon() -> dict:
    state, status, _ = fetch(CODMON_URL)
    return {"state": state, "status": status}


def build_report(missing_env: list[str], piyolog: dict, codmon: dict) -> str:
    lines = ["# codmon4claude の準備の点検結果", ""]

    network_blocked = piyolog.get("state") == "blocked" or codmon.get("state") == "blocked"
    lines.append(f"- ネットワーク: {'外へ出られない（設定が要る）' if network_blocked else 'OK'}")
    if missing_env:
        names = "、".join(f"{n}（{REQUIRED_ENV[n]}）" for n in missing_env)
        lines.append(f"- 環境変数: 足りない → {names}")
    else:
        lines.append("- 環境変数: OK")

    piyolog_text = {
        "ok": f"OK（直近の記録 {piyolog.get('records')} 件）",
        "not_used": "使っていない（URL が未設定。使う時は docs/setup.md の 2）",
        "bad_url": "URL の形が違う（https://feed.piyolog.com/v1/feed/ で始まるはず）",
        "feed_unavailable": "URL が無効（打ち間違い・期限切れ・失効のどれか）",
        "blocked": "ネットワーク設定で止められている",
        "bad_response": "応答が読めない",
    }.get(piyolog["state"], f"つながらない（{piyolog['state']} {piyolog.get('status') or ''}）")
    lines.append(f"- ぴよログ: {piyolog_text}")

    codmon_text = {
        "ok": "OK",
        "blocked": "ネットワーク設定で止められている",
    }.get(codmon["state"], f"つながらない（{codmon['state']} {codmon.get('status') or ''}）")
    lines.append(f"- コドモン: {codmon_text}")

    if network_blocked and len(missing_env) == len(REQUIRED_ENV):
        lines.append("")
        lines.append(
            "ネットワークの許可も環境変数も無い。どの環境で始めたかは点検では分からない。環境の名前は保護者が自由に付けられる。"
            "連絡帳用の環境が一覧にあるかを確かめる（list_environments があれば使う）。"
            "無ければ、create_environment があれば許可ホスト feed.piyolog.com、parents.codmon.com、ps-api.codmon.com で提案する。秘密の値は入れない。"
            "道具が無ければ docs/setup.md の 2 のカードが出ない時を案内する。"
            "あれば、その環境で新しいセッションを始めるよう案内する。"
        )
    elif not network_blocked and missing_env:
        lines.append("")
        lines.append(
            "ネットワークは通るが環境変数が足りない。環境変数は保存してもすぐには入らない。"
            "入れたばかりなら、10 分ほど何も送らずに置いてから何か送ってもらうか、連絡帳専用の環境で新しいセッションを始めてもらう。"
            "まだ入れていなければ docs/setup.md の 2 の環境変数を案内する。"
            "案内の最初に、ぴよログを使っているかを 1 回だけ聞く。使っていれば URL の手順も出す。使っていなければ要らないと伝える。"
        )

    ready = not network_blocked and not missing_env and piyolog["state"] in ("ok", "not_used") and codmon["state"] == "ok"
    lines.append("")
    if ready:
        lines.append("準備はそろっている。保護者が写真を上げたら、連絡帳の入力を始めてよい。")
    else:
        lines.append(
            "準備が足りない。docs/setup.md の手順のうち、足りない所だけを、上から 1 つずつ保護者に案内する。"
            "貼る文字はコードブロックで出し、手で打たせない。"
        )
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    local_env.load()
    missing_env = check_env()
    piyolog = check_piyolog()
    codmon = check_codmon()
    report = build_report(missing_env, piyolog, codmon)
    if "--human" in sys.argv:
        print(report)
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": report,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
