"""コドモンを開いたブラウザを裏で持ち続け、コマンドごとの起動とログインを省く。

    python3 scripts/codmon_browser.py start   # 裏で起動して、すぐ戻る
    python3 scripts/codmon_browser.py stop
    python3 scripts/codmon_browser.py prefetch   # 前の 14 日の食事を裏で読み、tmp/meal_history.json に置く

使われないまま 15 分たつか、起動から 1 時間たつか、日付が変わると自分で閉じる。
プロフィールは一時フォルダで、閉じると消える。動かなければ codmon_fill.py は毎回起動する形に戻る。
"""

from __future__ import annotations

import hashlib
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import local_env

STATE_DIR = Path(__file__).resolve().parent.parent / "tmp"
IDLE_SECONDS = 15 * 60
LIFE_SECONDS = 60 * 60
WAIT_READY_SECONDS = 15
JST = timezone(timedelta(hours=9))
SERVE_LOCK = 1000
USE_LOCK = 1500


def _key() -> str:
    return hashlib.sha256(os.environ.get("CODMON_EMAIL", "").encode()).hexdigest()[:12]


def port() -> int:
    return 9300 + int(_key(), 16) % 500


def _ready_file() -> Path:
    return STATE_DIR / f"codmon_browser_{_key()}.ready"


def _used_file() -> Path:
    return STATE_DIR / f"codmon_browser_{_key()}.used"


def endpoint() -> str:
    return f"http://127.0.0.1:{port()}"


def hold_lock(offset: int) -> socket.socket | None:
    """ポートをつかんで排他にする。プロセスが消えれば自動で外れる。取れなければ None。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port() + offset))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def _port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port())) == 0


def _touch_used() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    _used_file().touch()


def start() -> None:
    if _port_open() or not os.environ.get("CODMON_EMAIL") or not os.environ.get("CODMON_PASSWORD"):
        return
    _touch_used()
    _ready_file().unlink(missing_ok=True)
    subprocess.Popen([sys.executable, __file__, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())


HISTORY_FILE = STATE_DIR / "meal_history.json"
HISTORY_DAYS = 14


def _detached() -> dict:
    return {"creationflags": 0x00000008 | 0x08000000} if os.name == "nt" else {"start_new_session": True}


def prefetch() -> None:
    """前の日々の食事の欄を裏で読む。すぐ戻る。読み終わるまでファイルは無い。"""
    HISTORY_FILE.unlink(missing_ok=True)
    if not os.environ.get("CODMON_EMAIL") or not os.environ.get("CODMON_PASSWORD"):
        return
    fill = Path(__file__).resolve().parent / "codmon_fill.py"
    args = [sys.executable, str(fill), "--history", str(HISTORY_DAYS), "--out", str(HISTORY_FILE)]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())


def ensure() -> str | None:
    """開いたブラウザの接続先を返す。無ければ起動して待つ。間に合わなければ None。"""
    start()
    deadline = time.monotonic() + WAIT_READY_SECONDS
    while time.monotonic() < deadline:
        if _ready_file().exists() and _port_open():
            _touch_used()
            return endpoint()
        time.sleep(0.1)
    return None


def stop() -> None:
    try:
        os.kill(int(_ready_file().read_text(encoding="utf-8")), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    _ready_file().unlink(missing_ok=True)


def serve() -> None:
    import codmon_fill as cf
    from playwright.sync_api import sync_playwright

    lock = hold_lock(SERVE_LOCK)
    if not lock:
        return
    began = time.monotonic()
    day = datetime.now(JST).date()
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            "", args=[f"--remote-debugging-port={port()}"], **cf.launch_options(), **cf.CONTEXT_OPTIONS
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            cf.prepare_context(context, page, cf.load_login_state())
            cf.skip_extras(context)
            cf.login(page)
            cf.save_login_state(cf.login_only(context.storage_state()))
            _ready_file().write_text(str(os.getpid()), encoding="utf-8")
            while True:
                page.wait_for_timeout(5000)
                idle = time.time() - _used_file().stat().st_mtime
                if idle > IDLE_SECONDS or time.monotonic() - began > LIFE_SECONDS or datetime.now(JST).date() != day:
                    break
        finally:
            _ready_file().unlink(missing_ok=True)
            context.close()


if __name__ == "__main__":
    local_env.load()
    {"start": start, "stop": stop, "serve": serve, "prefetch": prefetch}[sys.argv[1]]()
