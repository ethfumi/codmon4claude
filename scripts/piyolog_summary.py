"""ぴよログのデータフィードを読み、連絡帳に書きやすい形にまとめる。

使い方:
    python3 scripts/piyolog_summary.py            # 環境変数 PIYOLOG_FEED_URL から取る
    python3 scripts/piyolog_summary.py --file x.json
    python3 scripts/piyolog_summary.py --json     # まとめを JSON で出す

データフィードの仕様: https://www.piyolog.com/app/piyolog/data_feed/ja/index.html
日時は UTC で来るので、日本時間に直して並べる。未入力の値は項目ごと省かれて来るので、0 とみなさない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
SUPPORTED_SCHEMA = 1

MEAL_TYPES = {"Meal": "ごはん", "Solid": "離乳食", "Snack": "おやつ"}
CONDITION_TYPES = {"Cough": "せき", "Vomiting": "吐いた", "Rash": "発疹", "Injury": "けが", "Hospital": "病院", "Medicine": "くすり"}
POOP_AMOUNT = {"minimum": "ごく少し", "small": "少なめ", "normal": "ふつう", "large": "多め"}
POOP_HARDNESS = {"diarrhea": "下痢", "soft": "やわらかめ", "normal": "ふつう", "hard": "かため"}
POOP_COLOR = {"white": "白", "yellow": "黄", "orange": "オレンジ", "brown": "茶", "green": "緑", "red": "赤", "black": "黒"}


def load_feed(url: str | None, path: str | None) -> dict:
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if not url:
        raise SystemExit("PIYOLOG_FEED_URL が設定されていません")
    req = urllib.request.Request(url, headers={"User-Agent": "codmon4claude"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def to_jst(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST)


def hm(dt: datetime) -> str:
    return dt.strftime("%m/%d %H:%M")


def summarize(feed: dict) -> dict:
    if feed.get("schema_version") != SUPPORTED_SCHEMA:
        raise SystemExit(f"未対応のデータ形式です（schema_version={feed.get('schema_version')}）")

    out: dict = {
        "range": {"from": hm(to_jst(feed["range"]["from"])), "to": hm(to_jst(feed["range"]["to"]))},
        "temperatures": [],
        "meals": [],
        "drinks": [],
        "milk": [],
        "poops": [],
        "pee_count": 0,
        "sleep": [],
        "conditions": [],
        "memos": [],
    }
    for r in sorted(feed.get("records", []), key=lambda r: r["datetime"]):
        t = r["type"]
        when = hm(to_jst(r["datetime"]))
        memo = r.get("memo")
        value = r.get("value") or {}
        if t == "Temperature" and "value" in value:
            out["temperatures"].append({"time": when, "celsius": value["value"]})
        elif t in MEAL_TYPES:
            out["meals"].append({"time": when, "kind": MEAL_TYPES[t], "memo": memo})
        elif t == "Drink":
            out["drinks"].append({"time": when, "ml": value.get("value"), "memo": memo})
        elif t in ("Formula", "BreastFeeding", "ExpressedBreastMilk"):
            out["milk"].append({"time": when, "kind": t, "ml": value.get("value")})
        elif t == "Poop":
            d = r.get("details") or {}
            out["poops"].append({
                "time": when,
                "amount": POOP_AMOUNT.get(d.get("amount")),
                "hardness": POOP_HARDNESS.get(d.get("hardness")),
                "color": POOP_COLOR.get(d.get("color")),
                "memo": memo,
            })
        elif t == "Pee":
            out["pee_count"] += 1
        elif t in ("Sleep", "WakeUp"):
            out["sleep"].append({"time": when, "event": "寝た" if t == "Sleep" else "起きた"})
        elif t in CONDITION_TYPES:
            out["conditions"].append({"time": when, "kind": CONDITION_TYPES[t], "memo": memo})
        elif t == "Memo" and memo:
            out["memos"].append({"time": when, "memo": memo})
    return out


def _with_memo(text: str, memo: str | None) -> str:
    return f"{text}（{memo}）" if memo else text


def to_text(s: dict) -> str:
    lines = [f"ぴよログの記録 {s['range']['from']} 〜 {s['range']['to']}"]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"■ {title}")
        lines.extend(f"- {i}" for i in items) if items else lines.append("- 記録なし")

    section("体温", [f"{t['time']} {t['celsius']}℃" for t in s["temperatures"]])
    section("食事", [_with_memo(f"{m['time']} {m['kind']}", m["memo"]) for m in s["meals"]])
    if s["drinks"]:
        section("のみもの", [_with_memo(f"{d['time']} {d['ml']}ml" if d["ml"] else d["time"], d["memo"]) for d in s["drinks"]])
    if s["milk"]:
        section("ミルク・授乳", [f"{m['time']} {m['ml']}ml" if m["ml"] else m["time"] for m in s["milk"]])
    poop_items = []
    for p in s["poops"]:
        detail = "・".join(x for x in (p["amount"], p["hardness"], p["color"] and p["color"] + "色") if x)
        poop_items.append(_with_memo(f"{p['time']} {detail}".rstrip(), p["memo"]))
    section(f"うんち {len(s['poops'])} 回", poop_items)
    lines.append(f"■ おしっこ {s['pee_count']} 回")
    section("睡眠", [f"{x['time']} {x['event']}" for x in s["sleep"]])
    if s["conditions"]:
        section("体調", [_with_memo(f"{c['time']} {c['kind']}", c["memo"]) for c in s["conditions"]])
    if s["memos"]:
        section("メモ", [f"{m['time']} {m['memo']}" for m in s["memos"]])
    return "\n".join(lines)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--file")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    summary = summarize(load_feed(os.environ.get("PIYOLOG_FEED_URL"), a.file))
    print(json.dumps(summary, ensure_ascii=False, indent=2) if a.json else to_text(summary))


if __name__ == "__main__":
    main()
