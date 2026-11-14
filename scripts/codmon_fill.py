"""コドモンの連絡帳に入力し、下書き保存する。送信は押さない。

使い方:
    python3 scripts/codmon_fill.py draft.json           # 埋めるだけ（保存しない）。埋まった値を表示する
    python3 scripts/codmon_fill.py draft.json --save    # 埋めて「下書き保存」を押す
    python3 scripts/codmon_fill.py --read               # 今入っている内容を表示するだけ
    python3 scripts/codmon_fill.py draft.json --save --date 2026-11-06   # 日付を指定する
    python3 scripts/codmon_fill.py --history 14 --out tmp/notebook_history.json   # 前の 14 日の連絡帳を読むだけ

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
      "pickup":       "18時\nパパ",
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
import hashlib
import json
import os
import re
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import local_env

LOGIN_URL = "https://parents.codmon.com/"
STATE_DIR = Path(__file__).resolve().parent.parent / "tmp"
LOGIN_STORAGE_KEYS = {"codmon_parent_id", "CapacitorStorage.codmon_semantic_id", "CapacitorStorage.codmon_client_unique_id", "CapacitorStorage.codmon_following_id"}
POOP_STATES = ["普通", "軟便", "少量便", "硬便", "下痢便"]
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


def history_dates(today: datetime, days: int) -> list[datetime]:
    """さかのぼって読む日。昨日から古い方へ並べる。"""
    return [today - timedelta(days=i) for i in range(1, days + 1)]


def split_parts(label: str, value: str) -> list[str]:
    """選ぶ欄の値を部分に分ける。未選択の部分は空にする。"""
    parts = [x.strip(" )") for x in value.split(SELECT_SPLIT[label])]
    return ["" if p in PLACEHOLDERS else p for p in parts]


def history_entry(d: datetime, values: dict) -> dict | None:
    """1 日分の連絡帳の欄（read_back の形）。全部の欄が空なら None。"""
    filled = any(any(split_parts(k, v)) if k in SELECT_SPLIT else v.strip() for k, v in values.items())
    if not filled:
        return None
    return {**{k: v.strip() for k, v in values.items()}, "date": f"{d:%Y-%m-%d}", "weekday": WEEKDAYS[d.weekday()]}


def most_common(values: list, at_least: int = 1):
    """いちばん多い値。同じ数なら先に出た物（新しい日）。

    at_least 回に届かない時と、入っている日が半分に届かない時（いつもは空の欄）は None。
    """
    total = len(values)
    values = [v for v in values if v]
    if not values or len(values) * 2 < total:
        return None
    value, n = Counter(values).most_common(1)[0]
    return value if n >= at_least else None


def meal_items(value: str) -> list[str]:
    """食事の欄を品目に分ける。括弧の中（食べた量）は外す。"""
    value = re.sub(r"（[^）]*）|\([^)]*\)", "", value)
    return [w.strip() for w in re.split(r"[、,・\n]", value) if w.strip()]


def usual_items(values: list[str]) -> list[str]:
    """食事の欄が入っている日の半分より多く出てくる品目を、多い順に並べる。

    献立の半分以上をその品目が占めていない時（毎日違う献立の家庭）は、推測できないので空にする。
    """
    days = [items for items in (set(meal_items(v)) for v in values) if items]
    count = Counter(w for items in days for w in items)
    usual = [w for w, n in count.most_common() if n * 2 > len(days)]
    if not usual or sum(len(items.intersection(usual)) / len(items) for items in days) * 2 < len(days):
        return []
    return usual


def usual_values(days: list[dict]) -> dict:
    """前の日々の連絡帳から、いつもの値を draft.json と同じ形で出す。days は新しい日から並ぶ。

    お迎えは同じ曜日ごと、コメントは 2 回以上同じ書き方をした時だけ。
    入っている日が半分に届かない欄は、いつもは空なので出さない。
    """
    out: dict = {}
    for key, label in (("poop_night", "排便 夜"), ("poop_morning", "排便 朝")):
        pairs = [tuple(split_parts(label, d.get(label, ""))[:2]) for d in days]
        pick = most_common([p if len(p) == 2 and all(p) else None for p in pairs])
        if pick:
            out[key] = {"state": pick[0], "count": int(pick[1].rstrip("回"))}
    sleep = [split_parts("睡眠", d.get("睡眠", "")) + ["", ""] for d in days]
    bed, wake = most_common([s[0] for s in sleep]), most_common([s[1] for s in sleep])
    if bed or wake:
        out["sleep"] = {k: v for k, v in (("bed", bed), ("wake", wake)) if v}
    temp = [split_parts("体温", d.get("体温", "")) + ["", ""] for d in days]
    celsius, at = most_common([t[0] for t in temp]), most_common([t[1] for t in temp])
    if celsius or at:
        out["temperature"] = {k: v for k, v in (("celsius", float(celsius.rstrip("℃")) if celsius else None), ("time", at)) if v}
    pickup = {w: p for w in WEEKDAYS if (p := most_common([d.get("お迎え", "") for d in days if d.get("weekday") == w]))}
    if pickup:
        out["pickup_by_weekday"] = pickup
    comment = most_common([d.get("コメント", "") for d in days], at_least=2)
    if comment:
        out["comment"] = comment
    for key, label in (("meal_night_items", "食事 夜"), ("meal_morning_items", "食事 朝")):
        items = usual_items([d.get(label, "") for d in days])
        if items:
            out[key] = items
    return out


def history_is_read(data: dict) -> bool:
    """この会話で一度読み終わっていれば、読み直さない。日付が変わっても、読んだ中身は会話に残っている。"""
    return bool(data.get("read_on"))


def write_atomic(path: Path, data: dict) -> None:
    """読み終わった時だけファイルが現れるように、別の名前で書いてから置き換える。"""
    path.parent.mkdir(exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, path)


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
SELECT_FIELDS = {"poop_night": "排便 夜", "poop_morning": "排便 朝", "sleep": "睡眠", "temperature": "体温"}
TEXT_FIELDS = {"meal_night": "食事 夜", "meal_morning": "食事 朝", "pickup": "お迎え", "comment": "コメント"}


def empty_fields(values: dict) -> list[str]:
    """まだ何も入っていない部分の名前を、画面の並びで返す。"""
    out = []
    for label, value in values.items():
        if label in SELECT_SPLIT:
            for part in (x.strip(" )") for x in value.split(SELECT_SPLIT[label])):
                if part in PLACEHOLDERS and part:
                    out.append(f"{label} {part}" if not label == part else label)
        elif not value.strip():
            out.append(label)
    return out


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
    """下書きの欄のうち、画面の値と食い違っている物を返す（入れたはずの欄が空になる事があった）。"""
    out = [k for k, label in TEXT_FIELDS.items() if draft.get(k) and values.get(label, "").strip() != draft[k].strip()]
    for key, label in SELECT_FIELDS.items():
        have = [x.strip(" )") for x in values.get(label, "").split(SELECT_SPLIT[label])]
        for i, want in expected_parts({key: draft.get(key)}).get(label, []):
            if i >= len(have) or have[i] != want:
                out.append(key)
                break
    return out


# 入力に要らない読み込み（画像・フォント・計測）を止めて速くする
SKIP_TYPES = {"image", "media", "font"}
SKIP_HOSTS = ("googletagmanager.com", "google-analytics.com", "newrelic.com", "nr-data.net", "paidy.com", "firebaseinstallations.googleapis.com")
CONTEXT_OPTIONS = {"viewport": {"width": 420, "height": 900}, "locale": "ja-JP", "timezone_id": "Asia/Tokyo"}


def login_state_path() -> Path:
    """同じ会話の 2 回目からログインを省くためのファイル。アカウントごとに分ける。git には入らない。"""
    name = hashlib.sha256(os.environ["CODMON_EMAIL"].encode()).hexdigest()[:12]
    return STATE_DIR / f"codmon_login_{name}.json"


def login_only(state: dict) -> dict:
    """ログインに要る物だけを残す。連絡帳の控え（子どもの情報）や翻訳の表は残さない。"""
    origins = [
        {"origin": o["origin"], "localStorage": [i for i in o.get("localStorage", []) if i["name"] in LOGIN_STORAGE_KEYS]}
        for o in state.get("origins", [])
    ]
    return {"cookies": state.get("cookies", []), "origins": origins}


def launch_options(headless: bool = True) -> dict:
    options = {"headless": headless}
    if os.environ.get("CHROME_PATH"):
        options["executable_path"] = os.environ["CHROME_PATH"]
    else:
        options["channel"] = "chrome"
    return options


def prepare_context(context, page, state: dict | None) -> None:
    """残しておいたログイン状態を、開いているブラウザに入れる。"""
    if not state:
        return
    context.add_cookies(state["cookies"])
    items = {i["name"]: i["value"] for o in state.get("origins", []) for i in o.get("localStorage", [])}
    page.add_init_script(f"for (const [k, v] of Object.entries({json.dumps(items)})) localStorage.setItem(k, v)")


def attach_kept_browser(pw):
    """開いたままのブラウザにつなぐ。使えなければ None。"""
    try:
        import codmon_browser

        url = codmon_browser.ensure()
        if not url:
            return None
        # 同じページを 2 つのコマンドで動かすと別の日へ入れてしまうので、1 つだけが使う
        lock = codmon_browser.hold_lock(codmon_browser.USE_LOCK)
        if not lock:
            return None
        browser = pw.chromium.connect_over_cdp(url, timeout=3000)
        context = browser.contexts[0]
        skip_extras(context)
        return browser, context, context.pages[0], lock
    except Exception:
        return None


def clear_notebook_copy(page) -> None:
    """画面が localStorage に控えた連絡帳を消す。控えがあると、保存されていない内容も欄に出る。"""
    page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('codmon_comments_')).forEach(k => localStorage.removeItem(k))")


def open_fresh(page, target: datetime) -> None:
    """控えを消してホームへ戻り、コドモンから読み直した連絡帳を開く。"""
    if not contact_tab(page).is_visible():
        login(page)
    clear_notebook_copy(page)
    page.get_by_text("ホーム", exact=True).last.click()
    page.wait_for_url("**/home", timeout=15000)
    open_contact(page, target)
    remember_names(page)


NAMES_FILE = STATE_DIR / "codmon_names.json"


def split_names(label: str) -> dict:
    """連絡先の「子ども > 施設」を、子どもの名前と施設の名前に分ける。"""
    child, _, facility = (x.strip() for x in label.partition(">"))
    return {"children": [child] if child else [], "facilities": [facility] if facility else []}


def remember_names(page) -> None:
    """コミットに子どもの名前と園の名前が入らないよう、フックが読む控えを tmp に置く。"""
    try:
        labels = page.locator("select").first.evaluate("e => [...e.options].map(o => o.text.trim())")
    except Exception:
        return
    names = {"children": [], "facilities": []}
    for label in labels:
        for key, values in split_names(label).items():
            names[key] += [v for v in values if v not in names[key]]
    if not names["children"]:
        return
    STATE_DIR.mkdir(exist_ok=True)
    NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")


def load_login_state() -> dict | None:
    try:
        return json.loads(login_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_login_state(state: dict) -> None:
    path = login_state_path()
    STATE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    path.chmod(0o600)


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


@contextmanager
def comments_response(page, d: datetime):
    """その日の連絡帳の内容が本文まで届くのを待つ。click はこの中で行う。"""
    loaded = f"search_start_display_date={d:%Y-%m-%d}"
    with page.expect_response(lambda r: r.request.method == "GET" and "/comments" in r.url and loaded in r.url, timeout=15000) as info:
        yield
    info.value.body()


def settle(page, meal) -> None:
    """届いた内容が欄に入るのを待つ。入るまでの実測は 0.02 秒ほど。

    移る前の日の内容を読まないよう 0.1 秒は待ち、0.3 秒たっても入らなければ空の日とする。
    """
    page.wait_for_timeout(100)
    for _ in range(4):
        if meal.input_value() or any_selected(page):
            return
        page.wait_for_timeout(50)


def open_contact(page, target: datetime) -> None:
    """下のタブから連絡帳を開き、画面の上のボタンで target の日付まで移る。

    URL を直接開くと保存済みの内容が出るまで 6 秒ほどかかるが、タブからだと 1 秒ほどで出る。
    """
    if not contact_tab(page).is_visible():
        login(page)
    today = datetime.now(JST)
    with comments_response(page, today):
        contact_tab(page).click()
    meal = page.locator("textarea[placeholder='食事内容']").first
    # 送信済みの日は欄が隠れているので、見えるまでは待たない
    meal.wait_for(state="attached", timeout=30000)
    if not page.get_by_text(date_label(today), exact=True).count():
        raise SystemExit(f"連絡帳の日付が今日（{date_label(today)}）ではありません。時計か画面の作りを確かめてください")
    days = (target.date() - today.date()).days
    step = 1 if days > 0 else -1
    for i in range(abs(days)):
        step_to(page, today + timedelta(days=step * (i + 1)))
    settle(page, meal)


def step_to(page, d: datetime) -> None:
    """画面の上のボタンで、隣の日 d に移る。"""
    # 移った直後は前の日の内容が欄に残っているので、その日の内容が届くまで待つ
    with comments_response(page, d):
        # 保存の直後は通知がボタンに重なり、普通の click だと 3.5 秒ほど待たされる
        page.get_by_text(short_label(d), exact=True).first.dispatch_event("click")
    page.get_by_text(date_label(d), exact=True).first.wait_for(timeout=15000)


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


def type_into(el, text: str) -> None:
    """fill だけだと、続けて入れた別の欄の値が画面に残らない事があったので、打って欄を離れる。"""
    el.click()
    el.fill("")
    el.press_sequentially(text)
    el.blur()


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


def read_history(page, days: int) -> dict:
    """画面の上のボタンで 1 日ずつさかのぼり、画面に出た欄を読む。何も変えない。"""
    today = datetime.now(JST)
    open_fresh(page, today)
    meal = page.locator("textarea[placeholder='食事内容']")
    entries = []
    for d in history_dates(today, days):
        step_to(page, d)
        settle(page, meal.first)
        entry = history_entry(d, read_back(page))
        if entry:
            entries.append(entry)
    return {
        "read_on": f"{today:%Y-%m-%d}",
        "from": f"{today - timedelta(days=days):%Y-%m-%d}",
        "to": f"{today - timedelta(days=1):%Y-%m-%d}",
        "days": entries,
        "usual": usual_values(entries),
    }


def work(a, page, target: datetime, draft: dict, target_label: str) -> tuple[dict, int]:
    if a.history:
        return read_history(page, a.history), 0
    open_fresh(page, target)
    existing = read_back(page)
    if a.read:
        return {"date": target_label, "existing": existing, "empty": empty_fields(existing)}, 0
    editable = page.locator("textarea[placeholder='食事内容']").first.is_visible()
    if not editable or "送信済" in page.locator("body").inner_text():
        return {"stopped": f"{target_label}の連絡帳はもう送信済みなので何も変えていない", "existing": existing}, 2
    clash = conflicts(draft, existing)
    if clash and not a.overwrite:
        return {"stopped": "別の内容が入っている欄があるので何も変えていない", "date": target_label, "fields": clash, "existing": existing}, 2
    done = fill(page, draft)
    values = read_back(page)
    bad = mismatches(draft, values)
    if bad:
        # もう一度だけ入れ直す
        fill(page, {k: draft[k] for k in bad})
        values = read_back(page)
        bad = mismatches(draft, values)
    if bad:
        return {"stopped": "入れた値が画面に残らなかったので保存していない", "fields": bad, "values": values}, 3
    result = {"date": target_label, "filled": done, "values": values, "saved": False}
    if a.save:
        try:
            # 保存の通信が返るまで待つ
            with page.expect_response(lambda r: r.request.method != "GET" and "ps-api.codmon.com" in r.url, timeout=15000):
                page.get_by_text("下書き保存", exact=True).first.click()
        except Exception:
            pass  # 保存できたかは、この後に読み直して確かめる
        open_fresh(page, target)
        result["values"] = read_back(page)
        bad = mismatches(draft, result["values"])
        if bad:
            return {"stopped": "保存した後に読み直すと値が残っていなかった", "fields": bad, **result}, 3
        result["saved"] = True
    result["empty"] = empty_fields(result["values"])
    return result, 0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    local_env.load()
    p = argparse.ArgumentParser()
    p.add_argument("draft", nargs="?")
    p.add_argument("--read", action="store_true", help="今入っている内容を表示するだけ")
    p.add_argument("--date", help="開く連絡帳の日付（YYYY-MM-DD）。省略時は午前は今日、12 時以降は明日")
    p.add_argument("--overwrite", action="store_true", help="すでに入っている内容を上書きする")
    p.add_argument("--save", action="store_true", help="「下書き保存」を押す（送信はしない）")
    p.add_argument("--history", type=int, metavar="日数", help="前の日数分の連絡帳を読むだけ")
    p.add_argument("--out", help="--history の結果を書くファイル（読み終わった時だけ現れる）")
    p.add_argument("--headed", action="store_true")
    a = p.parse_args()
    if a.history is not None and not 1 <= a.history <= 31:
        p.error("--history は 1 から 31 まで")
    if not a.read and not a.draft and not a.history:
        p.error("draft.json を指定するか --read か --history を付けてください")
    draft = json.loads(Path(a.draft).read_text(encoding="utf-8")) if a.draft else {}
    now = datetime.now(JST)
    target = datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=JST) if a.date else default_target(now)
    target_label = date_label(target)

    for name in ("NO_PROXY", "no_proxy"):
        os.environ[name] = ",".join(x for x in (os.environ.get(name), "127.0.0.1") if x)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        kept = None if a.headed else attach_kept_browser(pw)
        result, code = None, 0
        if kept:
            browser, context, page, lock = kept
            try:
                result, code = work(a, page, target, draft, target_label)
            except (Exception, SystemExit):
                # 毎回起動の形でやり直す。確認も読み直すので、やり直しても安全
                import codmon_browser

                codmon_browser.stop()
            finally:
                browser.close()
                lock.close()
        if result is None:
            browser = pw.chromium.launch(**launch_options(not a.headed))
            context = browser.new_context(**CONTEXT_OPTIONS, storage_state=load_login_state())
            skip_extras(context)
            page = context.new_page()
            login(page)
            save_login_state(login_only(context.storage_state()))
            result, code = work(a, page, target, draft, target_label)
            browser.close()
    if a.history and a.out:
        write_atomic(Path(a.out), result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
