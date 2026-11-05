"""開発者以外の会話で、リポジトリを変更する操作を止める。

Claude Code の PreToolUse フックから呼ばれる。連絡帳を使うだけの会話がコミットや PR を作らないようにする。
開発者は環境変数 CODMON_DEV=1 を入れておく（手元では .env、クラウドでは環境の設定）。
"""

from __future__ import annotations

import json
import os
import re
import sys

import local_env

BASH_PATTERN = re.compile(r"\bgit\s+(?:-\S+\s+(?:\S+\s+)?)*(commit|push|merge|rebase|tag)\b|\bgh\s+(pr|repo|release)\s+(create|merge|edit|delete|close)\b|\bgh\s+api\b.*(-X|--method)\s*(POST|PUT|PATCH|DELETE)")
MCP_PATTERN = re.compile(r"github.*(create|update|merge|push|delete|fork)", re.IGNORECASE)
MESSAGE = "このリポジトリを変更する操作は、開発者の会話でしかできません。変更はせず、気づいたことを相手に短く伝えて、連絡帳を続けてください。"


def is_blocked(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Bash":
        return bool(BASH_PATTERN.search(tool_input.get("command", "")))
    return bool(MCP_PATTERN.search(tool_name))


def main() -> None:
    local_env.load()
    if os.environ.get("CODMON_DEV") == "1":
        return
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if is_blocked(data.get("tool_name", ""), data.get("tool_input") or {}):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        print(MESSAGE, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
