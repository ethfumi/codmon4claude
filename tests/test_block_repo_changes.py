import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import block_repo_changes as b  # noqa: E402


class BlockRepoChangesTest(unittest.TestCase):
    def bash(self, command):
        return b.is_blocked("Bash", {"command": command})

    def test_blocks_changes(self):
        for c in ('git commit -m "x"', "git push -u origin fix", "cd a && git push", "git -C /tmp/r push origin x",
                  'git "push" origin x', "git 'commit' -m x",
                  "gh pr create --title x", "gh pr merge 3 --merge", "gh api -X PATCH repos/a/b/git/refs/heads/main",
                  "gh api repos/a/b/issues -f title=x", "gh api repos/a/b/contents/x --input f.json",
                  "gh issue create --title x", "gh pr comment 3 --body x"):
            self.assertTrue(self.bash(c), c)

    def test_allows_reading_and_daily_use(self):
        for c in ("git pull --ff-only origin main", "git status", "git log --oneline", "git diff", "git fetch origin",
                  "python3 scripts/codmon_fill.py tmp/draft.json --save", "gh pr view 3", "gh api repos/a/b"):
            self.assertFalse(self.bash(c), c)

    def test_mcp(self):
        self.assertTrue(b.is_blocked("mcp__github__create_pull_request", {}))
        self.assertFalse(b.is_blocked("mcp__github__get_pull_request", {}))
        self.assertFalse(b.is_blocked("Read", {"file_path": "a"}))

    def test_should_stop(self):
        self.assertFalse(b.should_stop("ethfumi"))
        self.assertTrue(b.should_stop("someone-else"))
        # 分からない時も止める
        self.assertTrue(b.should_stop(None))


    def test_secrets_and_leaks(self):
        env = {"CODMON_PASSWORD": "pass-1234", "CODMON_EMAIL": "a@example.com",
               "PIYOLOG_FEED_URL": "https://feed.piyolog.com/v1/feeds/abcdefghijklmnopqrstuvwx.ics"}
        names = {"children": ["山田 たろう"], "facilities": ["さくら保育園"]}
        found = b.secrets(env, names)
        self.assertEqual(b.leaks("朝 パン完食", found), [])
        self.assertEqual(b.leaks("+ comment: たろうは元気", found), ["子どもの名前"])
        self.assertEqual(b.leaks("山田たろう", found), ["子どもの名前"])
        self.assertEqual(b.leaks("さくら保育園に通う", found), ["園の名前"])
        self.assertEqual(b.leaks("保育園", found), [])
        self.assertEqual(b.leaks("token abcdefghijklmnopqrstuvwx", found), ["ぴよログの URL"])
        self.assertEqual(b.leaks("x=pass-1234", found), ["コドモンのパスワード"])
        self.assertEqual(b.leaks("mail a@example.com", found), ["コドモンのメールアドレス"])

    def test_empty_values_find_nothing(self):
        found = b.secrets({"CODMON_PASSWORD": ""}, {})
        self.assertEqual(found, {})
        self.assertEqual(b.leaks("anything", found), [])

    def test_mcp_input_is_checked(self):
        found = b.secrets({}, {"children": ["たろう"]})
        text = b.outgoing("mcp__github__create_issue", {"body": "たろうの記録"}, ".")
        self.assertEqual(b.leaks(text, found), ["子どもの名前"])

    def test_load_names_missing_file(self):
        self.assertEqual(b.load_names(Path("does-not-exist.json")), {})


if __name__ == "__main__":
    unittest.main()
