#!/usr/bin/env python3
"""
探测本机 Tushare 各接口是否可调用（与 TushareProvider 相同的 base URL 与 token）。

用法（在 scripts 目录）:
  python3 diagnose_tushare.py
  TUSHARE_HTTP_URL=https://api.tushare.pro python3 diagnose_tushare.py

环境变量:
  TUSHARE_TOKEN      必填
  TUSHARE_HTTP_URL   可选，默认 http://tushare.xyz（与 providers/tushare_provider.py 一致）
"""
from __future__ import annotations

import os
import sys
from typing import Callable
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dotenv import load_dotenv

load_dotenv(SCRIPT_DIR / ".env")


def main() -> int:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    base_url = os.getenv("TUSHARE_HTTP_URL", "http://tushare.xyz").strip()

    if not token:
        print("未设置 TUSHARE_TOKEN，无法探测。请配置 scripts/.env")
        return 1

    import pandas as pd
    import tushare as ts
    import tushare.pro.client as client

    client.DataApi._DataApi__http_url = base_url
    ts.set_token(token)
    pro = ts.pro_api()

    print(f"Base URL: {base_url}")
    print()

    def ok(msg: str) -> None:
        print(f"  OK  {msg}")

    def fail(msg: str) -> None:
        print(f"  FAIL {msg}")

    # 取最近一个上交所交易日（用于日线类接口）
    trade_d = None
    try:
        cal = pro.trade_cal(
            exchange="SSE",
            start_date=(datetime.now() - timedelta(days=60)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if cal is not None and not cal.empty and "is_open" in cal.columns:
            open_days = cal[cal["is_open"] == 1].sort_values("cal_date")
            if not open_days.empty:
                trade_d = str(open_days.iloc[-1]["cal_date"])
    except Exception as e:
        fail(f"trade_cal(取最近交易日): {e}")
        return 1

    if not trade_d:
        fail("trade_cal 未得到交易日")
        return 1

    d = trade_d
    print(f"探测使用交易日: {d} (YYYYMMDD)\n")

    probes: list[tuple[str, Callable[[], object]]] = []

    def add(name: str, fn):
        probes.append((name, fn))

    add("trade_cal", lambda: pro.trade_cal(exchange="SSE", start_date=d, end_date=d))
    add("index_daily(上证)", lambda: pro.index_daily(ts_code="000001.SH", start_date=d, end_date=d))
    add("index_daily(沪深300)", lambda: pro.index_daily(ts_code="000300.SH", start_date=d, end_date=d))
    add("daily(单日全市场 pct_chg)", lambda: pro.daily(trade_date=d, fields="ts_code,pct_chg"))
    add("daily(个股)", lambda: pro.daily(ts_code="000001.SZ", start_date=d, end_date=d))
    add("limit_list_d 涨停", lambda: pro.limit_list_d(trade_date=d, limit_type="U"))
    add("limit_list_d 跌停", lambda: pro.limit_list_d(trade_date=d, limit_type="D"))
    add("moneyflow_hsgt", lambda: pro.moneyflow_hsgt(trade_date=d))
    add("margin", lambda: pro.margin(trade_date=d))
    add("hsgt_top10 沪", lambda: pro.hsgt_top10(trade_date=d, market_type="1"))
    add("hsgt_top10 深", lambda: pro.hsgt_top10(trade_date=d, market_type="3"))
    add("top_list 龙虎榜", lambda: pro.top_list(trade_date=d))
    add("sw_daily", lambda: pro.sw_daily(trade_date=d))
    add("ths_daily", lambda: pro.ths_daily(trade_date=d))
    add("index_classify SW2021 L2", lambda: pro.index_classify(level="L2", src="SW2021"))
    add("ths_index", lambda: pro.ths_index(type="N"))

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
    add("index_global(DJI)", lambda: pro.index_global(ts_code="DJI", start_date=start, end_date=end))

    add("query anns_d", lambda: pro.query("anns_d", ts_code="000001.SZ", start_date=d, end_date=d))

    for name, fn in probes:
        try:
            df = fn()
            if df is None:
                fail(f"{name}: 返回 None")
            elif hasattr(df, "empty") and df.empty:
                ok(f"{name}: 成功但空表（可能无数据、非交易日或权限/积分不足）")
            else:
                n = len(df)
                ok(f"{name}: 成功 rows={n}")
        except Exception as e:
            err = str(e).replace("\n", " ")[:200]
            fail(f"{name}: {err}")

    print("\n说明: 「空表」在业务上常表示当日无记录、T+1 未出或接口积分不够；请以 Tushare 文档为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
