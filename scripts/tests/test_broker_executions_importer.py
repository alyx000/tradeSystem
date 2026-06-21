"""broker_executions importer 单元测试。

覆盖：首次入库 / 幂等 skipped / 冲突检测（老值保留）/ degraded 标记 /
dry-run ROLLBACK / dry-run 仍写报告 / 归档 / source_archive_path 回写 / run_id 同批次共享。
"""
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
    biz_date: str = "20260428",
    exec_time: str = "09:31:15",
    stock_code: str = "002594",
    stock_name: str = "比亚迪",
    direction: str = "买入",
    shares: str = "200",
    price: str = "342.50",
    amount: str = "68500.00",
    contract: str = "C001",
    trade_no: str = "T001",
    commission: str = "13.70",
    other_fees: str = "0.02",
) -> dict[str, str]:
    return {
        "成交日期": biz_date, "成交时间": exec_time,
        "证券代码": stock_code, "证券名称": stock_name, "操作": direction,
        "成交数量": shares, "成交均价": price, "成交金额": amount,
        "股票余额": shares, "合同编号": contract, "成交编号": trade_no,
        "净佣金": commission, "印花税": "0.00", "其他杂费": other_fees,
        "发生金额": f"-{float(amount)+float(commission)+float(other_fees):.2f}",
        "交易市场": "1", "市场名称": "深A",
        "经手费": "0.00", "证管费": "0.00", "过户费": "0.00",
        "真实操作": direction,
    }


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """tmp_path 内 SQLite + 已 migrate。"""
    conn = sqlite3.connect(tmp_path / "test.db")
    init_schema(conn)
    return conn


@pytest.fixture
def fake_source(tmp_path: Path) -> Path:
    """放一个假源文件供 shutil.copy2 归档时使用。"""
    src = tmp_path / "trade_fixture.tsv"
    src.write_bytes(b"placeholder")
    return src


@pytest.fixture
def import_dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "imports", tmp_path / "reports"


def _normalize_one(payload: dict[str, str]) -> list[NormalizedRow]:
    rows = [RawRow(row_index=1, payload=payload)]
    normalized, errs = normalize_rows(
        rows, source_file="trade_fixture.tsv", source_format="tsv-gbk",
    )
    assert not errs, f"unexpected normalization errors: {errs}"
    return normalized


def _run_import(
    conn: sqlite3.Connection,
    payloads: list[dict[str, str]],
    source: Path,
    archive_root: Path,
    report_root: Path,
    *,
    dry_run: bool = False,
) -> ImportReport:
    normalized: list[NormalizedRow] = []
    for i, p in enumerate(payloads, start=1):
        rows = [RawRow(row_index=i, payload=p)]
        n, errs = normalize_rows(
            rows, source_file=str(source), source_format="tsv-gbk",
        )
        assert not errs
        normalized.extend(n)
    return import_executions(
        conn, normalized,
        source_file=str(source), source_format="tsv-gbk",
        input_by="broker_export", account_id="default",
        dry_run=dry_run,
        report_root=report_root, archive_root=archive_root,
    )


def _count_rows(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM broker_executions").fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 首次入库
# ─────────────────────────────────────────────────────────────────────────────
def test_first_import_inserts_all_rows(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
    )
    assert len(report.inserted) == 1
    assert len(report.skipped) == 0
    assert len(report.conflicts) == 0
    assert _count_rows(db_conn) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. 第二次幂等
# ─────────────────────────────────────────────────────────────────────────────
def test_second_import_is_idempotent_skipped(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    _run_import(db_conn, [_make_payload()], fake_source, archive_root, report_root)
    report2 = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
    )
    assert len(report2.inserted) == 0
    assert len(report2.skipped) == 1
    assert len(report2.conflicts) == 0
    assert _count_rows(db_conn) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. 冲突检测：非键字段差异 → conflicts，老值保留
# ─────────────────────────────────────────────────────────────────────────────
def test_conflict_when_commission_differs_old_value_preserved(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    # 首次：commission=13.70
    _run_import(
        db_conn, [_make_payload(commission="13.70")], fake_source,
        archive_root, report_root,
    )
    old_commission = db_conn.execute(
        "SELECT commission FROM broker_executions"
    ).fetchone()[0]
    assert old_commission == pytest.approx(13.70)

    # 第二次：commission=15.40（券商修正），其它键字段完全一致
    report2 = _run_import(
        db_conn, [_make_payload(commission="15.40")], fake_source,
        archive_root, report_root,
    )
    assert len(report2.conflicts) == 1
    assert len(report2.skipped) == 0
    assert len(report2.inserted) == 0
    # 老值不被覆盖
    db_commission = db_conn.execute(
        "SELECT commission FROM broker_executions"
    ).fetchone()[0]
    assert db_commission == pytest.approx(13.70)
    # diff 字段
    diff = report2.conflicts[0].diffs
    assert "commission" in diff
    old, new = diff["commission"]
    assert old == pytest.approx(13.70)
    assert new == pytest.approx(15.40)


# ─────────────────────────────────────────────────────────────────────────────
# 4. degraded 行（双键空）
# ─────────────────────────────────────────────────────────────────────────────
def test_degraded_row_recorded_when_both_dedupe_keys_empty(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    payload = _make_payload(contract="", trade_no="")
    report = _run_import(db_conn, [payload], fake_source, archive_root, report_root)
    assert len(report.inserted) == 1
    assert len(report.degraded) == 1  # 单独计数（不互斥于 inserted）


def test_import_voids_semantic_duplicate_split_and_aggregate_rows(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn,
        [
            _make_payload(
                shares="200", price="10.00", amount="2000.00",
                contract="", trade_no="",
            ),
            _make_payload(
                shares="300", price="10.00", amount="3000.00",
                contract="", trade_no="",
            ),
            _make_payload(
                shares="500", price="10.00", amount="5000.00",
                contract="", trade_no="",
            ),
        ],
        fake_source,
        archive_root,
        report_root,
    )

    rows = db_conn.execute(
        """
        SELECT shares, is_void, void_reason
          FROM broker_executions
         ORDER BY shares
        """
    ).fetchall()

    assert len(report.inserted) == 3
    assert report.voided_execution_rows == 2
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (200, 1, "semantic_duplicate_component"),
        (300, 1, "semantic_duplicate_component"),
        (500, 0, None),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 5. dry-run ROLLBACK
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_run_rolls_back_no_db_changes(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
        dry_run=True,
    )
    assert report.dry_run is True
    assert _count_rows(db_conn) == 0   # ROLLBACK，没行
    # 但 ImportReport 内仍记录"预演 inserted"，让用户能预览
    assert len(report.inserted) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6. dry-run 仍写 markdown 报告
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_run_still_writes_report(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
        dry_run=True,
    )
    assert report.report_path is not None
    assert Path(report.report_path).exists()


# ─────────────────────────────────────────────────────────────────────────────
# 7. 非 dry-run 归档源文件
# ─────────────────────────────────────────────────────────────────────────────
def test_non_dry_run_archives_source_file(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
    )
    assert report.archive_path is not None
    archived = Path(report.archive_path)
    assert archived.exists()
    assert archived.name.endswith("trade_fixture.tsv")
    assert archived.read_bytes() == fake_source.read_bytes()


# ─────────────────────────────────────────────────────────────────────────────
# 8. dry-run 不归档
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_run_does_not_archive(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    report = _run_import(
        db_conn, [_make_payload()], fake_source, archive_root, report_root,
        dry_run=True,
    )
    assert report.archive_path is None
    # archive_root 可能因 report 创建而存在，但其内不应有归档副本
    if archive_root.exists():
        assert not any(archive_root.iterdir())


# ─────────────────────────────────────────────────────────────────────────────
# 9. import_run_id 同批次共享 + source_archive_path 回写
# ─────────────────────────────────────────────────────────────────────────────
def test_import_run_id_shared_and_archive_path_written(
    db_conn: sqlite3.Connection, fake_source: Path, import_dirs: tuple[Path, Path],
) -> None:
    archive_root, report_root = import_dirs
    payloads = [
        _make_payload(stock_code="002594", stock_name="比亚迪",
                      contract="C001", trade_no="T001"),
        _make_payload(stock_code="002594", stock_name="比亚迪", direction="卖出",
                      contract="C002", trade_no="T002"),
    ]
    report = _run_import(
        db_conn, payloads, fake_source, archive_root, report_root,
    )
    rows = db_conn.execute(
        "SELECT import_run_id, source_archive_path FROM broker_executions"
    ).fetchall()
    assert len(rows) == 2
    run_ids = {r[0] for r in rows}
    paths = {r[1] for r in rows}
    assert run_ids == {report.import_run_id}        # 同批次同 run_id
    assert paths == {report.archive_path}            # archive_path 已回写
