"""AkshareProvider 美债 10 年期收益率（us10y）取数与降级。

背景：index_global_spot_em（东财全球指数现货）端点不稳定，抛 JSONDecodeError 时
原实现因异常被外层 except 吞掉，bond_zh_us_rate 回退形同死代码；且回退选列用
`"10" in col` 命中的是「中国国债收益率10年」而非「美国国债收益率10年」，并用 iloc[-1]
取到美债数据滞后/美股休市造成的 NaN 当日行。本测试钉死三件事：
1) spot_em 抛异常时回退必须被触达；
2) 回退必须取美国列而非中国列；
3) 回退必须跳过末尾 NaN 行，取最近一个有效美债交易日（日期正确）。

全部 mock self.ak.*，不触外网。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    return p


def _bond_df() -> pd.DataFrame:
    """构造 ak.bond_zh_us_rate 形态：中美多档收益率，美债末两行 NaN（滞后/休市）。

    列名与真实接口一致；关键点：「中国国债收益率10年」全程有值（会误命中），
    「美国国债收益率10年」末两行 05-25（美国阵亡将士纪念日休市）/05-26（未发布）为 NaN，
    最近有效美债交易日为 2026-05-22。
    """
    return pd.DataFrame([
        {"日期": "2026-05-21", "中国国债收益率10年": 1.7448,
         "美国国债收益率2年": 4.08, "美国国债收益率10年": 4.57, "美国国债收益率10年-2年": 0.49},
        {"日期": "2026-05-22", "中国国债收益率10年": 1.7519,
         "美国国债收益率2年": 4.13, "美国国债收益率10年": 4.56, "美国国债收益率10年-2年": 0.43},
        {"日期": "2026-05-25", "中国国债收益率10年": 1.7484,
         "美国国债收益率2年": float("nan"), "美国国债收益率10年": float("nan"),
         "美国国债收益率10年-2年": float("nan")},
        {"日期": "2026-05-26", "中国国债收益率10年": 1.7405,
         "美国国债收益率2年": float("nan"), "美国国债收益率10年": float("nan"),
         "美国国债收益率10年-2年": float("nan")},
    ])


class TestUs10yFallbackReachable:
    def test_fallback_runs_when_spot_em_raises(self, ak: AkshareProvider):
        """index_global_spot_em 抛 JSONDecodeError 时，bond_zh_us_rate 回退必须被触达。"""
        ak.ak.index_global_spot_em.side_effect = ValueError(
            "Expecting value: line 1 column 1 (char 0)"
        )
        ak.ak.bond_zh_us_rate.return_value = _bond_df()

        r = ak.get_global_index("us10y")
        assert r.success, "spot_em 抛异常时回退被吞掉了，us10y 不该整体失败"
        assert "bond_zh_us_rate" in r.source


class TestUs10yFallbackPicksUsColumn:
    def test_returns_us_not_china_yield(self, ak: AkshareProvider):
        """回退必须取「美国国债收益率10年」(4.56)，而非首个含'10'的中国列 (1.74)。"""
        ak.ak.index_global_spot_em.side_effect = ValueError("endpoint down")
        ak.ak.bond_zh_us_rate.return_value = _bond_df()

        r = ak.get_global_index("us10y")
        assert r.success
        assert r.data["close"] == pytest.approx(4.56), (
            f"取到 {r.data['close']}，疑似误用中国 10 年期收益率"
        )

    def test_ignores_spread_column(self, ak: AkshareProvider):
        """不得命中「美国国债收益率10年-2年」利差列。"""
        ak.ak.index_global_spot_em.side_effect = ValueError("endpoint down")
        ak.ak.bond_zh_us_rate.return_value = _bond_df()

        r = ak.get_global_index("us10y")
        # 利差列末两行也 NaN，若误选会取到 0.43/None，绝不会是 4.56
        assert r.data["close"] == pytest.approx(4.56)


class TestUs10yFallbackDateCorrect:
    def test_skips_nan_rows_uses_last_valid_us_session(self, ak: AkshareProvider):
        """末两行美债 NaN（休市/滞后），必须取最近有效美债交易日 2026-05-22，而非当日 NaN 行。"""
        ak.ak.index_global_spot_em.side_effect = ValueError("endpoint down")
        ak.ak.bond_zh_us_rate.return_value = _bond_df()

        r = ak.get_global_index("us10y")
        assert r.success
        assert r.data["close"] == pytest.approx(4.56)
        # change_bps 基于 05-22(4.56) 与上一有效日 05-21(4.57)：(4.56-4.57)*100 = -1.0bp
        assert r.data["change_bps"] == pytest.approx(-1.0)
        assert r.data.get("as_of") == "2026-05-22", (
            f"数据日期 {r.data.get('as_of')} 不对，应为最近有效美债交易日"
        )


class TestUs10yPrimaryPathRegression:
    def test_spot_em_primary_still_works(self, ak: AkshareProvider):
        """spot_em 正常且含美债行时，主路径直接返回，不进回退。"""
        ak.ak.index_global_spot_em.return_value = pd.DataFrame([
            {"名称": "美国10年期国债", "最新价": 4.55, "涨跌幅": -0.5},
        ])
        r = ak.get_global_index("us10y")
        assert r.success
        assert r.data["close"] == pytest.approx(4.55)
        assert r.source == "akshare:index_global_spot_em"
        ak.ak.bond_zh_us_rate.assert_not_called()


# ---------------------------------------------------------------------------
# 中债 10Y/30Y（cn10y / cn30y）
#
# 出处：体系课第11课「大类资产观察跟踪大趋势」——中国国债是内部锚定维度。
# 与美债共用 bond_zh_us_rate 宽表，故必须钉死：选列锁国别（不能被美国列串味）、
# 锁期限（10年 vs 30年）、排除利差列、跳过末尾 NaN，以及单次采集只拉一次全表。
# ---------------------------------------------------------------------------


def _cn_bond_df() -> pd.DataFrame:
    """中美同表；中债末行 NaN（未发布），最近有效中债日为 2026-07-23。

    刻意让美债 10 年期(4.71) 与中国 30 年期(2.2045) 同表，用于验证选列不串国别；
    并带「中国国债收益率10年-2年」利差列，验证不被误选。
    """
    return pd.DataFrame([
        {"日期": "2026-07-22", "中国国债收益率10年": 1.7297, "中国国债收益率30年": 2.2120,
         "中国国债收益率10年-2年": 0.4648, "美国国债收益率10年": 4.67},
        {"日期": "2026-07-23", "中国国债收益率10年": 1.7325, "中国国债收益率30年": 2.2045,
         "中国国债收益率10年-2年": 0.4634, "美国国债收益率10年": 4.71},
        {"日期": "2026-07-24", "中国国债收益率10年": float("nan"),
         "中国国债收益率30年": float("nan"), "中国国债收益率10年-2年": float("nan"),
         "美国国债收益率10年": 4.69},
    ])


class TestCnBondYield:
    def test_cn10y_picks_china_column(self, ak: AkshareProvider):
        """cn10y 必须取中国 10 年期(1.7325)，不得串到美国 10 年期(4.71)。"""
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df()

        r = ak.get_global_index("cn10y")
        assert r.success
        assert r.data["close"] == pytest.approx(1.7325), (
            f"取到 {r.data['close']}，疑似误用美债 10 年期"
        )
        assert r.data["name"] == "中国10年期国债收益率"
        assert r.source == "akshare:bond_zh_us_rate"

    def test_cn30y_picks_30y_not_10y(self, ak: AkshareProvider):
        """cn30y 必须取 30 年期(2.2045)，不得落到同国别的 10 年期。"""
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df()

        r = ak.get_global_index("cn30y")
        assert r.success
        assert r.data["close"] == pytest.approx(2.2045)
        assert r.data["name"] == "中国30年期国债收益率"

    def test_cn10y_ignores_spread_column(self, ak: AkshareProvider):
        """不得命中「中国国债收益率10年-2年」利差列(0.4634)。"""
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df()

        r = ak.get_global_index("cn10y")
        assert r.data["close"] != pytest.approx(0.4634)
        assert r.data["close"] == pytest.approx(1.7325)

    def test_cn10y_skips_nan_tail_and_reports_as_of(self, ak: AkshareProvider):
        """末行中债 NaN（未发布），须取最近有效日 07-23 并带 as_of，bp 基于前一有效日。"""
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df()

        r = ak.get_global_index("cn10y")
        assert r.data.get("as_of") == "2026-07-23", (
            f"数据日期 {r.data.get('as_of')} 不对，应为最近有效中债发布日"
        )
        # (1.7325 - 1.7297) * 100 = 0.28bp
        assert r.data["change_bps"] == pytest.approx(0.28)

    def test_all_nan_column_fails_closed(self, ak: AkshareProvider):
        """整列无有效值时必须失败，不得返回 0 或末行 NaN 冒充收益率。"""
        df = _cn_bond_df()
        df["中国国债收益率30年"] = float("nan")
        ak.ak.bond_zh_us_rate.return_value = df

        r = ak.get_global_index("cn30y")
        assert not r.success
        assert r.data is None

    def test_missing_column_fails_closed(self, ak: AkshareProvider):
        """接口改版丢列时必须失败，不得回退到别的期限。"""
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df().drop(
            columns=["中国国债收益率30年"]
        )

        r = ak.get_global_index("cn30y")
        assert not r.success


class TestBondTableCachedAcrossCalls:
    def test_single_fetch_for_us10y_cn10y_cn30y(self, ak: AkshareProvider):
        """一次盘前会连着取三档收益率；宽表接口实测约 30s，必须只拉一次。"""
        ak.ak.index_global_spot_em.side_effect = ValueError("endpoint down")
        ak.ak.bond_zh_us_rate.return_value = _cn_bond_df()

        assert ak.get_global_index("us10y").success
        assert ak.get_global_index("cn10y").success
        assert ak.get_global_index("cn30y").success

        assert ak.ak.bond_zh_us_rate.call_count == 1, (
            f"宽表被拉了 {ak.ak.bond_zh_us_rate.call_count} 次，缓存未生效"
        )
