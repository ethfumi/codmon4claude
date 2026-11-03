"""コドモンの連絡帳に入力し、下書き保存する。送信は押さない。

使い方:
    python3 scripts/codmon_fill.py draft.json           # 埋めるだけ（保存しない）。埋まった値を表示する
    python3 scripts/codmon_fill.py draft.json --save    # 埋めて「下書き保存」を押す
    python3 scripts/codmon_fill.py --read               # 今入っている内容を表示するだけ

その日の連絡帳にすでに内容が入っている時は、何も変えずに止まり、入っている内容を表示する。
上書きしてよいと保護者が言った時だけ --overwrite を付ける。

draft.json の形（書かない欄はそのまま）:
    {
      "poop_night":   {"state": "普通", "count": 1},
      "poop_morning": {"state": "軟便", "count": 0},
      "meal_night":   "カレー（半分くらい）、ぶどう",
      "meal_morning": "パン、バナナ、牛乳",
      "sleep":        {"bed": "20:45", "wake": "6:00"},
      "temperature":  {"celsius": 36.8, "time": "6:10"},
      "pickup":       "18:00 父",
      "comment":      "朝だけ少しせき"
    }

時刻は 15 分刻み、体温は 0.1℃刻みに丸めて選ぶ。欄の一覧は docs/codmon-form.md。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import local_env

LOGIN_URL = "https://parents.codmon.com/"
CONTACT_URL = "https://parents.codmon.com/contact"
POOP_STATES = ["普通", "軟便", "少量便", "硬便", "下痢便"]
SHOT_DIR = Path(__file__).resolve().parent.parent / "tmp"


def round_time(hm: str, step: int = 15) -> str:
    h, m = (int(x) for x in hm.split(":"))
    total = (h * 60 + m + step // 2) // step * step % (24 * 60)
    return f"{total // 60}:{total % 60:02d}"


def temp_label(celsius: float) -> str:
    return f"{round(float(celsius), 1):.1f}℃"


def count_label(n: int) -> str:
    return f"{min(max(int(n), 0), 3)}回"


def login(page) -> None:
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.get_by_text("すでにアカウントをお持ちの方").click()
    page.wait_for_selector("input[type=password]")
    page.wait_for_timeout(1500)
    page.locator("input[placeholder='メールアドレス']").fill(os.environ["CODMON_EMAIL"])
    page.locator("input[type=password]").fill(os.environ["CODMON_PASSWORD"])
    page.get_by_text("ログインする", exact=True).click()
    page.wait_for_url("**/home", timeout=30000)
    page.wait_for_timeout(2000)


def selects_by_placeholder(page) -> dict[str, list]:
    """未選択の時に出る見出し（状態・回数・入眠など）ごとに、select を出てくる順に並べる。"""
    out: dict[str, list] = {}
    for el in page.locator("select").all():
        head = el.evaluate(
            "e => { const o=[...e.options].find(o=>o.disabled||o.value===''||o.hidden); return o ? o.text.trim() : e.options[e.selectedIndex].text.trim(); }"
        )
        out.setdefault(head, []).append(el)
    return out


def textarea(page, placeholder: str, nth: int = 0):
    return page.locator("textarea").filter(has=page.locator(f"xpath=self::*[@placeholder={json.dumps(placeholder)}]")).nth(nth)


def fill(page, draft: dict) -> list[str]:
    done: list[str] = []
    sel = selects_by_placeholder(page)
    if "状態" not in sel or "回数" not in sel:
        raise SystemExit("連絡帳の欄が見つかりません。画面の作りが変わったかもしれません（docs/codmon-form.md）")

    for i, key in enumerate(("poop_night", "poop_morning")):
        v = draft.get(key)
        if not v:
            continue
        if v.get("state"):
            if v["state"] not in POOP_STATES:
                raise SystemExit(f"{key}.state は {POOP_STATES} のどれか: {v['state']}")
            sel["状態"][i].select_option(label=v["state"])
        if v.get("count") is not None:
            sel["回数"][i].select_option(label=count_label(v["count"]))
        done.append(key)

    for i, key in enumerate(("meal_night", "meal_morning")):
        if draft.get(key):
            page.locator("textarea[placeholder='食事内容']").nth(i).fill(draft[key])
            done.append(key)

    if draft.get("sleep"):
        s = draft["sleep"]
        if s.get("bed"):
            sel["入眠"][0].select_option(label=round_time(s["bed"]))
        if s.get("wake"):
            sel["起床"][0].select_option(label=round_time(s["wake"]))
        done.append("sleep")

    if draft.get("temperature"):
        t = draft["temperature"]
        if t.get("celsius") is not None:
            sel["体温"][0].select_option(label=temp_label(t["celsius"]))
        if t.get("time"):
            sel["測定時間"][0].select_option(label=round_time(t["time"]))
        done.append("temperature")

    if draft.get("pickup"):
        page.locator("textarea[placeholder='時間\\n誰'], textarea[placeholder^='時間']").first.fill(draft["pickup"])
        done.append("pickup")

    if draft.get("comment"):
        page.locator("textarea[placeholder='お子さまの様子について']").fill(draft["comment"])
        done.append("comment")
    return done


def read_back(page) -> dict:
    sel = selects_by_placeholder(page)

    def picked(head: str, i: int = 0) -> str:
        els = sel.get(head) or []
        return els[i].evaluate("e => e.options[e.selectedIndex].text.trim()") if len(els) > i else ""

    ta = page.locator("textarea[placeholder='食事内容']")
    return {
        "排便 夜": f"{picked('状態', 0)} / {picked('回数', 0)}",
        "排便 朝": f"{picked('状態', 1)} / {picked('回数', 1)}",
        "食事 夜": ta.nth(0).input_value(),
        "食事 朝": ta.nth(1).input_value(),
        "睡眠": f"{picked('入眠')} 〜 {picked('起床')}",
        "体温": f"{picked('体温')} ({picked('測定時間')})",
        "お迎え": page.locator("textarea[placeholder^='時間']").first.input_value(),
        "コメント": page.locator("textarea[placeholder='お子さまの様子について']").input_value(),
    }


def has_content(values: dict) -> bool:
    empty = {"", "状態 / 回数", "入眠 〜 起床", "体温 (測定時間)"}
    return any(v.strip() not in empty for v in values.values())


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    local_env.load()
    p = argparse.ArgumentParser()
    p.add_argument("draft", nargs="?")
    p.add_argument("--read", action="store_true", help="今入っている内容を表示するだけ")
    p.add_argument("--overwrite", action="store_true", help="すでに入っている内容を上書きする")
    p.add_argument("--save", action="store_true", help="「下書き保存」を押す（送信はしない）")
    p.add_argument("--headed", action="store_true")
    a = p.parse_args()
    if not a.read and not a.draft:
        p.error("draft.json を指定するか --read を付けてください")
    draft = json.loads(Path(a.draft).read_text(encoding="utf-8")) if a.draft else {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launch = {"headless": not a.headed}
        if os.environ.get("CHROME_PATH"):
            launch["executable_path"] = os.environ["CHROME_PATH"]
        else:
            launch["channel"] = "chrome"
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 420, "height": 900}, locale="ja-JP")
        login(page)
        page.goto(CONTACT_URL, wait_until="networkidle")
        page.wait_for_selector("textarea[placeholder='食事内容']", timeout=30000)
        # 保存済みの内容は欄が出た後に遅れて読み込まれるので待つ
        page.wait_for_timeout(5000)
        existing = read_back(page)
        if a.read:
            browser.close()
            print(json.dumps({"existing": existing}, ensure_ascii=False, indent=2))
            return
        if has_content(existing) and not a.overwrite:
            browser.close()
            print(json.dumps({"stopped": "すでに内容が入っているので何も変えていない", "existing": existing}, ensure_ascii=False, indent=2))
            sys.exit(2)
        done = fill(page, draft)
        result = {"filled": done, "values": read_back(page)}
        if a.save:
            page.get_by_role("button", name="下書き保存").click()
            page.wait_for_timeout(3000)
            result["saved"] = True
        else:
            result["saved"] = False
        SHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / "codmon_fill.png"), full_page=True)
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
