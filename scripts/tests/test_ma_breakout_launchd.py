from __future__ import annotations

import plistlib
from pathlib import Path


def _ma_breakout_intervals() -> list[dict]:
    # 锚定 __file__：make check-scripts 从 scripts/ 目录跑 pytest，相对 cwd 路径会解析失败
    repo_root = Path(__file__).resolve().parents[2]
    plist_path = repo_root / "deploy/launchd/com.alyx.tradesystem.ma-breakout.plist"
    with plist_path.open("rb") as fh:
        data = plistlib.load(fh)
    return data["StartCalendarInterval"]


def test_ma_breakout_plist_includes_shanghai_evening_trigger():
    intervals = _ma_breakout_intervals()
    actual = {(item["Weekday"], item["Hour"], item["Minute"]) for item in intervals}

    for weekday in range(0, 6):
        assert (weekday, 21, 35) in actual
