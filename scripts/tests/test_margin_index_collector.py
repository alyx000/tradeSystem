"""margin_index_correlation.collector：唯一 IO 入口 build_record 的隔离测试。

mock registry.call('get_margin_series') + pro.index_daily，无网络。验证 record 结构完整、
data_trade_date 取两融真实末日、数据不足返 None。相关数值正确性由 aggregator 单测保证。
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from services.margin_index_correlation import collector


# 共享交易日（两融与指数必须同日历才能对齐）
_DATES = pd.date_range("2026-05-01", periods=30, freq="B").strftime("%Y%m%d").tolist()


def _margin_series():
    out = []
    for i, d in enumerate(_DATES):
        iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        out.append({
            "trade_date": iso,
            "total_rzye_yi": 17000.0 + i * 5,
            "total_rqye_yi": 200.0,
            "total_rzrqye_yi": 17200.0 + i * 5,
            "sse_rzrqye_yi": 9000.0 + i * 3,
            "szse_rzrqye_yi": 8200.0 + i * 2,
            "bse_rzrqye_yi": 50.0,
            "market_scope": "BSE+SSE+SZSE",
        })
    return out


def _index_df(slope):
    # pct_chg 随 i 线性变化，与两融余额增量正相关
    rows = []
    for i, d in enumerate(_DATES):
        rows.append({"trade_date": d, "pct_chg": slope * ((i % 5) - 2)})
    return pd.DataFrame(rows)


def _fake_pro():
    pro = SimpleNamespace()
    pro.index_daily = lambda ts_code, start_date, end_date: _index_df(1.0)
    return pro


def _fake_registry(success=True):
    def _call(method, *args, **kwargs):
        if method == "get_margin_series" and success:
            return SimpleNamespace(success=True, data=_margin_series(), source="tushare:margin", error=None)
        return SimpleNamespace(success=False, data=None, source="registry", error="所有数据源均失败")
    return SimpleNamespace(call=_call)


_KW = dict(
    windows=[5, 20],
    broad_indices=[("000001.SH", "上证指数"), ("399006.SZ", "创业板指")],
    cross_pairs=[("sse", "沪市两融", "000001.SH", "上证指数"),
                 ("szse", "深市两融", "399001.SZ", "深证成指")],
    base_index="000001.SH",
    divergence_windows=[5],
    min_gap=0.5,
    max_lag=3,
    min_sample_by_window={5: 5, 20: 15},
    lag_min_sample=10,
)


def test_build_record_full_structure():
    rec = collector.build_record(_fake_registry(), _fake_pro(), date="2026-06-11", **_KW)
    assert rec is not None
    assert rec["date"] == "2026-06-11"
    # data_trade_date = 两融序列真实末日（可 < date）
    assert rec["data_trade_date"] == _margin_series()[-1]["trade_date"]
    # 四维都在
    assert rec["lag"] and rec["sync_corr"] and rec["divergence"] and rec["balance"]
    assert "risk_alert" in rec
    assert rec["risk_alert"]["level"] in {"none", "low", "medium", "high", "unevaluated"}
    # 水位三口径
    assert set(rec["balance"]) == {"total", "sse", "szse"}
    assert rec["balance"]["total"]["latest_yi"] > 0
    # pairing 元数据：broad(total) + cross(sse/szse)
    keys = set(rec["lag"])
    assert "total:000001.SH" in keys
    assert "sse:000001.SH" in keys
    assert "szse:399001.SZ" in keys
    # meta
    assert "tushare:margin" in rec["meta"]["source"]
    assert rec["meta"]["market_scope"] == "BSE+SSE+SZSE"


def test_margin_fail_returns_none():
    rec = collector.build_record(_fake_registry(success=False), _fake_pro(), date="2026-06-11", **_KW)
    assert rec is None


def test_all_index_fail_returns_none():
    pro = SimpleNamespace()

    def _raise(ts_code, start_date, end_date):
        raise RuntimeError("index unreachable")

    pro.index_daily = _raise
    rec = collector.build_record(_fake_registry(), pro, date="2026-06-11", **_KW)
    assert rec is None


def test_stale_flag_when_data_date_before_request():
    rec = collector.build_record(_fake_registry(), _fake_pro(), date="2026-06-30", **_KW)
    # 请求 06-30 但两融末日 < 06-30 → stale=True
    assert rec["meta"]["stale"] is True


def test_index_truncated_to_margin_spine_on_stale():
    """T-1 脊柱回归（codex round2 #1）：两融止于 T-1、指数含 T，请求日为 T。

    指数必须截断到两融真实末日，否则背离窗口以无两融配对的 T 结尾 → 误报「日期缺口」。
    断言：背离按 T-1 窗口真算出（非「日期缺口」），meta.analysis_trade_date = T-1。
    """
    import numpy as np

    rng = np.random.RandomState(11)
    rets = rng.uniform(-1.5, 1.5, len(_DATES))
    bal = 17200.0 * np.cumprod(1 + rets / 100.0)
    t_minus_1_iso = f"{_DATES[-2][:4]}-{_DATES[-2][4:6]}-{_DATES[-2][6:8]}"
    t_iso = f"{_DATES[-1][:4]}-{_DATES[-1][4:6]}-{_DATES[-1][6:8]}"

    def _margin_to_t_minus_1():  # 两融止于 T-1（缺最后一天）
        out = []
        for i, d in enumerate(_DATES[:-1]):
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            out.append({"trade_date": iso, "total_rzye_yi": bal[i], "total_rqye_yi": 200.0,
                        "total_rzrqye_yi": bal[i], "sse_rzrqye_yi": bal[i] * 0.52,
                        "szse_rzrqye_yi": bal[i] * 0.48, "bse_rzrqye_yi": 50.0,
                        "market_scope": "BSE+SSE+SZSE"})
        return out

    registry = SimpleNamespace(call=lambda m, *a, **k: SimpleNamespace(
        success=True, data=_margin_to_t_minus_1(), source="tushare:margin", error=None))
    pro = SimpleNamespace()
    pro.index_daily = lambda ts_code, start_date, end_date: pd.DataFrame(  # 指数含 T（全 _DATES）
        [{"trade_date": d, "pct_chg": rets[i]} for i, d in enumerate(_DATES)])

    rec = collector.build_record(registry, pro, date=t_iso, **_KW)
    assert rec["meta"]["analysis_trade_date"] == t_minus_1_iso
    assert rec["meta"]["stale"] is True
    # 背离按 T-1 窗口真算出，而非因 T 日无两融配对误报「日期缺口」
    assert rec["divergence"]["total:000001.SH"]["5"]["type"] != "日期缺口"


def test_correlation_actually_computes_aligned_dates():
    """对齐回归：两融(YYYY-MM-DD)与指数(YYYYMMDD)日期格式必须归一，否则零交集→全样本不足。

    构造指数涨跌幅 = 两融日变化率（强正相关），断言 sync_corr 真算出数值（非 None）、
    lag 非「样本不足」。**这条用例锁死 critical 日期对齐 bug：若格式不归一，corr 恒 None。**
    """
    import numpy as np

    rng = np.random.RandomState(7)
    rets = rng.uniform(-1.5, 1.5, len(_DATES))  # 已知日涨跌幅序列
    # 两融合计余额 = 按 rets 复利累积（→ 其 pct_change ≈ rets）
    bal = 17200.0 * np.cumprod(1 + rets / 100.0)

    def _margin():
        out = []
        for i, d in enumerate(_DATES):
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            out.append({"trade_date": iso, "total_rzye_yi": bal[i], "total_rqye_yi": 200.0,
                        "total_rzrqye_yi": bal[i], "sse_rzrqye_yi": bal[i] * 0.52,
                        "szse_rzrqye_yi": bal[i] * 0.48, "bse_rzrqye_yi": 50.0,
                        "market_scope": "BSE+SSE+SZSE"})
        return out

    registry = SimpleNamespace(call=lambda m, *a, **k: SimpleNamespace(
        success=True, data=_margin(), source="tushare:margin", error=None))
    pro = SimpleNamespace()
    pro.index_daily = lambda ts_code, start_date, end_date: pd.DataFrame(
        [{"trade_date": d, "pct_chg": rets[i]} for i, d in enumerate(_DATES)])

    rec = collector.build_record(registry, pro, date="2026-06-11", **_KW)
    sync = rec["sync_corr"]["total:000001.SH"]["20"]  # window 键统一 str（fresh==persisted）
    assert sync["corr"] is not None  # 对齐成功 → 相关真算出来
    assert sync["corr"] > 0.9        # 构造的强正相关被捕捉
    assert rec["lag"]["total:000001.SH"]["relation"] != "样本不足"
