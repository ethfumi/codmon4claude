"""リポジトリを変更する操作を、2 つの決まりで止める。

Claude Code の PreToolUse フックから呼ばれる。

1. 送る中身に、コドモンのパスワード・メールアドレス、ぴよログの URL、子どもの名前、園の名前が入っていたら、
   誰の会話でも止める。名前は、コドモンの画面を開いた時に tmp/codmon_names.json に控えたものを使う。
2. 連絡帳を使うだけの会話がコミットや PR を作らないよう、開発者以外の会話では止める。
   GitHub のユーザー名が開発者なら通す。ユーザー名が分からない時も止める。
   自分のフォークを直したい人は、環境変数 CODMON4CLAUDE_DEV=1 を入れると通る（1 の決まりは残る）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DEVELOPER = "ethfumi"
DEV_FLAG = "CODMON4CLAUDE_DEV"
REPO_ROOT = Path(__file__).resolve().parent.parent
NAMES_FILE = REPO_ROOT / "tmp" / "codmon_names.json"

BASH_PATTERN = re.compile(
    r"\bgit\s+(?:-\S+\s+(?:\S+\s+)?)*[\"']?(commit|push|merge|rebase|tag)\b"
    r"|\bgh\s+(pr|repo|release|issue|gist)\s+(create|merge|edit|delete|close|comment)\b"
    r"|\bgh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)"
    r"|\bgh\s+api\b.*\s(-f|-F|--field|--raw-field|--input)\b"
)
MCP_PATTERN = re.compile(r"github.*(create|update|merge|push|delete|fork|comment)", re.IGNORECASE)
MESSAGE = "このリポジトリを変更する操作は、開発者の会話でしかできません。変更はせず、気づいたことを相手に短く伝えて、連絡帳を続けてください。"
LEAK_MESSAGE = (
    "送ろうとした変更に、{kinds}が入っているので止めました。値は表示しません。"
    "その行を変更から外してから、やり直してください。"
)


def is_blocked(tool_name: str, tool_input: dict) -> bool:
    """リポジトリを変更する操作なら True。"""
    if tool_name == "Bash":
        return bool(BASH_PATTERN.search(tool_input.get("command", "")))
    return bool(MCP_PATTERN.search(tool_name))


def load_names(path: Path = NAMES_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _variants(value: str, split: bool) -> list[str]:
    value = value.strip()
    out = [value, re.sub(r"\s+", "", value)]
    if split:
        out += value.split()
    return out


def secrets(env: dict, names: dict) -> dict[str, set[str]]:
    """種類ごとの、変更に入っていてはいけない値。短すぎる値は外す。"""
    found: dict[str, set[str]] = {}

    def add(kind: str, values) -> None:
        values = {v for v in values if len(v) >= 2}
        if values:
            found.setdefault(kind, set()).update(values)

    add("コドモンのパスワード", [v for v in [env.get("CODMON_PASSWORD", "")] if len(v) >= 4])
    add("コドモンのメールアドレス", [v for v in [env.get("CODMON_EMAIL", "")] if len(v) >= 4])
    url = env.get("PIYOLOG_FEED_URL", "").strip()
    if url:
        add("ぴよログの URL", [url] + [s for s in re.split(r"[/?=&#.]", url) if len(s) >= 16])
    for name in names.get("children") or []:
        add("子どもの名前", _variants(str(name), split=True))
    for name in names.get("facilities") or []:
        add("園の名前", _variants(str(name), split=False))
    return found


def leaks(text: str, found: dict[str, set[str]]) -> list[str]:
    return [kind for kind, values in found.items() if any(v in text for v in values)]


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=20)
        return r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def outgoing(tool_name: str, tool_input: dict, cwd: str) -> str:
    """この操作で GitHub へ出ていく可能性のある中身。"""
    if tool_name != "Bash":
        return json.dumps(tool_input, ensure_ascii=False)
    return "\n".join([
        tool_input.get("command", ""),
        # コミットされる変更（ステージ済みと、-a で入る変更）
        _git(cwd, "diff", "HEAD"),
        # まだどのリモートにも無いコミット
        # 作者の欄は比べない（メールアドレスがコドモンと同じことがある）
        _git(cwd, "log", "-p", "--format=%B", "--branches", "--not", "--remotes"),
    ])


def github_login() -> str | None:
    """今の会話の GitHub のユーザー名を返す。分からなければ None。"""
    try:
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "codmon4claude"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request("https://api.github.com/user", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.load(res).get("login")
    except (OSError, ValueError):
        return None


def should_stop(login: str | None) -> bool:
    return login != DEVELOPER


def stop(message: str) -> None:
    sys.stderr.reconfigure(encoding="utf-8")
    print(message, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return
    tool_name, tool_input = data.get("tool_name", ""), data.get("tool_input") or {}
    if not is_blocked(tool_name, tool_input):
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import local_env

    local_env.load()
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or str(REPO_ROOT)
    kinds = leaks(outgoing(tool_name, tool_input, cwd), secrets(dict(os.environ), load_names()))
    if kinds:
        stop(LEAK_MESSAGE.format(kinds="・".join(kinds)))
    if os.environ.get(DEV_FLAG) == "1":
        return
    if should_stop(github_login()):
        stop(MESSAGE)


if __name__ == "__main__":
    main()
