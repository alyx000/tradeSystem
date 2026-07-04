"""串阳首阴：主线融合判断。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from db.migrate import migrate
from services.volume_concentration import repo as vc_repo


class _Registry:
    def call(self, name: str, *args):
        if name == "get_ths_member":
            return SimpleNamespace(success=True, data=[
                {"con_code": "600010.SH", "index_name": "CPO"},
                {"con_code": "600011.SH", "index_name": "CPO"},
                {"con_code": "600012.SH", "index_name": "机器人"},
            ])
        if name == "get_concept_moneyflow_ths":
            return SimpleNamespace(success=True, data=[
                {"name": "CPO", "net_amount": 9_000_000_000},
                {"name": "机器人", "net_amount": 4_000_000_000},
                {"name": "未知概念", "net_amount": 3_000_000_000},
            ])
        raise AssertionError(f"unexpected provider call: {name}")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    vc_repo.save_concentration(conn, {
        "date": "2026-06-09",
        "top_n": 20,
        "total_amount_billion": 1000,
        "sector_summary": [
            {"industry": "半导体", "amount_billion": 150, "amount_share": 0.15},
            {"industry": "银行", "amount_billion": 80, "amount_share": 0.08},
        ],
        "stocks": [],
        "source": {"provider": "pytest"},
    })
    conn.execute(
        """INSERT INTO teacher_notes
           (date, title, source_type, input_by, core_view, sectors, raw_content)
           VALUES (?, ?, 'text', 'pytest', ?, ?, ?)""",
        (
            "2026-06-09",
            "老师盘后",
            "今天主线在算力链，CPO 分支比泛科技更集中。",
            '["算力","CPO"]',
            "CPO 成交额集中，机器人只能看轮动。",
        ),
    )
    return conn


def test_judge_mainline_uses_llm_but_only_accepts_provided_sectors_and_concepts() -> None:
    from services.string_yang import mainline

    def runner(prompt, payload):
        assert "老师观点" in prompt
        assert payload["volume_sectors"][0]["industry"] == "半导体"
        assert payload["teacher_notes"][0]["title"] == "老师盘后"
        return {
            "main_l2": ["半导体", "不存在行业"],
            "main_concepts": ["CPO", "不存在概念"],
            "watch_only": ["机器人"],
            "evidence": ["半导体成交额居前", "老师强调 CPO 分支"],
            "confidence": 0.82,
        }

    judgment = mainline.judge_mainline(
        _conn(),
        _Registry(),
        "2026-06-09",
        top_k=2,
        top_concepts=3,
        teacher_lookback_days=7,
        use_llm=True,
        llm_runner=runner,
    )

    assert judgment.main_sectors == ["半导体"]
    assert judgment.main_concepts == ["CPO"]
    assert judgment.watch_only == ["机器人"]
    assert judgment.status == "llm"
    assert judgment.confidence == 0.82
    assert "600010" in judgment.stock_concept_map


def test_judge_mainline_falls_back_to_volume_sectors_when_llm_returns_no_valid_mainline() -> None:
    from services.string_yang import mainline

    judgment = mainline.judge_mainline(
        _conn(),
        _Registry(),
        "2026-06-09",
        top_k=1,
        top_concepts=3,
        teacher_lookback_days=7,
        use_llm=True,
        llm_runner=lambda _prompt, _payload: {"main_l2": ["不存在行业"], "main_concepts": []},
    )

    assert judgment.status == "llm_fallback"
    assert judgment.main_sectors == ["半导体"]
    assert judgment.main_concepts == []
    assert "llm_mainline_empty" in judgment.source_errors
