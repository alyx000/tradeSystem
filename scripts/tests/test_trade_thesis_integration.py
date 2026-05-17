"""阶段 4 集成测试:trade_thesis + executions import 联动."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.schema import init_schema
from services.broker_executions import (
    ImportReport,
    NormalizedRow,
    import_executions,
    normalize_rows,
)
from services.broker_executions.models import RawRow


def _make_payload(
    *,
    biz_date: str = "20260514",
    exec_time: str = "09:31:15",
    stock_code: str = "002663",
    stock_name: str = "测试票",
    direction: str = "买入",
    shares: str = "1000",
    price: str = "6.50",
    amount: str | None = None,
    contract: str = "C001",
    trade_no: str = "T001",
) -> dict[str, str]:
    if amount is None:
        amount = f"{int(shares) * float(price):.2f}"
    fee = 13.70
    return {
        "成交日期": biz_date, "成交时间": exec_time,
        "证券代码": stock_code, "证券名称": stock_name, "操作": direction,
        "成交数量": shares, "成交均价": price, "成交金额": amount,
        "股票余额": shares if direction == "买入" else "0",
        "合同编号": contract, "成交编号": trade_no,
        "净佣金": f"{fee:.2f}", "印花税": "0.00", "其他杂费": "0.02",
        "发生金额": f"-{float(amount)+fee+0.02:.2f}",
        "交易市场": "1", "市场名称": "深A",
        "经手费": "0.00", "证管费": "0.00", "过户费": "0.00",
        "真实操作": direction,
    }


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "integration.db")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


@pytest.fixture
def fake_source(tmp_path: Path) -> Path:
    src = tmp_path / "broker_fixture.tsv"
    src.write_bytes(b"placeholder")
    return src


@pytest.fixture
def import_dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "imports", tmp_path / "reports"


def _normalize(payloads: list[dict[str, str]], source: Path) -> list[NormalizedRow]:
    out: list[NormalizedRow] = []
    for i, p in enumerate(payloads, start=1):
        rows = [RawRow(row_index=i, payload=p)]
        n, errs = normalize_rows(
            rows, source_file=str(source), source_format="tsv-gbk",
        )
        assert not errs, f"normalization failed: {errs}"
        out.extend(n)
    return out


def _create_thesis_for(
    conn, *, code, account="default", entry_reason="主线", trade_mode="break",
):
    from services.trade_thesis import Thesis, repository

    return repository.create(conn, Thesis(
        stock_code=code, stock_name="测试票",
        account_id=account, opened_at="2026-05-14",
        entry_reason=entry_reason, failure_condition="尾盘破板",
        trade_mode=trade_mode, market_region="a-share",
        sector="测试板块", planned_position_pct=0.15, input_by="alyx",
    ))


# ──────────────────────────────────────────────────────────────
# I1: dry-run 思路触发段 + 可执行命令模板
# ──────────────────────────────────────────────────────────────

class TestImportDryRunThesisTriggers:
    def test_import_dry_run_appends_thesis_trigger_section_with_command_template(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        normalized = _normalize([_make_payload(stock_code="002663")], fake_source)

        report = import_executions(
            db_conn, normalized,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=True,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True,
        )

        assert hasattr(report, "thesis_triggers"), "ImportReport 应有 thesis_triggers 字段"
        opens = [t for t in report.thesis_triggers if t["action"] == "open"]
        assert len(opens) == 1
        assert opens[0]["stock_code"] == "002663"
        assert "db thesis-open" in opens[0]["command_template"]
        assert "--code 002663" in opens[0]["command_template"]


# ──────────────────────────────────────────────────────────────
# I2 + I3: 严格模式 reject 无 thesis 的 buy + --allow-orphan-buy 降级
# ──────────────────────────────────────────────────────────────

class TestImportStrictMode:
    def test_import_rejects_new_buy_without_existing_thesis(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        normalized = _normalize([_make_payload(stock_code="002663")], fake_source)

        report = import_executions(
            db_conn, normalized,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, allow_orphan_buy=False,
        )

        # 整批 reject:broker_executions 表应有 0 行
        cnt = db_conn.execute("SELECT COUNT(*) FROM broker_executions").fetchone()[0]
        assert cnt == 0
        # 错误信息应给出命令模板
        assert any("thesis-open" in str(e.reason) for e in report.errors), (
            f"errors 应包含 thesis-open 命令模板,实际: {[e.reason for e in report.errors]}"
        )

    def test_import_allow_orphan_buy_flag_writes_null_thesis_id(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        normalized = _normalize([_make_payload(stock_code="002663")], fake_source)

        import_executions(
            db_conn, normalized,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, allow_orphan_buy=True,
        )

        row = db_conn.execute(
            "SELECT thesis_id FROM broker_executions WHERE stock_code = '002663'"
        ).fetchone()
        assert row is not None
        assert row["thesis_id"] is None


# ──────────────────────────────────────────────────────────────
# I4: 已有 open thesis 时 buy 自动关联 thesis_id(plan R2)
# ──────────────────────────────────────────────────────────────

class TestImportAttachToExistingThesis:
    def test_import_groups_buy_into_existing_open_thesis(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        normalized = _normalize([_make_payload(stock_code="002663")], fake_source)

        import_executions(
            db_conn, normalized,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True,
        )

        row = db_conn.execute(
            "SELECT thesis_id FROM broker_executions WHERE stock_code = '002663'"
        ).fetchone()
        assert row is not None
        assert row["thesis_id"] == thesis_id


# ──────────────────────────────────────────────────────────────
# I5 + I6: auto-close 同批 sell 归零 + notes 追加 + --no-auto-close 反转
# ──────────────────────────────────────────────────────────────

class TestImportAutoClose:
    def test_auto_close_when_sell_to_zero_appends_notes(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        # 先 import buy
        normalized_buy = _normalize(
            [_make_payload(stock_code="002663", direction="买入", trade_no="TB1")],
            fake_source,
        )
        import_executions(
            db_conn, normalized_buy,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )

        # 再 import sell 1000(让 holdings 归零)
        normalized_sell = _normalize(
            [_make_payload(
                stock_code="002663", direction="卖出", trade_no="TS1",
                biz_date="20260520",
            )],
            fake_source,
        )
        import_executions(
            db_conn, normalized_sell,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )

        row = db_conn.execute(
            "SELECT status, notes FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        assert row["status"] == "closed"
        assert "[auto-close 2026-05-20]" in (row["notes"] or "")
        assert "holdings 归零自动关闭" in (row["notes"] or "")

    def test_no_auto_close_flag_disables_auto_close(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        normalized_buy = _normalize(
            [_make_payload(stock_code="002663", direction="买入", trade_no="TB1")],
            fake_source,
        )
        import_executions(
            db_conn, normalized_buy,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=False,
        )
        normalized_sell = _normalize(
            [_make_payload(
                stock_code="002663", direction="卖出", trade_no="TS1",
                biz_date="20260520",
            )],
            fake_source,
        )
        import_executions(
            db_conn, normalized_sell,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=False,
        )

        row = db_conn.execute(
            "SELECT status FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        # auto_close=False → 仍 open
        assert row["status"] == "open"


# ──────────────────────────────────────────────────────────────
# I7: 跨批次累计 holdings — 第二批 import 触发 close 判定
# ──────────────────────────────────────────────────────────────

class TestImportPartialSellBatches:
    """单批多笔 sell 分批减仓到同一 thesis,累计归零才 close(subagent review 高 1)."""

    def test_auto_close_is_idempotent_on_already_closed_thesis(
        self, db_conn, fake_source, import_dirs,
    ):
        """codex review 中等 2:对已 closed 的 thesis 再触发 auto_close 不应追加 notes."""
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        # 第一次 buy+sell 让 thesis auto-closed
        for direction, trade_no, biz_date in (
            ("买入", "TB1", "20260514"),
            ("卖出", "TS1", "20260520"),
        ):
            normalized = _normalize(
                [_make_payload(stock_code="002663", direction=direction,
                              trade_no=trade_no, biz_date=biz_date)],
                fake_source,
            )
            import_executions(
                db_conn, normalized,
                source_file=str(fake_source), source_format="tsv-gbk",
                input_by="broker_export", account_id="default",
                dry_run=False, report_root=report_root, archive_root=archive_root,
                enforce_strict_thesis=True, allow_orphan_buy=True, auto_close=True,
            )

        notes_after_first_close = db_conn.execute(
            "SELECT notes FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()["notes"]

        # 再 import 一笔无关股票(让 auto_close 跑但本 thesis 已 closed)
        normalized = _normalize(
            [_make_payload(stock_code="002663", direction="卖出",
                          trade_no="TS_NOOP", biz_date="20260521",
                          shares="0", price="6.50")],
            fake_source,
        )
        # 注意:--allow-orphan-buy 让 thesis_id=NULL 行也可写,触发 auto_close 复扫
        import_executions(
            db_conn, normalized,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False, report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, allow_orphan_buy=True, auto_close=True,
        )

        notes_after_noop = db_conn.execute(
            "SELECT notes FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()["notes"]
        # 已 closed thesis 的 notes 不应被再次追加 [auto-close ...]
        assert notes_after_first_close == notes_after_noop, (
            "auto_close 应对已 closed thesis 幂等,不重复追加 notes"
        )

    def test_two_sells_in_one_batch_close_only_when_balance_zero(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        # buy 1000 一次
        normalized_buy = _normalize(
            [_make_payload(stock_code="002663", direction="买入",
                           shares="1000", trade_no="TB1")],
            fake_source,
        )
        import_executions(
            db_conn, normalized_buy,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )

        # 单批同时 sell 500 + 500(必须不同 trade_no 才不会被 dedupe)
        normalized_sells = _normalize(
            [
                _make_payload(stock_code="002663", direction="卖出",
                              shares="500", trade_no="TS1", biz_date="20260520"),
                _make_payload(stock_code="002663", direction="卖出",
                              shares="500", trade_no="TS2", biz_date="20260520"),
            ],
            fake_source,
        )
        import_executions(
            db_conn, normalized_sells,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )

        row = db_conn.execute(
            "SELECT status FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        # 两笔 sell 累计 1000 = buy 1000 → 应 auto-close
        assert row["status"] == "closed"


class TestImportCrossBatch:
    def test_cross_batch_holdings_invariant_close_in_second_import(
        self, db_conn, fake_source, import_dirs,
    ):
        archive_root, report_root = import_dirs
        thesis_id = _create_thesis_for(db_conn, code="002663", account="default")

        # T+0:只 import buy 1000
        normalized_buy = _normalize(
            [_make_payload(stock_code="002663", direction="买入",
                          shares="1000", trade_no="TB_X")],
            fake_source,
        )
        import_executions(
            db_conn, normalized_buy,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )
        # 此时 thesis 仍 open
        row1 = db_conn.execute(
            "SELECT status FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        assert row1["status"] == "open"

        # T+3:第二批含 sell 1000(累计归零)
        normalized_sell = _normalize(
            [_make_payload(stock_code="002663", direction="卖出",
                          shares="1000", trade_no="TS_X", biz_date="20260520")],
            fake_source,
        )
        import_executions(
            db_conn, normalized_sell,
            source_file=str(fake_source), source_format="tsv-gbk",
            input_by="broker_export", account_id="default",
            dry_run=False,
            report_root=report_root, archive_root=archive_root,
            enforce_strict_thesis=True, auto_close=True,
        )
        row2 = db_conn.execute(
            "SELECT status FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        assert row2["status"] == "closed"
