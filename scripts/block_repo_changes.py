"""開発者以外のアカウントの会話で、リポジトリを変更する操作を止める。

Claude Code の PreToolUse フックから呼ばれる。連絡帳を使うだけの会話がコミットや PR を作らないようにする。
クラウドセッションでは Claude のアカウントのメールで、手元では GitHub のユーザー名で開発者かどうかを見る。
開発者と違う時だけ止める。どちらも分からない時は止めない。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request

DEVELOPER = "ethfumi"
DEVELOPER_EMAIL = "fumilin01@gmail.com"
BASH_PATTERN = re.compile(r"\bgit\s+(?:-\S+\s+(?:\S+\s+)?)*(commit|push|merge|rebase|tag)\b|\bgh\s+(pr|repo|release)\s+(create|merge|edit|delete|close)\b|\bgh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)")
MCP_PATTERN = re.compile(r"github.*(create|update|merge|push|delete|fork)", re.IGNORECASE)
MESSAGE = "このリポジトリを変更する操作は、開発者の会話でしかできません。変更はせず、気づいたことを相手に短く伝えて、連絡帳を続けてください。"


def is_blocked(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Bash":
        return bool(BASH_PATTERN.search(tool_input.get("command", "")))
    return bool(MCP_PATTERN.search(tool_name))


def github_login() -> str | None:
    """今の会話の GitHub のユーザー名を返す。分からなければ None。"""
    try:
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"User-Agent": "codmon4claude"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request("https://api.github.com/user", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.load(res).get("login")
    except (OSError, ValueError):
        return None


def should_stop(email: str | None, login: str | None) -> bool:
    if email:
        return email != DEVELOPER_EMAIL
    return login is not None and login != DEVELOPER


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if not is_blocked(data.get("tool_name", ""), data.get("tool_input") or {}):
        return
    email = os.environ.get("CLAUDE_CODE_USER_EMAIL")
    if should_stop(email, None if email else github_login()):
        sys.stderr.reconfigure(encoding="utf-8")
        print(MESSAGE, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
