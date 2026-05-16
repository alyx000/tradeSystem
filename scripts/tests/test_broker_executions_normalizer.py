"""broker_executions normalizer 单元测试。

覆盖：列名映射、方向归一、日期归一、类型化、费用合计、amount 校验、
双键空 degraded 标识、raw_payload_json 序列化。
"""
from __future__ import annotations

import json

import pytest

from services.broker_executions import normalize_rows
from services.broker_executions.models import NormalizedRow, RawRow


def _payload(
    *,
    biz_date: str = "20260428",
    exec_time: str = "09:31:15",
    stock_code: str = "002594",
    stock_name: str = "比亚迪",
    direction: str = "买入",
    shares: str = "200",
    price: str = "342.50",
    amount: str = "68500.00",
    balance: str = "200",
    contract: str = "C001",
    trade_no: str = "T001",
    commission: str = "13.70",
    stamp: str = "0.00",
    other_fees: str = "0.02",
    net: str = "-68513.72",
    market_raw: str = "1",
    market_name: str = "深A",
    exchange_fee: str = "0.00",
    regulatory_fee: str = "0.00",
    transfer_fee: str = "0.00",
    real_op: str = "买入",
) -> dict[str, str]:
    return {
        "成交日期": biz_date, "成交时间": exec_time,
        "证券代码": stock_code, "证券名称": stock_name, "操作": direction,
        "成交数量": shares, "成交均价": price, "成交金额": amount,
        "股票余额": balance, "合同编号": contract, "成交编号": trade_no,
        "净佣金": commission, "印花税": stamp, "其他杂费": other_fees,
        "发生金额": net, "交易市场": market_raw, "市场名称": market_name,
        "经手费": exchange_fee, "证管费": regulatory_fee, "过户费": transfer_fee,
        "真实操作": real_op,
    }


def _normalize_one(payload: dict[str, str]) -> tuple[list[NormalizedRow], list]:
    rows = [RawRow(row_index=1, payload=payload)]
    return normalize_rows(rows, source_file="t.tsv", source_format="tsv-gbk")


@pytest.mark.parametrize("raw", ["买入", "担保买入", "融资买入", "买"])
def test_buy_direction_variants_normalize_to_buy(raw: str) -> None:
    normalized, errs = _normalize_one(_payload(direction=raw, real_op=raw))
    assert not errs
    assert normalized[0].direction == "buy"
    assert normalized[0].direction_raw == raw


@pytest.mark.parametrize("raw", ["卖出", "担保卖出", "融券卖出", "卖"])
def test_sell_direction_variants_normalize_to_sell(raw: str) -> None:
    normalized, errs = _normalize_one(_payload(direction=raw, real_op=raw))
    assert not errs
    assert normalized[0].direction == "sell"


def test_unknown_direction_yields_error_row() -> None:
    normalized, errs = _normalize_one(_payload(direction="其它"))
    assert not normalized
    assert len(errs) == 1
    assert errs[0].reason == "invalid_direction"


def test_date_yyyymmdd_normalizes_to_iso() -> None:
    normalized, errs = _normalize_one(_payload(biz_date="20260428"))
    assert not errs
    assert normalized[0].biz_date == "2026-04-28"


def test_date_already_iso_kept_as_is() -> None:
    normalized, errs = _normalize_one(_payload(biz_date="2026-04-28"))
    assert not errs
    assert normalized[0].biz_date == "2026-04-28"


def test_total_fees_sums_six_components() -> None:
    normalized, _ = _normalize_one(
        _payload(
            commission="13.70", stamp="0.00", transfer_fee="1.69",
            exchange_fee="0.10", regulatory_fee="0.20", other_fees="0.02",
        )
    )
    n = normalized[0]
    assert n.total_fees == pytest.approx(13.70 + 0.00 + 1.69 + 0.10 + 0.20 + 0.02)


def test_amount_mismatch_yields_error_row() -> None:
    # price * shares = 342.50 * 200 = 68500，但故意把 amount 改成 70000（差 ≥ 容差）
    normalized, errs = _normalize_one(_payload(amount="70000.00"))
    assert not normalized
    assert len(errs) == 1
    assert errs[0].reason == "amount_mismatch"


def test_amount_within_tolerance_passes() -> None:
    # 100 股 * 1685.00 = 168500，容差 max(0.01, 100*0.0005)=0.05；168500.04 在容差内
    normalized, errs = _normalize_one(
        _payload(stock_code="600519", stock_name="贵州茅台", direction="卖出",
                 shares="100", price="1685.00", amount="168500.04",
                 stamp="168.50", transfer_fee="1.69", net="168296.11",
                 market_raw="2", market_name="沪A", real_op="卖出")
    )
    assert not errs
    assert normalized[0].amount == pytest.approx(168500.04)


def test_double_empty_dedupe_keys_marks_degraded() -> None:
    normalized, errs = _normalize_one(_payload(contract="", trade_no=""))
    assert not errs
    n = normalized[0]
    assert n._dedupe_mode == "degraded"
    assert n.broker_contract_no is None
    assert n.broker_trade_no is None


def test_single_dedupe_key_present_stays_strict() -> None:
    normalized, _ = _normalize_one(_payload(contract="C999", trade_no=""))
    assert normalized[0]._dedupe_mode == "strict"

    normalized2, _ = _normalize_one(_payload(contract="", trade_no="T999"))
    assert normalized2[0]._dedupe_mode == "strict"


def test_raw_payload_json_keeps_chinese_and_sorted() -> None:
    payload = _payload()
    normalized, _ = _normalize_one(payload)
    parsed = json.loads(normalized[0].raw_payload_json)
    # 中文不被转义
    assert "比亚迪" in normalized[0].raw_payload_json
    # 所有原始列都保留
    for col in payload:
        assert col in parsed
    # sort_keys=True：序列化后键序应字典序
    keys_in_json = list(parsed.keys())
    assert keys_in_json == sorted(keys_in_json)


def test_stock_code_normalizes_strips_market_suffix() -> None:
    normalized, _ = _normalize_one(_payload(stock_code="600519.SH"))
    assert normalized[0].stock_code == "600519"


def test_market_inferred_from_stock_code() -> None:
    # 6 开头 → SH
    normalized, _ = _normalize_one(
        _payload(stock_code="600519", stock_name="贵州茅台", direction="卖出",
                 shares="100", price="1685.00", amount="168500.00",
                 stamp="168.50", transfer_fee="1.69", net="168296.11",
                 market_raw="2", market_name="沪A", real_op="卖出")
    )
    assert normalized[0].market == "SH"
    # 0/3 开头 → SZ
    normalized2, _ = _normalize_one(_payload(stock_code="002594"))
    assert normalized2[0].market == "SZ"
