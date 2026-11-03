import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import piyolog_summary as ps  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "feed_sample.json"


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        self.s = ps.summarize(json.loads(FIXTURE.read_text(encoding="utf-8")))

    def test_converts_to_jst(self):
        self.assertEqual(self.s["meals"][0]["time"], "11/02 18:30")
        self.assertEqual(self.s["temperatures"], [{"time": "11/03 06:10", "celsius": 36.8}])

    def test_poop_details_in_japanese(self):
        self.assertEqual(len(self.s["poops"]), 1)
        p = self.s["poops"][0]
        self.assertEqual((p["amount"], p["hardness"], p["color"]), ("ふつう", "やわらかめ", "茶"))

    def test_counts_and_sleep(self):
        self.assertEqual(self.s["pee_count"], 2)
        self.assertEqual([x["event"] for x in self.s["sleep"]], ["寝た", "起きた"])

    def test_condition_and_memo(self):
        self.assertEqual(self.s["conditions"][0]["kind"], "せき")
        self.assertEqual(self.s["memos"][0]["memo"], "替えの靴下を持たせた")

    def test_unknown_type_is_ignored(self):
        text = ps.to_text(self.s)
        self.assertNotIn("Custom3", text)

    def test_empty_records(self):
        s = ps.summarize({"schema_version": 1, "range": {"from": "2026-11-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"}, "records": []})
        text = ps.to_text(s)
        self.assertIn("うんち 0 回", text)
        self.assertIn("記録なし", text)

    def test_rejects_unknown_schema(self):
        with self.assertRaises(SystemExit):
            ps.summarize({"schema_version": 2, "records": []})


if __name__ == "__main__":
    unittest.main()
