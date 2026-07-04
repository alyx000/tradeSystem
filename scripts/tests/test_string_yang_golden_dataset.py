"""串阳首阴黄金数据集护栏。

这些样本是用户确认过的黄金数据，未获用户明确允许不得修改。
"""
from __future__ import annotations

import json
from pathlib import Path


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "string_yang" / "golden_2026_05_06.json"

EXPECTED_GOLDEN = {
    "name": "string_yang_golden_2026_05_06",
    "locked": True,
    "change_policy": "Do not modify without explicit user approval.",
    "date": "2026-05-06",
    "items": [
        {
            "code": "605111",
            "name": "新洁能",
            "today_pct_chg": 1.62,
            "amount_ratio_vs_prev5_max": 1.24,
            "price_ma_ratio": 1.00,
        },
        {
            "code": "688785",
            "name": "恒运昌",
            "today_pct_chg": -0.00,
            "amount_ratio_vs_prev5_max": 1.00,
            "price_ma_ratio": 1.02,
        },
    ],
}


def test_string_yang_golden_2026_05_06_is_locked() -> None:
    actual = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert actual == EXPECTED_GOLDEN
