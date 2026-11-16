"""独立的反包后再断板分支；只读四个相邻开放日，缺源不改变原分支。"""
from __future__ import annotations

from datetime import date as Date, timedelta
import math
import re

from services.board_break import constants as C
from utils import is_st_stock
from utils.limit_identity import positive_streak, verified_streak


def recent_dates(conn, day: str, previous: str) -> list[str]:
    """只读本地SSE日历，逐自然日回溯；遇缺口即停止，绝不跳过未知日期。"""
    cursor = Date.fromisoformat(day)
    opens = []
    for _ in range(45):  # 有界且覆盖长假；不是用自然日代替交易日
        d = cursor.isoformat()
        row = conn.execute("SELECT is_open,exchange FROM trade_calendar WHERE date=?", (d,)).fetchone()
        if row is None or row[0] not in (0, 1) or row[1] != "SSE":
            raise ValueError(f"{d}完整SSE日历缺失或非法")
        if d == day and row[0] != 1:
            raise ValueError("目标日不是开放日")
        if row[0] == 1:
            opens.append(d)
        if len(opens) == 4:
            if opens[1] != previous:
                raise ValueError("新旧分支上一开放日不一致")
            return list(reversed(opens))
        cursor -= timedelta(days=1)
    raise ValueError("45自然日内不足四个已核验开放日")


def limit_rows(result, day: str, *, allow_empty_list=False) -> dict:
    """校验新分支来源；兼容provider明确成功的空跌停列表。"""
    if not result.success:
        raise ValueError(str(result.error or "来源失败"))
    data = result.data
    if allow_empty_list and data == []:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("stocks"), list):
        raise ValueError("涨跌停名单结构缺失")
    rows = data["stocks"]
    if "count" in data and data["count"] != len(rows):
        raise ValueError("涨跌停名单数量不一致")
    mapping = {}
    for item in [data, *rows]:
        if not isinstance(item, dict):
            raise ValueError("涨跌停条目非法")
        for key in ("date", "trade_date"):
            if key in item and str(item[key]).replace("-", "") != day.replace("-", ""):
                raise ValueError("涨跌停名单日期错位")
    for row in rows:
        code = str(row.get("code", "")).split(".")[0]
        if not re.fullmatch(r"\d{6}", code) or code in mapping:
            raise ValueError("涨跌停名单代码非法或重复")
        mapping[code] = row
    return mapping


def pattern_bars(bars: list, dates: list[str]) -> list[dict]:
    if not bars or bars[-1].get("trade_date") != dates[-1]:
        raise ValueError("日线末根非目标日")
    out = []
    for day in dates:
        matches = [b for b in bars if b.get("trade_date") == day]
        if len(matches) != 1:
            raise ValueError(f"{day}日线缺失或重复，不能跳过停牌")
        b = matches[0]
        for key in ("close", "pct_chg"):
            value = b.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{day}日线{key}缺失或非法")
        if b["close"] <= 0:
            raise ValueError(f"{day}收盘价非法")
        # 日线存在是最低交易证据；provider带成交量/额时，零成交不得冒充有交易。
        for key in ("vol", "volume", "amount"):
            if key in b and (isinstance(b[key], bool) or not isinstance(b[key], (int, float))
                             or not math.isfinite(b[key]) or b[key] <= 0):
                raise ValueError(f"{day}无有效成交或{key}非法")
        out.append(b)
    return out


def scan(conn, registry, day, previous, prev_result, today_up_result, today_down_result, fetch_range):
    """T-3连板>=2 → T-2断板 → T-1首板涨停 → T再断板。

    limit_times始终保留昨日真实值1，前段高度另存，避免评分/PK误读为当前三连板。
    新分支硬源失败或逐股证据缺口单独报告，原分支仍可用。
    """
    report = dict(status="complete", dates=[], sources={}, gaps=[], rejects={}, candidates=[])

    def reject(reason):
        report["rejects"][reason] = report["rejects"].get(reason, 0) + 1

    def rows(result, d, key, *, down=False):
        report["sources"][key] = dict(date=d, source=result.source, ok=False)
        value = limit_rows(result, d, allow_empty_list=down)
        report["sources"][key]["ok"] = True
        return value

    try:
        prev = rows(prev_result, previous, "rebound_limit_up")
        up = rows(today_up_result, day, "today_limit_up")
        down = rows(today_down_result, day, "today_limit_down", down=True)
        eligible = {}
        for code, row in prev.items():
            if (not code.startswith(C.MAIN_BOARD_PREFIXES) or is_st_stock(row.get("name", ""))
                    or code in up or code in down):
                continue
            height, _ = verified_streak(row, prev_result.source)
            if height is None:
                # 昨日>=2的旧分支口径不在本分支改变；只审计可能首板的未知值。
                if positive_streak(row.get("limit_times")) not in (None, 1):
                    continue
                report["gaps"].append(f"{code}昨日首板身份未核验")
            elif height == 1:
                eligible[code] = row
        if not eligible:
            report["status"] = "partial" if report["gaps"] else "complete"
            return report

        dates = recent_dates(conn, day, previous)
        report["dates"] = dates
        anchor_day, break_day, rebound_day, _ = dates
        anchor_result = registry.call("get_limit_up_list", anchor_day)
        anchor = rows(anchor_result, anchor_day, "prior_limit_up")
        broken_up = rows(registry.call("get_limit_up_list", break_day), break_day, "first_break_limit_up")
        broken_down = rows(registry.call("get_limit_down_list", break_day), break_day, "first_break_limit_down", down=True)
        for code, row in eligible.items():
            if code not in anchor:
                reject("无前段连板")
                continue
            prior, _ = verified_streak(anchor[code], anchor_result.source)
            if prior is None:
                report["gaps"].append(f"{code}前段连板高度未核验")
                continue
            if prior < C.MIN_LIMIT_TIMES or is_st_stock(anchor[code].get("name", "")):
                reject("前段非合格连板")
                continue
            if code in broken_up or code in broken_down:
                reject("首次断板仍涨停或已跌停")
                continue
            try:
                start = (Date.fromisoformat(day)-timedelta(days=C.LOOKBACK_NATURAL_DAYS)).isoformat()
                bars = fetch_range(code, start, day)
                selected = pattern_bars(bars, dates)
            except Exception as exc:
                report["gaps"].append(f"{code}日线缺口：{exc}")
                continue
            if any(selected[i]["pct_chg"] > C.BREAK_DAY_MAX_PCT for i in (1, 3)):
                reject("首次或再次断板涨幅>6%")
                continue
            last = selected[-1]
            evidence = dict(dates=dates, prior_limit_times=prior,
                            first_break_pct_chg=selected[1]["pct_chg"],
                            rebound_limit_times=1, rebound_close=selected[2]["close"],
                            prior_limit_source=anchor_result.source,
                            rebound_limit_source=prev_result.source)
            report["candidates"].append(dict(
                code=code, name=row.get("name", ""), industry=row.get("industry", ""),
                limit_times=1, branch="rebreak", pattern_evidence=evidence, bars=bars,
                close=last["close"], pct_chg=last["pct_chg"],
                ref_price=round(last["close"]*C.REBOUND_REF_RATIO, 2)))
    except Exception as exc:
        report["status"] = "source_failed"
        report["gaps"].append(str(exc))
        return report
    report["status"] = "partial" if report["gaps"] else "complete"
    return report
