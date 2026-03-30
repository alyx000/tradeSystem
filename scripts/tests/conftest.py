"""保证从仓库根目录或 scripts/ 运行 pytest 时均能 import collectors、generators 等包。"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 手动推送联调脚本，非标准单测（会 return bool）；请直接 `python scripts/tests/test_pushers.py`
collect_ignore = ["test_pushers.py"]
