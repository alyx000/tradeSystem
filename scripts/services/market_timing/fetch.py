"""指数日线取数路由（market-timing 统一入口）。

为什么需要这层：`registry.call` 按 provider 优先级降级，而 tushare 对未知 code
（如平均股价的伪 code `avg_price`）会返回**空成功**，把后面的 tdx provider 遮蔽掉
（见 tdx_provider 头注释）。所以平均股价必须 `call_specific("tdx", ...)` 直连，
其余标准指数走正常 `registry.call` 降级。scanner 一律经本函数取数，避免在编排层
散落路由判断、也避免漏掉 avg_price 的专路。
"""
from __future__ import annotations

# 平均股价伪 code → 直连 tdx 的哨兵
AVG_PRICE_CODE = "avg_price"


def fetch_index_daily(registry, index_code: str, start_date: str, end_date: str):
    """取指数日线 OHLCV（升序）。返回 DataResult。

    - `avg_price`：`call_specific("tdx", ...)` 绕开 tushare 空成功遮蔽
    - 其余：`registry.call(...)` 按优先级降级
    """
    if index_code == AVG_PRICE_CODE:
        return registry.call_specific("tdx", "get_index_daily_range", index_code, start_date, end_date)
    return registry.call("get_index_daily_range", index_code, start_date, end_date)
