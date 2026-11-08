import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import codmon_fill as cf  # noqa: E402


class HistoryTest(unittest.TestCase):
    def test_history_dates(self):
        got = [f"{d:%Y-%m-%d}" for d in cf.history_dates(datetime(2026, 3, 2), 3)]
        self.assertEqual(got, ["2026-03-01", "2026-02-28", "2026-02-27"])

    def test_history_entry(self):
        d = datetime(2026, 11, 7)
        self.assertEqual(cf.history_entry(d, "カレー ", ""), {"date": "2026-11-07", "食事 夜": "カレー", "食事 朝": ""})
        self.assertIsNone(cf.history_entry(d, " ", ""))

    def test_write_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meal_history.json"
            cf.write_atomic(path, {"meals": [{"食事 夜": "鍋"}]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"meals": [{"食事 夜": "鍋"}]})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["meal_history.json"])


class CodmonFillTest(unittest.TestCase):
    def test_round_time(self):
        self.assertEqual(cf.round_time("8:10"), "8:15")
        self.assertEqual(cf.round_time("22:00"), "22:00")
        self.assertEqual(cf.round_time("23:55"), "0:00")

    def test_date_label(self):
        self.assertEqual(cf.date_label(datetime(2026, 11, 5)), "11月5日(木)")
        self.assertEqual(cf.date_label(datetime(2026, 11, 1)), "11月1日(日)")

    def test_mismatches(self):
        draft = {"meal_night": "ごはん、みかん", "meal_morning": "パン、牛乳", "pickup": "13時\nママ"}
        values = {"食事 夜": "ごはん、みかん", "食事 朝": "", "お迎え": "13時\nママ", "コメント": ""}
        self.assertEqual(cf.mismatches(draft, values), ["meal_morning"])
        values["食事 朝"] = "パン、牛乳"
        self.assertEqual(cf.mismatches(draft, values), [])

    def test_default_target(self):
        self.assertEqual(cf.default_target(datetime(2026, 11, 5, 7, 0)).day, 5)
        self.assertEqual(cf.default_target(datetime(2026, 11, 5, 20, 0)).day, 6)
        self.assertEqual(cf.default_target(datetime(2026, 11, 30, 20, 0)).month, 12)

    def test_short_label(self):
        self.assertEqual(cf.short_label(datetime(2026, 11, 5)), "5(木)")

    def test_conflicts_keeps_other_fields(self):
        existing = {"排便 夜": "状態 / 回数", "排便 朝": "状態 / 回数", "食事 夜": "カレー", "食事 朝": "",
                    "睡眠": "入眠 〜 起床", "体温": "体温 (測定時間)", "お迎え": "", "コメント": ""}
        # 夜に入れた欄はそのまま、朝の欄だけ足す時は止まらない
        self.assertEqual(cf.conflicts({"meal_morning": "パン"}, existing), [])
        # 同じ内容なら止まらない
        self.assertEqual(cf.conflicts({"meal_night": "カレー"}, existing), [])
        # 別の内容が入っている欄だけ返す
        self.assertEqual(cf.conflicts({"meal_night": "うどん", "comment": "元気"}, existing), ["食事 夜"])

    def test_conflicts_select_parts(self):
        existing = {"排便 夜": "普通 / 回数", "睡眠": "20:45 〜 起床", "体温": "36.8℃ (6:15)"}
        self.assertEqual(cf.conflicts({"poop_night": {"state": "普通", "count": 1}}, existing), [])
        self.assertEqual(cf.conflicts({"poop_night": {"state": "軟便"}}, existing), ["排便 夜"])
        self.assertEqual(cf.conflicts({"sleep": {"bed": "20:50", "wake": "6:00"}}, existing), [])
        self.assertEqual(cf.conflicts({"temperature": {"celsius": 36.8, "time": "6:10"}}, existing), [])
        self.assertEqual(cf.conflicts({"temperature": {"celsius": 37.0}}, existing), ["体温"])

    def test_empty_fields(self):
        values = {"排便 夜": "状態 / 0回", "排便 朝": "状態 / 回数", "食事 夜": "カレー", "食事 朝": "",
                  "睡眠": "18:30 〜 起床", "体温": "体温 (測定時間)", "お迎え": "", "コメント": ""}
        self.assertEqual(cf.empty_fields(values), ["排便 夜 状態", "排便 朝 状態", "排便 朝 回数", "食事 朝", "睡眠 起床", "体温", "体温 測定時間", "お迎え", "コメント"])

    def test_mismatches_checks_select_fields(self):
        draft = {"poop_night": {"state": "普通", "count": 1}, "sleep": {"bed": "20:40", "wake": "6:00"}, "temperature": {"celsius": 36.5, "time": "7:10"}}
        same = {"排便 夜": "普通 / 1回", "睡眠": "20:45 〜 6:00", "体温": "36.5℃ (7:15)"}
        self.assertEqual(cf.mismatches(draft, same), [])
        lost = {"排便 夜": "状態 / 回数", "睡眠": "20:45 〜 起床", "体温": "体温 (測定時間)"}
        self.assertEqual(cf.mismatches(draft, lost), ["poop_night", "sleep", "temperature"])

    def test_login_only_drops_notebook_copy(self):
        state = {
            "cookies": [{"name": "CODMONSESSID"}],
            "origins": [{"origin": "https://parents.codmon.com", "localStorage": [
                {"name": "codmon_parent_id", "value": "1"},
                {"name": "codmon_comments_2026-11-09_1", "value": "child"},
                {"name": "codmon_translation", "value": "large"},
            ], "indexedDB": [{"name": "firebase"}]}],
        }
        self.assertEqual(cf.login_only(state), {
            "cookies": [{"name": "CODMONSESSID"}],
            "origins": [{"origin": "https://parents.codmon.com", "localStorage": [{"name": "codmon_parent_id", "value": "1"}]}],
        })

    def test_browser_port_is_stable_per_account(self):
        import os
        from unittest import mock

        import codmon_browser as cb

        with mock.patch.dict(os.environ, {"CODMON_EMAIL": "a@example.com"}):
            first = cb.port()
            self.assertEqual(first, cb.port())
        self.assertTrue(9300 <= first < 9800)


if __name__ == "__main__":
    unittest.main()
