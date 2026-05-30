"""AkshareProvider.get_macro_indicators 单元测试。

全程 mock self.ak.macro_china_*，不触真实网络。覆盖：
  - 5 个指标全部成功：结构 {period_col, latest, trend, name, source}
  - periods 截断：取末尾 N 期（时间升序），latest=最后一行
  - 单指标失败优雅降级：该指标记 error，其余正常，整体 success
  - 全部失败：返回 error 且 data=None
  - 未初始化：返回 error
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from providers.akshare_provider import AkshareProvider


def _pmi_df(rows: int = 8) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "月份": [f"2025{m:02d}" for m in range(1, rows + 1)],
            "制造业-指数": [49.0 + i * 0.1 for i in range(rows)],
            "制造业-同比增长": [-0.5 + i * 0.05 for i in range(rows)],
        }
    )


def _simple_df(col_value: str, rows: int = 8) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "月份": [f"2025{m:02d}" for m in range(1, rows + 1)],
            col_value: [float(i) for i in range(rows)],
        }
    )


def _make_provider(funcs: dict) -> AkshareProvider:
    prov = AkshareProvider()
    ak = MagicMock()
    for name, ret in funcs.items():
        getattr(ak, name).return_value = ret
    prov.ak = ak
    return prov


def _all_ok_funcs() -> dict:
    return {
        "macro_china_pmi": _pmi_df(),
        "macro_china_cpi": _simple_df("全国-当月", 8),
        "macro_china_money_supply": _simple_df("货币和准货币(M2)-数量(亿元)", 8),
        "macro_china_lpr": _simple_df("LPR1Y", 8),
        "macro_china_shrzgm": _simple_df("社会融资规模增量", 8),
    }


class TestGetMacroIndicators:
    def test_all_indicators_success(self):
        prov = _make_provider(_all_ok_funcs())
        r = prov.get_macro_indicators(periods=6)
        assert r.success
        assert r.data is not None
        assert set(r.data.keys()) == {"pmi", "cpi", "m2", "lpr", "shrzgm"}
        pmi = r.data["pmi"]
        assert pmi["period_col"] == "月份"
        assert pmi["name"]
        assert pmi["source"] == "macro_china_pmi"
        assert len(pmi["trend"]) == 6
        assert pmi["latest"] == pmi["trend"][-1]
        # period 键补充正确（取首列值）
        assert pmi["latest"]["period"] == pmi["latest"]["月份"]

    def test_periods_cap_and_ascending(self):
        prov = _make_provider(_all_ok_funcs())
        r = prov.get_macro_indicators(periods=6)
        trend = r.data["pmi"]["trend"]
        assert len(trend) == 6
        # 末尾 6 期、时间升序（首列 202503..202508）
        assert trend[0]["月份"] == "202503"
        assert trend[-1]["月份"] == "202508"

    def test_periods_smaller_than_default(self):
        prov = _make_provider(_all_ok_funcs())
        r = prov.get_macro_indicators(periods=3)
        assert len(r.data["pmi"]["trend"]) == 3

    def test_descending_akshare_order_uses_latest_periods(self):
        """akshare 部分接口最新在前（降序）：须按周期排序后取最近 N 期，latest 为最新月。"""
        desc = pd.DataFrame(
            {
                "月份": ["202508", "202507", "202506", "202505", "202504", "202503", "202502", "202501"],
                "制造业-指数": [49.7, 49.6, 49.5, 49.4, 49.3, 49.2, 49.1, 49.0],
            }
        )
        funcs = _all_ok_funcs()
        funcs["macro_china_pmi"] = desc
        prov = _make_provider(funcs)
        r = prov.get_macro_indicators(periods=6)
        trend = r.data["pmi"]["trend"]
        assert len(trend) == 6
        # 升序、最新一期在末尾
        assert trend[0]["月份"] == "202503"
        assert trend[-1]["月份"] == "202508"
        assert r.data["pmi"]["latest"]["月份"] == "202508"
        assert r.data["pmi"]["latest"]["制造业-指数"] == 49.7

    def test_real_format_non_padded_chinese_month_sorted(self):
        """非零填充中文月份（2024年1月 vs 2024年10月）须正确归一排序，latest 取真最新。

        模拟 akshare eastmoney 降序返回；归一键 2024年9月→202409、2024年10月→202410 等长可排。
        """
        desc_cn = pd.DataFrame(
            {
                "月份": ["2024年10月", "2024年9月", "2024年8月", "2024年7月", "2024年6月", "2024年5月", "2024年1月"],
                "制造业-指数": [50.1, 49.8, 49.5, 49.4, 49.0, 48.5, 48.0],
            }
        )
        funcs = _all_ok_funcs()
        funcs["macro_china_pmi"] = desc_cn
        prov = _make_provider(funcs)
        r = prov.get_macro_indicators(periods=6)
        trend = r.data["pmi"]["trend"]
        assert r.data["pmi"]["latest"]["月份"] == "2024年10月"
        assert r.data["pmi"]["latest"]["制造业-指数"] == 50.1
        # 取最近 6 期，最旧的 2024年1月被裁掉，升序
        assert trend[0]["月份"] == "2024年5月"
        assert trend[-1]["月份"] == "2024年10月"

    def test_real_format_iso_datetime_period_sorted(self):
        """完整 datetime 周期（eastmoney TIME 形态）降序返回须正确取最新。"""
        desc_dt = pd.DataFrame(
            {
                "TIME": [
                    "2024-05-01 00:00:00",
                    "2024-04-01 00:00:00",
                    "2024-03-01 00:00:00",
                ],
                "LPR1Y": [3.45, 3.45, 3.50],
            }
        )
        funcs = _all_ok_funcs()
        funcs["macro_china_lpr"] = desc_dt
        prov = _make_provider(funcs)
        r = prov.get_macro_indicators(periods=6)
        latest = r.data["lpr"]["latest"]
        assert latest["TIME"] == "2024-05-01 00:00:00"
        assert latest["LPR1Y"] == 3.45

    def test_single_indicator_failure_degrades(self):
        funcs = _all_ok_funcs()
        prov = _make_provider(funcs)
        prov.ak.macro_china_lpr.side_effect = RuntimeError("lpr backend down")
        r = prov.get_macro_indicators(periods=6)
        assert r.success  # 其余 4 个成功
        assert "error" in r.data["lpr"]
        assert "lpr backend down" in r.data["lpr"]["error"]
        assert "trend" in r.data["pmi"]  # 其余正常
        assert r.note  # note 标注部分失败

    def test_empty_dataframe_marks_error_for_that_indicator(self):
        funcs = _all_ok_funcs()
        funcs["macro_china_cpi"] = pd.DataFrame()
        prov = _make_provider(funcs)
        r = prov.get_macro_indicators(periods=6)
        assert r.success
        assert r.data["cpi"]["error"] == "无数据"

    def test_all_fail_returns_error(self):
        prov = _make_provider({})
        for name in (
            "macro_china_pmi",
            "macro_china_cpi",
            "macro_china_money_supply",
            "macro_china_lpr",
            "macro_china_shrzgm",
        ):
            getattr(prov.ak, name).side_effect = RuntimeError("boom")
        r = prov.get_macro_indicators(periods=6)
        assert not r.success
        assert r.data is None
        assert "所有宏观指标获取失败" in r.error

    def test_not_initialized_returns_error(self):
        prov = AkshareProvider()  # ak 未注入
        r = prov.get_macro_indicators()
        assert not r.success
        assert "未初始化" in r.error

    def test_capabilities_includes_macro_indicators(self):
        prov = AkshareProvider()
        assert "get_macro_indicators" in prov.get_capabilities()

    def test_nan_cell_normalized_to_none(self):
        df = pd.DataFrame({"月份": ["202508"], "值": [float("nan")]})
        funcs = _all_ok_funcs()
        funcs["macro_china_pmi"] = df
        prov = _make_provider(funcs)
        r = prov.get_macro_indicators(periods=6)
        assert r.data["pmi"]["latest"]["值"] is None
