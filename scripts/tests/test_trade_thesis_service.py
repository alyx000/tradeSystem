"""trade_thesis service 层测试 — 阶段 2 TDD 微循环 S1-S16."""
from __future__ import annotations

from dataclasses import fields

import pytest

from db.connection import get_connection
from db.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test_thesis_svc.db")
    migrate(c)
    yield c
    c.close()


# ──────────────────────────────────────────────────────────────
# S1: models.Thesis dataclass 字段必须对齐 trade_thesis schema
# ──────────────────────────────────────────────────────────────

class TestThesisDataclass:
    def test_thesis_dataclass_fields_match_schema(self):
        from services.trade_thesis.models import Thesis

        field_names = {f.name for f in fields(Thesis)}
        # 来自 plan precious-crunching-ocean.md trade_thesis 表
        expected = {
            "id", "stock_code", "stock_name", "account_id",
            "opened_at", "closed_at", "status",
            "entry_reason", "failure_condition",
            "target_price", "stop_loss",
            "trade_mode", "mode_note", "market_region",
            "sector", "planned_position_pct",
            "plan_id", "notes",
            "created_at", "updated_at", "input_by",
            "reopen_count", "last_reopened_at",
        }
        missing = expected - field_names
        assert not missing, f"Thesis dataclass 缺字段: {missing}"


# ──────────────────────────────────────────────────────────────
# S2: validators.TRADE_MODES 枚举(plan U3:8 个 mode 含 gap_jump)
# ──────────────────────────────────────────────────────────────

class TestTradeModeValidators:
    def test_trade_modes_contains_all_eight_enum_values(self):
        from services.trade_thesis.validators import TRADE_MODES

        assert set(TRADE_MODES) == {
            "break", "dip", "trend", "scalp",
            "swing", "arbitrage", "gap_jump", "other",
        }

    def test_validate_trade_mode_accepts_valid_value(self):
        from services.trade_thesis.validators import validate_trade_mode

        # 不抛即可
        validate_trade_mode("break")
        validate_trade_mode("gap_jump")

    def test_validate_trade_mode_rejects_invalid_value(self):
        from services.trade_thesis.validators import validate_trade_mode

        with pytest.raises(ValueError, match="trade_mode"):
            validate_trade_mode("invalid_mode")


def _make_thesis(**overrides):
    """构造 Thesis 用例的最小工厂."""
    from services.trade_thesis.models import Thesis

    base = dict(
        stock_code="600519",
        stock_name="贵州茅台",
        account_id="A001",
        opened_at="2026-05-01",
        entry_reason="主线龙头反包",
        failure_condition="尾盘破板",
        trade_mode="break",
        market_region="a-share",
        sector="白酒",
        planned_position_pct=0.15,
        input_by="alyx",
    )
    base.update(overrides)
    return Thesis(**base)


# ──────────────────────────────────────────────────────────────
# S3-S6: repository CRUD — create / find_open / close
# ──────────────────────────────────────────────────────────────

class TestRepositoryCreateAndFind:
    def test_create_thesis_returns_id(self, conn):
        from services.trade_thesis import repository

        thesis = _make_thesis()
        new_id = repository.create(conn, thesis)
        assert isinstance(new_id, int) and new_id > 0

        # DB 中能查到对应行
        row = conn.execute(
            "SELECT stock_code, status, entry_reason FROM trade_thesis WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert row["stock_code"] == "600519"
        assert row["status"] == "open"
        assert row["entry_reason"] == "主线龙头反包"

    def test_find_open_thesis_by_account_stock(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        found = repository.find_open_thesis(conn, account_id="A001", stock_code="600519")
        assert found is not None
        assert found.id == new_id
        assert found.status == "open"

    def test_find_open_thesis_returns_none_when_not_open(self, conn):
        from services.trade_thesis import repository

        # 没有任何 thesis 时
        assert repository.find_open_thesis(conn, account_id="A001", stock_code="600519") is None


class TestRepositoryAccountIsolation:
    """S5: 账户隔离不变式 G — 跨账户的同票 open thesis 互不干扰."""

    def test_find_open_thesis_account_isolation_same_stock_diff_account(self, conn):
        from services.trade_thesis import repository

        a_id = repository.create(conn, _make_thesis(account_id="A001"))
        b_id = repository.create(conn, _make_thesis(account_id="A002"))

        a = repository.find_open_thesis(conn, account_id="A001", stock_code="600519")
        b = repository.find_open_thesis(conn, account_id="A002", stock_code="600519")
        assert a is not None and b is not None
        assert a.id == a_id
        assert b.id == b_id
        assert a.id != b.id


class TestRepositoryClose:
    def test_close_sets_status_and_closed_at(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")

        row = conn.execute(
            "SELECT status, closed_at FROM trade_thesis WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert row["status"] == "closed"
        assert row["closed_at"] == "2026-05-10"

    def test_close_nonexistent_thesis_raises(self, conn):
        from services.trade_thesis import repository

        with pytest.raises(LookupError, match="thesis"):
            repository.close(conn, thesis_id=9999, closed_at="2026-05-10", input_by="alyx")


# ──────────────────────────────────────────────────────────────
# S7-S8: fill — closed 时拒主字段、允许 notes(plan 行为契约 A)
# ──────────────────────────────────────────────────────────────

class TestRepositoryFillClosedSemantics:
    def test_fill_on_closed_thesis_raises_with_friendly_msg(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")

        # 主字段:status='closed' 时禁止修改
        with pytest.raises(ValueError, match="closed.*frozen|reopen"):
            repository.fill(conn, thesis_id=new_id, entry_reason="改主线")

        with pytest.raises(ValueError, match="closed.*frozen|reopen"):
            repository.fill(conn, thesis_id=new_id, trade_mode="dip")

    def test_fill_notes_on_closed_thesis_ok(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")

        # notes / mode_note / target_price / stop_loss / plan_id 是可选字段,允许修改
        repository.fill(conn, thesis_id=new_id, notes="补充复盘备注")

        row = conn.execute(
            "SELECT notes FROM trade_thesis WHERE id = ?", (new_id,)
        ).fetchone()
        assert row["notes"] == "补充复盘备注"

    def test_fill_on_open_thesis_allows_main_fields(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        repository.fill(conn, thesis_id=new_id, entry_reason="修订主线", trade_mode="dip")

        row = conn.execute(
            "SELECT entry_reason, trade_mode FROM trade_thesis WHERE id = ?", (new_id,)
        ).fetchone()
        assert row["entry_reason"] == "修订主线"
        assert row["trade_mode"] == "dip"


# ──────────────────────────────────────────────────────────────
# S9: reopen — count++,notes 追加 [reopen DATE] reason(plan C)
# ──────────────────────────────────────────────────────────────

class TestRepositoryReopen:
    def test_reopen_increments_count_appends_to_notes(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis(notes="初始备注"))
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")

        # 第一次 reopen
        repository.reopen(
            conn, thesis_id=new_id, reason="发现新逻辑", input_by="alyx",
            reopened_at="2026-05-11",
        )

        row = conn.execute(
            "SELECT status, reopen_count, last_reopened_at, notes FROM trade_thesis WHERE id = ?",
            (new_id,),
        ).fetchone()
        assert row["status"] == "open"
        assert row["reopen_count"] == 1
        assert row["last_reopened_at"] == "2026-05-11"
        assert "[reopen 2026-05-11]" in row["notes"]
        assert "发现新逻辑" in row["notes"]
        # 初始 notes 保留
        assert "初始备注" in row["notes"]

        # 第二次 reopen:count=2,notes 累积追加
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-15", input_by="alyx")
        repository.reopen(
            conn, thesis_id=new_id, reason="再次重开", input_by="alyx",
            reopened_at="2026-05-16",
        )
        row2 = conn.execute(
            "SELECT reopen_count, notes FROM trade_thesis WHERE id = ?", (new_id,)
        ).fetchone()
        assert row2["reopen_count"] == 2
        assert "[reopen 2026-05-11]" in row2["notes"]
        assert "[reopen 2026-05-16]" in row2["notes"]
        assert "再次重开" in row2["notes"]

    def test_reopen_open_thesis_raises(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        # 已 open,reopen 应拒绝
        with pytest.raises(ValueError, match="open"):
            repository.reopen(
                conn, thesis_id=new_id, reason="x", input_by="alyx",
                reopened_at="2026-05-11",
            )

    def test_second_reopen_updates_last_reopened_at_to_latest(self, conn):
        """codex review 中等 2:连续两次 reopen,last_reopened_at 应是最新一次."""
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        # 第一次 close-reopen
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")
        repository.reopen(
            conn, thesis_id=new_id, reason="r1", reopened_at="2026-05-11", input_by="alyx",
        )
        # 第二次 close-reopen
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-15", input_by="alyx")
        repository.reopen(
            conn, thesis_id=new_id, reason="r2", reopened_at="2026-05-16", input_by="alyx",
        )
        row = conn.execute(
            "SELECT reopen_count, last_reopened_at, closed_at FROM trade_thesis WHERE id = ?",
            (new_id,),
        ).fetchone()
        # last_reopened_at 是最新一次,旧的不留
        assert row["last_reopened_at"] == "2026-05-16"
        assert row["reopen_count"] == 2
        # reopen 后 closed_at 应重置为 NULL(下次 close 重新写)
        assert row["closed_at"] is None


# ──────────────────────────────────────────────────────────────
# S10: thesis_review upsert(plan 行为契约 B)
# ──────────────────────────────────────────────────────────────

class TestRepositoryReviewUpsert:
    def test_thesis_review_upsert_allows_multiple_updates(self, conn):
        from services.trade_thesis import repository

        new_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=new_id, closed_at="2026-05-10", input_by="alyx")

        # 第一次:只填 executed_as_planned
        repository.review_upsert(
            conn, thesis_id=new_id, executed_as_planned=1, input_by="alyx",
        )
        row1 = conn.execute(
            "SELECT executed_as_planned, lessons, discipline_score FROM thesis_review WHERE thesis_id = ?",
            (new_id,),
        ).fetchone()
        assert row1["executed_as_planned"] == 1
        assert row1["lessons"] is None
        assert row1["discipline_score"] is None

        # 第二次:增量补 lessons + discipline_score(不传 executed_as_planned 时保留原值)
        repository.review_upsert(
            conn, thesis_id=new_id, lessons="按计划执行,纪律分到位",
            discipline_score=5, input_by="alyx",
        )
        row2 = conn.execute(
            "SELECT executed_as_planned, lessons, discipline_score FROM thesis_review WHERE thesis_id = ?",
            (new_id,),
        ).fetchone()
        assert row2["executed_as_planned"] == 1, "upsert 不应覆盖未传字段"
        assert row2["lessons"] == "按计划执行,纪律分到位"
        assert row2["discipline_score"] == 5

        # 仍只有一行 thesis_review(upsert 而非 insert)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM thesis_review WHERE thesis_id = ?", (new_id,)
        ).fetchone()[0]
        assert cnt == 1


# ──────────────────────────────────────────────────────────────
# S11-S14, S16: lifecycle 建议器 — 半自动判定
# ──────────────────────────────────────────────────────────────


def _seed_broker_execution(
    conn, *, account_id, stock_code, direction, shares, price, biz_date,
    thesis_id=None,
):
    """种子一行 broker_executions(测试 fixture 辅助)."""
    conn.execute(
        """
        INSERT INTO broker_executions (
            account_id, biz_date, stock_code_raw, stock_code, stock_name,
            market, direction, direction_raw, shares, price, amount,
            raw_payload_json, source_file, source_format, input_by, thesis_id
        ) VALUES (?, ?, ?, ?, ?, 'A股', ?, ?, ?, ?, ?, '{}', 'test.tsv', 'tsv', 'alyx', ?)
        """,
        (
            account_id, biz_date, stock_code, stock_code, "测试票",
            direction, direction,
            shares, price, shares * price,
            thesis_id,
        ),
    )
    conn.commit()


def _seed_holding(conn, *, stock_code, shares, status="active"):
    conn.execute(
        """
        INSERT INTO holdings (stock_code, stock_name, shares, status)
        VALUES (?, ?, ?, ?)
        """,
        (stock_code, "测试票", shares, status),
    )
    conn.commit()


class TestLifecycleSuggestOpen:
    """S11 + S14: suggest_open_for_executions."""

    def test_lifecycle_suggest_open_for_first_buy(self, conn):
        from services.trade_thesis import lifecycle

        # 一笔新 buy(没有 open thesis,没有 holdings)
        executions = [
            {"account_id": "A001", "stock_code": "600519", "direction": "buy",
             "shares": 100, "biz_date": "2026-05-15"},
        ]
        suggestions = lifecycle.suggest_open_for_executions(conn, executions)
        # 至少 1 条 open 建议
        opens = [s for s in suggestions if s["action"] == "open"]
        assert len(opens) == 1
        assert opens[0]["stock_code"] == "600519"
        assert opens[0]["account_id"] == "A001"
        assert "thesis-open" in opens[0]["command_template"]
        assert "--code 600519" in opens[0]["command_template"]
        assert "--account A001" in opens[0]["command_template"]

    def test_buy_with_existing_open_thesis_no_new_thesis(self, conn):
        from services.trade_thesis import lifecycle, repository

        # 已有 open thesis
        repository.create(conn, _make_thesis(account_id="A001", stock_code="600519"))

        # 加仓 buy
        executions = [
            {"account_id": "A001", "stock_code": "600519", "direction": "buy",
             "shares": 100, "biz_date": "2026-05-16"},
        ]
        suggestions = lifecycle.suggest_open_for_executions(conn, executions)
        # 不应建议 open(已存在);可建议 attach 到现有 thesis
        opens = [s for s in suggestions if s["action"] == "open"]
        assert len(opens) == 0
        attach = [s for s in suggestions if s["action"] == "attach"]
        assert len(attach) == 1
        assert attach[0]["stock_code"] == "600519"


class TestLifecycleSuggestClose:
    """S12 + S16: suggest_close_for_holdings."""

    def test_lifecycle_suggest_close_when_holdings_zero(self, conn):
        from services.trade_thesis import lifecycle, repository

        # 创建 open thesis + holdings>0
        thesis_id = repository.create(
            conn, _make_thesis(account_id="A001", stock_code="600519")
        )
        # 同步 holdings.thesis_id
        _seed_holding(conn, stock_code="600519", shares=1000)
        conn.execute(
            "UPDATE holdings SET thesis_id = ? WHERE stock_code = ?",
            (thesis_id, "600519"),
        )
        conn.commit()

        # 卖光的事件(导致 holdings 归零)
        close_events = [
            {"account_id": "A001", "stock_code": "600519",
             "new_balance": 0, "biz_date": "2026-05-20"},
        ]
        suggestions = lifecycle.suggest_close_for_holdings(conn, close_events)
        closes = [s for s in suggestions if s["action"] == "close"]
        assert len(closes) == 1
        assert closes[0]["thesis_id"] == thesis_id

    def test_suggest_close_when_holdings_zero_but_no_open_thesis_historical_orphan(self, conn):
        """S16: 历史持仓清仓,无 thesis 关联 → 提示历史孤儿,不报关 thesis."""
        from services.trade_thesis import lifecycle

        # holdings>0 但没有任何 thesis(plan R5 / U2 历史孤儿场景)
        _seed_holding(conn, stock_code="600519", shares=1000)

        close_events = [
            {"account_id": "A001", "stock_code": "600519",
             "new_balance": 0, "biz_date": "2026-05-20"},
        ]
        suggestions = lifecycle.suggest_close_for_holdings(conn, close_events)
        # 不应建议 close thesis,而是标识为"历史孤儿"
        closes = [s for s in suggestions if s["action"] == "close"]
        assert len(closes) == 0
        orphans = [s for s in suggestions if s["action"] == "historical_orphan"]
        assert len(orphans) == 1
        assert orphans[0]["stock_code"] == "600519"


class TestLifecycleSuggestReview:
    """S13: closed thesis 缺 thesis_review → 建议补 review."""

    def test_lifecycle_suggest_review_for_closed_without_review(self, conn):
        from services.trade_thesis import lifecycle, repository

        # 关闭一个 thesis,但不写 review
        thesis_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=thesis_id, closed_at="2026-05-20", input_by="alyx")

        suggestions = lifecycle.suggest_review(conn)
        assert any(s["thesis_id"] == thesis_id for s in suggestions)

    def test_suggest_review_excludes_already_reviewed(self, conn):
        from services.trade_thesis import lifecycle, repository

        thesis_id = repository.create(conn, _make_thesis())
        repository.close(conn, thesis_id=thesis_id, closed_at="2026-05-20", input_by="alyx")
        repository.review_upsert(
            conn, thesis_id=thesis_id, executed_as_planned=1, input_by="alyx",
        )

        suggestions = lifecycle.suggest_review(conn)
        assert not any(s["thesis_id"] == thesis_id for s in suggestions)


# ──────────────────────────────────────────────────────────────
# S15: backfill_pnl — 从 broker_executions 派生 PnL
# ──────────────────────────────────────────────────────────────

class TestRepositoryBackfillPnl:
    def test_backfill_pnl_from_executions(self, conn):
        from services.trade_thesis import repository

        thesis_id = repository.create(conn, _make_thesis(opened_at="2026-05-01"))
        # buy 1000 @ 100,sell 1000 @ 110(忽略手续费)
        _seed_broker_execution(
            conn, account_id="A001", stock_code="600519",
            direction="buy", shares=1000, price=100.0,
            biz_date="2026-05-01", thesis_id=thesis_id,
        )
        _seed_broker_execution(
            conn, account_id="A001", stock_code="600519",
            direction="sell", shares=1000, price=110.0,
            biz_date="2026-05-10", thesis_id=thesis_id,
        )
        repository.close(conn, thesis_id=thesis_id, closed_at="2026-05-10", input_by="alyx")

        pnl = repository.backfill_pnl(conn, thesis_id=thesis_id)
        assert pnl["realized_pnl_amount"] == pytest.approx(10000.0)
        assert pnl["realized_pnl_pct"] == pytest.approx(0.10, abs=1e-6)
        assert pnl["holding_days"] == 9


# ──────────────────────────────────────────────────────────────
# C8-C12: CLI 行为细节(直接调 _cmd_* 函数,monkeypatch DB path)
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def cli_args_factory(tmp_path, monkeypatch):
    """构造 argparse.Namespace 工厂 + 切换 DB 到 tmp 路径."""
    import argparse as _argparse

    db_path = tmp_path / "test_cli_thesis.db"
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)

    # 预初始化 DB
    from db.connection import get_connection
    from db.migrate import migrate as _migrate
    c = get_connection(db_path)
    _migrate(c)
    c.close()

    def make(**kwargs):
        return _argparse.Namespace(**kwargs)

    return make, str(db_path)


class TestCLIThesisListFilter:
    """C8-C10: thesis-list 三个过滤路径."""

    def test_thesis_list_without_review_excludes_reviewed(self, cli_args_factory, capsys):
        from db.cli import _cmd_thesis_open, _cmd_thesis_close, _cmd_thesis_review, _cmd_thesis_list

        make, _ = cli_args_factory
        # 开 2 个 + close 2 个,只对其中 1 个写 review
        for code in ("600519", "300750"):
            _cmd_thesis_open(make(
                code=code, name="t", account="A001", opened_at="2026-05-01",
                entry_reason="r", trade_mode="break", failure_condition="f",
                planned_position_pct=0.1, sector="x", market_region="a-share",
                input_by="alyx", target_price=None, stop_loss=None,
                mode_note=None, notes=None, plan_id=None,
            ))
        _cmd_thesis_close(make(id=1, closed_at="2026-05-10", input_by="alyx"))
        _cmd_thesis_close(make(id=2, closed_at="2026-05-10", input_by="alyx"))
        _cmd_thesis_review(make(
            id=1, executed_as_planned=1, exit_trigger=None, lessons=None,
            discipline_score=None, input_by="alyx",
        ))
        capsys.readouterr()  # 清缓存

        _cmd_thesis_list(make(
            status=None, account=None, code=None, date_from=None, date_to=None,
            filter_name=None, without_review=True, reopened=False, json=False,
        ))
        out = capsys.readouterr().out
        # 只剩 #2(无 review),#1 已被 reviewed 应排除
        assert "#2" in out
        assert "#1 [closed]" not in out

    def test_thesis_list_reopened_warns_on_count_gt_3(self, cli_args_factory, capsys):
        from db.cli import _cmd_thesis_open, _cmd_thesis_close, _cmd_thesis_reopen, _cmd_thesis_list

        make, _ = cli_args_factory
        _cmd_thesis_open(make(
            code="600519", name="t", account="A001", opened_at="2026-05-01",
            entry_reason="r", trade_mode="break", failure_condition="f",
            planned_position_pct=0.1, sector="x", market_region="a-share",
            input_by="alyx", target_price=None, stop_loss=None,
            mode_note=None, notes=None, plan_id=None,
        ))
        # 反复 close-reopen 4 次,reopen_count=4
        for i in range(4):
            _cmd_thesis_close(make(id=1, closed_at=f"2026-05-{10+i:02d}", input_by="alyx"))
            _cmd_thesis_reopen(make(
                id=1, reason=f"r{i}", reopened_at=f"2026-05-{11+i:02d}", input_by="alyx",
            ))
        capsys.readouterr()

        _cmd_thesis_list(make(
            status=None, account=None, code=None, date_from=None, date_to=None,
            filter_name=None, without_review=False, reopened=True, json=False,
        ))
        out = capsys.readouterr().out
        assert "#1" in out
        assert "reopen=4" in out
        assert "⚠️" in out


class TestCLIThesisSuggest:
    """C11: thesis-suggest 三段输出."""

    def test_thesis_suggest_outputs_three_categories(self, cli_args_factory, capsys):
        from db.cli import _cmd_thesis_open, _cmd_thesis_close, _cmd_thesis_suggest

        make, _ = cli_args_factory
        # 制造一个 closed 但无 review 的 thesis(进入"待 review"段)
        _cmd_thesis_open(make(
            code="600519", name="t", account="A001", opened_at="2026-05-01",
            entry_reason="r", trade_mode="break", failure_condition="f",
            planned_position_pct=0.1, sector="x", market_region="a-share",
            input_by="alyx", target_price=None, stop_loss=None,
            mode_note=None, notes=None, plan_id=None,
        ))
        _cmd_thesis_close(make(id=1, closed_at="2026-05-10", input_by="alyx"))
        capsys.readouterr()

        _cmd_thesis_suggest(make(account=None))
        out = capsys.readouterr().out
        # 三段标题都必须出现
        assert "待 open" in out
        assert "待 close" in out
        assert "待 review" in out
        # closed 但无 review 的 thesis 出现在第三段
        assert "thesis #1" in out


class TestCLIThesisFillClosedError:
    """C12: thesis-fill closed 时主字段被冻结 → 友好错误."""

    def test_thesis_fill_closed_returns_actionable_error(self, cli_args_factory, capsys):
        from db.cli import _cmd_thesis_open, _cmd_thesis_close, _cmd_thesis_fill

        make, _ = cli_args_factory
        _cmd_thesis_open(make(
            code="600519", name="t", account="A001", opened_at="2026-05-01",
            entry_reason="r", trade_mode="break", failure_condition="f",
            planned_position_pct=0.1, sector="x", market_region="a-share",
            input_by="alyx", target_price=None, stop_loss=None,
            mode_note=None, notes=None, plan_id=None,
        ))
        _cmd_thesis_close(make(id=1, closed_at="2026-05-10", input_by="alyx"))
        capsys.readouterr()

        _cmd_thesis_fill(make(
            id=1, entry_reason="改主线", failure_condition=None,
            target_price=None, stop_loss=None,
            trade_mode=None, mode_note=None, planned_position_pct=None,
            sector=None, market_region=None, notes=None, plan_id=None,
        ))
        out = capsys.readouterr().out
        # service 友好错误必须含 closed/frozen/reopen 任一关键词,让用户知道下一步动作
        assert "❌" in out
        assert any(kw in out for kw in ("closed", "frozen", "reopen")), (
            f"友好错误应提示 closed/frozen/reopen,实际输出: {out!r}"
        )
