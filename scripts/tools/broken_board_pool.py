"""回验单日炸板池与独立日线触板样本，只向单独的日期归档导出。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import pickle
import re
import sqlite3
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from analyzers import broken_board_pool as pool
from services.board_break_intraday.data import calendar
from services.board_break_intraday.store import lock
from services.dated_market_archive import read_source
from services.handwritten_review import valid_date
from services.volume_record.service import atomic_write
from utils.network_env import without_standard_http_proxy


def _rows(folder, manifest, day, file, method, source):
    rows, evidence = read_source(folder, manifest, day, file, method, source)
    if not isinstance(rows, list):
        raise ValueError("日线对照记录结构错误")
    result = {}
    for row in rows:
        code = row.get("ts_code") if isinstance(row, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or code in result:
            raise ValueError("日线对照证券身份重复或错误")
        if row.get("trade_date") != day.replace("-", ""):
            raise ValueError("日线对照行情错日")
        result[code] = row
    return result, evidence


def touch_evidence(root, day):
    """收盘行情高点达到非零限价且收盘回落，仅说明成交曾触价。"""
    folder = root / "data/reports/volume-buildup/runs" / day
    try:
        manifest = json.loads((folder / "report.json").read_text())
        if manifest["date"] != day:
            raise ValueError("日线对照目录事实日错位")
        quotes, q_receipt = _rows(folder, manifest, day, "cache/market.pkl", "get_market_daily_quotes", "tushare:daily")
        limits, l_receipt = _rows(folder, manifest, day, f"limits-{day}.pkl", "get_stock_limit_prices", "tushare:stk_limit")
        st, s_receipt = _rows(folder, manifest, day, "cache/stock_st.pkl", "get_stock_st", "tushare:stock_st")
        if len(quotes) < 4000 or not quotes.keys() <= limits.keys() or not st:
            raise ValueError("日线、限价或ST对照覆盖不足")
        rows = []
        for code, quote in sorted(quotes.items()):
            if code.startswith(("900", "200")):
                continue
            high, close, upper = (pool._number(v) for v in (quote.get("high"), quote.get("close"), limits[code].get("up_limit")))
            if high is None or close is None or upper is None or high < close:
                raise ValueError("日线对照价格缺失或不合规 " + code)
            if high == upper and close < upper:
                rows.append(dict(code=code, high=high, up_limit=upper, close=close, is_st=code in st))
        return dict(status="complete", trade_date=day, definition="当日有行情的A股中，最高成交价等于真实涨停价，且收盘低于涨停价。",
                    quote_rows=len(quotes), limits_rows=len(limits), count=len(rows),
                    count_ex_st=sum(not s["is_st"] for s in rows), stocks=rows,
                    sources=[q_receipt, l_receipt, s_receipt])
    except (KeyError, OSError, ValueError, EOFError, TypeError, pickle.UnpicklingError) as exc:
        return dict(status="unavailable", trade_date=day, count=None, gaps=[str(exc)])


def run(root, day, requester, save=False):
    uri = (root / "data/trade.db").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        if not calendar(conn, day)["is_open"]:
            raise ValueError("目标日为休市日")
    now = datetime.now(pool.TZ)
    if day > now.date().isoformat() or (day == now.date().isoformat() and now.hour < 16):
        raise ValueError("未到目标日盘后")
    raw = pool.archived_post(root, day)
    old_limit = raw.get("limit_up", {})
    count = old_limit.get("broken_count")
    legacy = dict(reported_count=count if type(count) is int and count > 0 else None,
                  source=str(root / "daily" / day / "post-market.yaml"),
                  note="历史盘后汇总。旧版本源失败可能返回0，孤立的0无法核验。")
    block = pool.prefer_verified(pool.collect(day), raw.get(pool.KEY))
    cross = touch_evidence(root, day)
    result = dict(trade_date=day, input_by=requester, checked_at=now.isoformat(), broken_board_pool=block,
                  prior_post_summary=legacy, daily_touch_only=cross)
    folder = root / "data/reports/broken-board-pool"
    path = folder / (day + ".json")
    if save:
        with lock(folder, day):
            if path.exists():
                old = json.loads(path.read_text())
                if old.get("trade_date") != day:
                    raise ValueError("旧档事实日错位")
                result[pool.KEY] = pool.prefer_verified(block, old.get(pool.KEY))
                if old.get("daily_touch_only", {}).get("status") == "complete" and cross["status"] != "complete":
                    # 原子保留完整旧文件，避免未重新校验的独立样本被覆盖。
                    if old.get(pool.KEY, {}).get("status") == "complete" or result[pool.KEY]["status"] != "complete":
                        return old
                    result["daily_touch_only"] = old["daily_touch_only"]
            atomic_write(path, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=valid_date)
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.input_by.strip():
        parser.error("--input-by 不得为空")
    try:
        with without_standard_http_proxy():
            result = run(SCRIPTS.parent, args.date, args.input_by.strip(), save=args.save)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, str(exc) + "\n")
    block = result[pool.KEY]
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(args.date, "东财炸板池", block["status"], block["count"], "日线触板未封", result["daily_touch_only"]["count"])
        for gap in block["gaps"]:
            print(gap)
    return 0 if block["status"] == "complete" else 2


if __name__ == "__main__":
    sys.exit(main())
