import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import codmon_fill as cf  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
