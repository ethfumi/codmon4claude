"""手元で動かす時に、リポジトリの .env を環境変数として読み込む。

クラウドセッションでは環境の設定から環境変数が入るので、.env は無くてよい。
すでに環境変数に値がある時は、そちらを優先する。
worktree の中で動かした時は、本体の checkout の .env も探す。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _candidates() -> list[Path]:
    paths = [REPO_ROOT / ".env"]
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if common:
            paths.append(Path(common).parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return paths


def load() -> None:
    for path in _candidates():
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if value and not os.environ.get(key):
                os.environ[key] = value
        return
