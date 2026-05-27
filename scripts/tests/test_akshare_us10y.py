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
