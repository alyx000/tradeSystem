from __future__ import annotations

import json
import sqlite3

import pytest

from db import queries as Q
from db.migrate import migrate
from services.daily_leaders import service


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "trade.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    migrate(c)
    yield c
    c.close()


def test_propose_fail_closed_when_active_history_unavailable(tmp_path, monkeypatch):
    def raise_history_error(conn):
        raise RuntimeError("leader_tracking unavailable")

    monkeypatch.setattr(service.Q, "get_active_leaders", raise_history_error)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {
            "step5_leaders": {
                "top_leaders": [
                    {
                        "stock": "海光信息",
                        "sector": "半导体",
                        "attribute_type": "走势引领",
                    }
                ]
            },
            "teacher_notes": [],
        },
        no_llm=True,
        output_root=tmp_path,
    )

    assert proposal["date"] == "2026-07-03"
    assert proposal["top_leaders"][0]["stock"] == "海光信息"
    assert proposal["top_leaders"][0]["is_new"] is True
    assert proposal["paths"]["json"].endswith("2026-07-03.json")


def test_attach_market_quotes_adds_stock_quotes_without_mutating_source():
    prefill = {"market": {"concept_moneyflow_ths": {"data": []}}}

    class FakeRegistry:
        def call(self, name, date):
            assert date == "2026-06-29"
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "688432.SH",
                                "pct_chg": 12.34,
                                "amount": 1880000.0,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [{"ts_code": "688432.SH", "name": "有研硅"}],
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-06-29", FakeRegistry())

    assert "stock_quotes" not in prefill["market"]
    assert out["market"]["stock_quotes"]["data"] == [
        {
            "code": "688432.SH",
            "name": "有研硅",
            "pct_chg": 12.34,
            "amount_yi": 18.8,
        }
    ]


def test_confirm_writes_step5_and_syncs_leader_tracking(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-03",
                "top_leaders": [
                    {
                        "stock": "  海光信息  ",
                        "sector": "  半导体  ",
                        "attribute_type": "走势引领",
                        "attribute": "启动日主动引领",
                        "clarity": "高",
                        "position": "主升初期",
                        "is_new": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    review = Q.get_daily_review(conn, "2026-07-03")
    step5 = json.loads(review["step5_leaders"])
    leaders = Q.get_active_leaders(conn)
    assert result["ok"] is True
    assert result["synced_leader_tracking"] == 1
    assert step5["notes"] == "daily-leaders confirmed by codex"
    assert step5["top_leaders"] == [
        {
            "stock": "海光信息",
            "sector": "半导体",
            "attribute_type": "走势引领",
            "attribute": "启动日主动引领",
            "clarity": "高",
            "position": "主升初期",
            "is_new": True,
        }
    ]
    assert leaders[0]["stock_code"] == "海光信息"
    assert leaders[0]["sector"] == "半导体"


@pytest.mark.parametrize(
    "candidate",
    [
        {"stock": "", "sector": "半导体"},
        {"stock": "   ", "sector": "半导体"},
        {"stock": 688041, "sector": "半导体"},
        {"stock": "海光信息", "sector": ""},
        {"stock": "海光信息", "sector": "   "},
        {"stock": "海光信息", "sector": ["半导体"]},
        "bad-row",
    ],
)
def test_confirm_invalid_candidate_raises_without_writing(conn, tmp_path, candidate):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps({"date": "2026-07-03", "top_leaders": [candidate]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_date_mismatch_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-02",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_invalid_cli_date_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-99-99",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-99-99", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-99-99") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_invalid_source_date_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-99-99",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_requires_top_leaders_list_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps({"date": "2026-07-03", "top_leaders": {"stock": "海光信息"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []
