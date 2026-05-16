"""broker_executions parser 单元测试。

覆盖：编码探测、格式分发、剔除空行/合计行/重复表头、payload 完整性。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.broker_executions import parse_file
from services.broker_executions.parser import detect_format

_HEADER = [
    "成交日期", "成交时间", "证券代码", "证券名称", "操作",
    "成交数量", "成交均价", "成交金额", "股票余额", "合同编号",
    "成交编号", "净佣金", "印花税", "其他杂费", "发生金额",
    "交易市场", "市场名称", "经手费", "证管费", "过户费", "真实操作",
]

_BODY_ROWS = [
    ["20260428", "09:31:15", "002594", "比亚迪", "买入", "200", "342.50",
     "68500.00", "200", "C001", "T001", "13.70", "0.00", "0.02", "-68513.72",
     "1", "深A", "0.00", "0.00", "0.00", "买入"],
    ["20260505", "13:42:08", "600519", "贵州茅台", "卖出", "100", "1685.00",
     "168500.00", "0", "C002", "T002", "33.70", "168.50", "1.69", "168296.11",
     "2", "沪A", "0.00", "0.00", "1.69", "卖出"],
    ["20260506", "10:00:00", "300750", "宁德时代", "担保买入", "300", "258.80",
     "77640.00", "300", "C003", "T003", "15.53", "0.00", "0.02", "-77655.55",
     "1", "深A", "0.00", "0.00", "0.00", "担保买入"],
    ["20260507", "14:30:00", "300750", "宁德时代", "担保卖出", "300", "260.00",
     "78000.00", "0", "C004", "T004", "15.60", "78.00", "0.78", "77905.62",
     "1", "深A", "0.00", "0.00", "0.78", "担保卖出"],
    ["20260508", "09:30:00", "000001", "平安银行", "买入", "500", "12.00",
     "6000.00", "500", "", "", "1.20", "0.00", "0.00", "-6001.20",
     "1", "深A", "0.00", "0.00", "0.00", "买入"],
]

_SUMMARY_ROW = ["", "合计", "", "", "", "", "", "10000.00",
                "", "", "", "", "", "", "", "", "", "", "", "", ""]


def _build_tsv(lines: list[list[str]]) -> str:
    return "\n".join("\t".join(row) for row in lines)


@pytest.fixture
def broker_tsv_gbk(tmp_path: Path) -> Path:
    """5 笔成交 + 1 合计行 + 1 空行，GBK 编码。"""
    rows: list[list[str]] = [_HEADER]
    rows.extend(_BODY_ROWS)
    rows.append(_SUMMARY_ROW)
    text = _build_tsv(rows) + "\n\n"  # 末尾空行
    path = tmp_path / "broker_export_min.tsv"
    path.write_bytes(text.encode("gbk"))
    return path


def test_parse_returns_five_rows_excluding_summary_and_empty(
    broker_tsv_gbk: Path,
) -> None:
    rows, meta = parse_file(broker_tsv_gbk)
    assert meta["source_format"] == "tsv-gbk"
    assert len(rows) == 5


def test_parse_preserves_actual_action_column(broker_tsv_gbk: Path) -> None:
    rows, _ = parse_file(broker_tsv_gbk)
    payload = rows[0].payload
    assert "真实操作" in payload
    assert payload["真实操作"] == "买入"


def test_parse_preserves_all_business_columns(broker_tsv_gbk: Path) -> None:
    rows, _ = parse_file(broker_tsv_gbk)
    payload = rows[0].payload
    for col in _HEADER:
        assert col in payload, f"列 {col!r} 未保留在 raw payload 中"


def test_detect_format_tsv_gbk(broker_tsv_gbk: Path) -> None:
    assert detect_format(broker_tsv_gbk) == "tsv-gbk"


def test_detect_format_prefers_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "utf8_bom.tsv"
    path.write_bytes(b"\xef\xbb\xbf" + "col1\tcol2\nval1\tval2".encode("utf-8"))

    assert "utf" in detect_format(path)


def test_detect_format_xlsx_raises(tmp_path: Path) -> None:
    fake_xlsx = tmp_path / "fake.xls"
    fake_xlsx.write_bytes(b"PK\x03\x04something")
    assert detect_format(fake_xlsx) == "xlsx"
    with pytest.raises(NotImplementedError):
        parse_file(fake_xlsx)


def test_detect_format_html_table_raises(tmp_path: Path) -> None:
    fake_html = tmp_path / "fake.html"
    fake_html.write_text("<html><body><table>...</table></body></html>", encoding="utf-8")
    assert detect_format(fake_html) == "html-table"
    with pytest.raises(NotImplementedError):
        parse_file(fake_html)


def test_parse_skips_repeated_header_in_body(tmp_path: Path) -> None:
    rows_with_dup_header: list[list[str]] = [_HEADER, _BODY_ROWS[0], _HEADER, _BODY_ROWS[1]]
    text = _build_tsv(rows_with_dup_header)
    path = tmp_path / "dup_header.tsv"
    path.write_bytes(text.encode("gbk"))

    rows, _ = parse_file(path)
    # 表头出现两次，第二次应被剔除；预期得到 2 行有效数据
    assert len(rows) == 2
    assert rows[0].payload["证券代码"] == "002594"
    assert rows[1].payload["证券代码"] == "600519"
