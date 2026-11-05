"""コドモンの連絡帳に入力し、下書き保存する。送信は押さない。

使い方:
    python3 scripts/codmon_fill.py draft.json           # 埋めるだけ（保存しない）。埋まった値を表示する
    python3 scripts/codmon_fill.py draft.json --save    # 埋めて「下書き保存」を押す
    python3 scripts/codmon_fill.py --read               # 今入っている内容を表示するだけ
    python3 scripts/codmon_fill.py draft.json --save --date 2026-11-06   # 日付を指定する

draft.json に書いた欄だけを変える。書かない欄は入っている内容のまま残るので、夜と朝に分けて少しずつ入れられる。
書いた欄に別の内容がすでに入っている時は、何も変えずに止まり、入っている内容を表示する。
上書きしてよいと保護者が言った時だけ --overwrite を付ける。

日付を指定しない時は、午前は今日、12 時以降は明日の連絡帳を開く（夜に入れる分は、次の朝に出す連絡帳に入れる）。

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
お迎えは「時間」と「誰」を改行で分ける（"13時\nママ"）。

ブラウザは日本時間で開き、画面の日付が開きたい日にならなければ止まる
（クラウドの時計は UTC なので、そのままだと朝 9 時前は前の日の連絡帳が開く）。
保存した後は開き直して、入れた値が残っているかを確かめる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import local_env

LOGIN_URL = "https://parents.codmon.com/"
CONTACT_URL = "https://parents.codmon.com/contact"
POOP_STATES = ["普通", "軟便", "少量便", "硬便", "下痢便"]
SHOT_DIR = Path(__file__).resolve().parent.parent / "tmp"
JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"


def round_time(hm: str, step: int = 15) -> str:
    h, m = (int(x) for x in hm.split(":"))
    total = (h * 60 + m + step // 2) // step * step % (24 * 60)
    return f"{total // 60}:{total % 60:02d}"


def temp_label(celsius: float) -> str:
    return f"{round(float(celsius), 1):.1f}℃"


def count_label(n: int) -> str:
    return f"{min(max(int(n), 0), 3)}回"


def date_label(d: datetime) -> str:
    """コドモンの画面の日付の書き方（例: 11月5日(木)）。"""
    return f"{d.month}月{d.day}日({WEEKDAYS[d.weekday()]})"


def short_label(d: datetime) -> str:
    """画面の上の、前の日・次の日のボタンの書き方（例: 5(木)）。"""
    return f"{d.day}({WEEKDAYS[d.weekday()]})"


def default_target(now: datetime) -> datetime:
    """開く連絡帳の日付。午前は今日、12 時以降は次の朝に出す明日の分。"""
    return now + timedelta(days=1) if now.hour >= 12 else now


def expected_parts(draft: dict) -> dict[str, list[tuple[int, str]]]:
    """下書きの選ぶ欄を、read_back の値の何番目の部分に入るかと一緒に並べる。"""
    out: dict[str, list[tuple[int, str]]] = {}
    for key, label in (("poop_night", "排便 夜"), ("poop_morning", "排便 朝")):
        v = draft.get(key) or {}
        parts = []
        if v.get("state"):
            parts.append((0, v["state"]))
        if v.get("count") is not None:
            parts.append((1, count_label(v["count"])))
        if parts:
            out[label] = parts
    s = draft.get("sleep") or {}
    parts = [(i, round_time(s[k])) for i, k in enumerate(("bed", "wake")) if s.get(k)]
    if parts:
        out["睡眠"] = parts
    t = draft.get("temperature") or {}
    parts = []
    if t.get("celsius") is not None:
        parts.append((0, temp_label(t["celsius"])))
    if t.get("time"):
        parts.append((1, round_time(t["time"])))
    if parts:
        out["体温"] = parts
    return out


SELECT_SPLIT = {"排便 夜": " / ", "排便 朝": " / ", "睡眠": " 〜 ", "体温": " ("}
PLACEHOLDERS = {"状態", "回数", "入眠", "起床", "体温", "測定時間", ""}
TEXT_FIELDS = {"meal_night": "食事 夜", "meal_morning": "食事 朝", "pickup": "お迎え", "comment": "コメント"}


def conflicts(draft: dict, existing: dict) -> list[str]:
    """下書きに書いた欄のうち、別の内容がすでに入っている欄の名前を返す。同じ内容や空の欄は含めない。"""
    out = []
    for key, label in TEXT_FIELDS.items():
        now = existing.get(label, "").strip()
        if draft.get(key) and now and now != draft[key].strip():
            out.append(label)
    for label, parts in expected_parts(draft).items():
        have = [x.strip(" )") for x in existing.get(label, "").split(SELECT_SPLIT[label])]
        for i, want in parts:
            now = have[i] if i < len(have) else ""
            if now not in PLACEHOLDERS and now != want:
                out.append(label)
                break
    return out


def mismatches(draft: dict, values: dict) -> list[str]:
    """下書きの文字の欄のうち、画面の値と食い違っている物を返す（入れたはずの欄が空になる事があった）。"""
    return [k for k, label in TEXT_FIELDS.items() if draft.get(k) and values.get(label, "").strip() != draft[k].strip()]


# 入力に要らない読み込み（画像・フォント・計測）を止めて速くする
SKIP_TYPES = {"image", "media", "font"}
SKIP_HOSTS = ("googletagmanager.com", "google-analytics.com", "newrelic.com", "nr-data.net", "paidy.com")


def skip_extras(context) -> None:
    def route(r):
        if r.request.resource_type in SKIP_TYPES or any(h in r.request.url for h in SKIP_HOSTS):
            return r.abort()
        return r.continue_()
    context.route("**/*", route)


def contact_tab(page):
    return page.get_by_text("連絡", exact=True).last


def login(page) -> None:
    """ログインして、下のタブが出るまで待つ。ログイン済みならそのまま。"""
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    chooser = page.get_by_text("すでにアカウントをお持ちの方")
    chooser.or_(contact_tab(page)).first.wait_for(timeout=30000)
    if chooser.is_visible():
        chooser.click()
        email = page.locator("input[placeholder='メールアドレス']")
        password = page.locator("input[type=password]")
        for _ in range(3):
            # 画面が出た直後は、入れた文字が消される事があるので、残ったか確かめる
            email.fill(os.environ["CODMON_EMAIL"])
            password.fill(os.environ["CODMON_PASSWORD"])
            page.wait_for_timeout(200)
            if email.input_value() and password.input_value():
                break
        page.get_by_text("ログインする", exact=True).click()
    page.wait_for_url("**/home", timeout=30000)
    contact_tab(page).wait_for(timeout=30000)


def open_contact(page, target: datetime) -> None:
    """下のタブから連絡帳を開き、画面の上のボタンで target の日付まで移る。

    URL を直接開くと保存済みの内容が出るまで 6 秒ほどかかるが、タブからだと 1 秒ほどで出る。
    """
    if not contact_tab(page).is_visible():
        login(page)
    contact_tab(page).click()
    meal = page.locator("textarea[placeholder='食事内容']").first
    # 送信済みの日は欄が隠れているので、見えるまでは待たない
    meal.wait_for(state="attached", timeout=30000)
    today = datetime.now(JST)
    if not page.get_by_text(date_label(today), exact=True).count():
        raise SystemExit(f"連絡帳の日付が今日（{date_label(today)}）ではありません。時計か画面の作りを確かめてください")
    days = (target.date() - today.date()).days
    step = 1 if days > 0 else -1
    for i in range(abs(days)):
        d = today + timedelta(days=step * (i + 1))
        # 移った直後は前の日の内容が欄に残っているので、その日の内容が届くまで待つ
        loaded = f"search_start_display_date={d:%Y-%m-%d}"
        with page.expect_response(lambda r: r.request.method == "GET" and "/comments" in r.url and loaded in r.url, timeout=15000):
            page.get_by_text(short_label(d), exact=True).first.click()
        page.get_by_text(date_label(d), exact=True).first.wait_for(timeout=15000)
        page.wait_for_timeout(700)
    # 保存済みの内容は欄が出た少し後に入る。入ったらすぐ進み、空の日は 3 秒だけ待つ
    for _ in range(15):
        if meal.input_value() or any_selected(page):
            page.wait_for_timeout(300)
            return
        page.wait_for_timeout(200)


def any_selected(page) -> bool:
    return page.evaluate("() => [...document.querySelectorAll('select')].slice(1, 9).some(s => s.value !== '')")


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


def type_into(el, text: str) -> None:
    """fill だけだと、続けて入れた別の欄の値が画面に残らない事があったので、打って欄を離れる。"""
    el.click()
    el.fill("")
    el.press_sequentially(text)
    el.blur()
    el.page.wait_for_timeout(300)


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
            type_into(page.locator("textarea[placeholder='食事内容']").nth(i), draft[key])
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
        type_into(page.locator("textarea[placeholder^='時間']").first, draft["pickup"])
        done.append("pickup")

    if draft.get("comment"):
        type_into(page.locator("textarea[placeholder='お子さまの様子について']"), draft["comment"])
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    local_env.load()
    p = argparse.ArgumentParser()
    p.add_argument("draft", nargs="?")
    p.add_argument("--read", action="store_true", help="今入っている内容を表示するだけ")
    p.add_argument("--date", help="開く連絡帳の日付（YYYY-MM-DD）。省略時は午前は今日、12 時以降は明日")
    p.add_argument("--overwrite", action="store_true", help="すでに入っている内容を上書きする")
    p.add_argument("--save", action="store_true", help="「下書き保存」を押す（送信はしない）")
    p.add_argument("--headed", action="store_true")
    a = p.parse_args()
    if not a.read and not a.draft:
        p.error("draft.json を指定するか --read を付けてください")
    draft = json.loads(Path(a.draft).read_text(encoding="utf-8")) if a.draft else {}
    now = datetime.now(JST)
    target = datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=JST) if a.date else default_target(now)
    target_label = date_label(target)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launch = {"headless": not a.headed}
        if os.environ.get("CHROME_PATH"):
            launch["executable_path"] = os.environ["CHROME_PATH"]
        else:
            launch["channel"] = "chrome"
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(viewport={"width": 420, "height": 900}, locale="ja-JP", timezone_id="Asia/Tokyo")
        skip_extras(context)
        page = context.new_page()
        login(page)
        open_contact(page, target)
        existing = read_back(page)
        if a.read:
            browser.close()
            print(json.dumps({"date": target_label, "existing": existing}, ensure_ascii=False, indent=2))
            return
        editable = page.locator("textarea[placeholder='食事内容']").first.is_visible()
        if not editable or "送信済" in page.locator("body").inner_text():
            browser.close()
            print(json.dumps({"stopped": f"{target_label}の連絡帳はもう送信済みなので何も変えていない", "existing": existing}, ensure_ascii=False, indent=2))
            sys.exit(2)
        clash = conflicts(draft, existing)
        if clash and not a.overwrite:
            browser.close()
            print(json.dumps({"stopped": "別の内容が入っている欄があるので何も変えていない", "date": target_label, "fields": clash, "existing": existing}, ensure_ascii=False, indent=2))
            sys.exit(2)
        done = fill(page, draft)
        values = read_back(page)
        bad = mismatches(draft, values)
        if bad:
            # もう一度だけ入れ直す
            fill(page, {k: draft[k] for k in bad})
            values = read_back(page)
            bad = mismatches(draft, values)
        if bad:
            browser.close()
            print(json.dumps({"stopped": "入れた値が画面に残らなかったので保存していない", "fields": bad, "values": values}, ensure_ascii=False, indent=2))
            sys.exit(3)
        result = {"date": target_label, "filled": done, "values": values, "saved": False}
        if a.save:
            try:
                # 保存の通信が返るまで待つ
                with page.expect_response(lambda r: r.request.method != "GET" and "ps-api.codmon.com" in r.url, timeout=15000):
                    page.get_by_text("下書き保存", exact=True).first.click()
            except Exception:
                page.wait_for_timeout(3000)
            SHOT_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(SHOT_DIR / "codmon_fill.png"), full_page=True)
            # 開き直して、保存された値を読む（ログインは同じまま、新しいタブで開く）
            again = context.new_page()
            again.goto(LOGIN_URL, wait_until="domcontentloaded")
            open_contact(again, target)
            result["values"] = read_back(again)
            bad = mismatches(draft, result["values"])
            if bad:
                browser.close()
                print(json.dumps({"stopped": "保存した後に開き直すと値が残っていなかった", "fields": bad, **result}, ensure_ascii=False, indent=2))
                sys.exit(3)
            result["saved"] = True
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
