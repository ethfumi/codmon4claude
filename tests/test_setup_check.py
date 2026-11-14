import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import codmon_browser as cb  # noqa: E402
import setup_check as sc  # noqa: E402


class HistoryStateTest(unittest.TestCase):
    def state(self, tmp):
        with mock.patch.multiple(cb, HISTORY_FILE=tmp / "h.json", HISTORY_STARTED=tmp / "h.started"):
            return sc.history_state()

    def test_history_state(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            self.assertEqual(self.state(tmp), "まだ読んでいない")
            started = tmp / "h.started"
            started.touch()
            self.assertEqual(self.state(tmp), "裏で読んでいる")
            # 読み終わらないまま時間がたった時は、読んでいる扱いにしない
            old = time.time() - cb.HISTORY_STARTED_SECONDS - 1
            os.utime(started, (old, old))
            self.assertEqual(self.state(tmp), "まだ読んでいない")
            (tmp / "h.json").write_text(json.dumps({"days": [{}, {}]}), encoding="utf-8")
            self.assertEqual(self.state(tmp), "読み終わった（中身のある日 2 日）")


class BuildReportTest(unittest.TestCase):
    def test_ready(self):
        report = sc.build_report([], {"state": "not_used"}, {"state": "ok", "status": 200})
        self.assertIn("準備はそろっている", report)

    def test_missing_env_and_blocked_network(self):
        report = sc.build_report(list(sc.REQUIRED_ENV), {"state": "not_used"}, {"state": "blocked", "status": None})
        self.assertIn("外へ出られない", report)
        self.assertIn("準備が足りない", report)


if __name__ == "__main__":
    unittest.main()
