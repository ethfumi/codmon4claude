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
                  "gh pr create --title x", "gh pr merge 3 --merge", "gh api -X PATCH repos/a/b/git/refs/heads/main"):
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
        self.assertFalse(b.should_stop(b.DEVELOPER_EMAIL, None))
        self.assertTrue(b.should_stop("other@example.com", None))
        self.assertFalse(b.should_stop(None, "ethfumi"))
        self.assertTrue(b.should_stop(None, "someone-else"))
        # 分からない時は止めない
        self.assertFalse(b.should_stop(None, None))


if __name__ == "__main__":
    unittest.main()
