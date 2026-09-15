"""复用盘后交易日任务；行业/概念分支隔离，无数据库或推送写入。"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from .detectors import DAILY_BARS, MINUTE_DAYS, PARAMETERS, SCHEMA, analyze, normalize, number

ACTIVITY_DAYS = 5
TOP_INDUSTRIES = 10
TOP_CONCEPTS = 8
TODAY_TOP = 5
METHOD = (
    "来源：鞠磊 teacher_notes#826；认知 cog_1aad1d19 / cog_c0a2c385。"
    "[判断] 工程代理v1，未回测为胜率：近60日先低后高涨幅≥20%、峰值距今≤20日；"
    "MACD(12,26,9)，日线红柱衰减≥10%；双侧2根确认高点、两峰间隔≥4根且回落≥1%。"
    "60分钟两峰间最大成交量阳线低点作为支撑代理，连续2根收盘跌破作为确认代理。"
    "图形45度、双胞胎数字不作规则；涨幅门槛和支撑代理不是老师给出的精确阈值。"
)


def failed(report_date, reason, *, status="source_failed"):
    return {"schema": SCHEMA, "date": report_date, "status": status,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "method": METHOD, "parameters": dict(PARAMETERS), "rows": [],
            "gaps": [reason], "market": {"status": "source_failed"}}


def calendar(db_path, report_date, now=None):
    target = date.fromisoformat(report_date)
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        raise ValueError("运行时钟缺少时区")
    current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    if target > current.date() or (target == current.date() and current.hour < 16):
        raise ValueError("仅上海16:00后读取完成交易日")
    start = target-timedelta(days=260)
    with sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ? ORDER BY date",
            (start.isoformat(), report_date),
        ).fetchall()
    expected = [(start+timedelta(days=i)).isoformat() for i in range(261)]
    if [r[0] for r in rows] != expected or any(r[1] not in (0, 1) for r in rows):
        raise ValueError("SSE自然日日历不完整")
    if rows[-1][1] == 0:
        return []
    opened = [d for d, flag in rows if flag]
    if len(opened) < DAILY_BARS:
        raise ValueError("开放日历不足120日")
    return opened[-DAILY_BARS:]


def records(frame):
    if frame is None or not hasattr(frame, "to_dict") or frame.empty:
        raise ValueError("来源为空，未证实无数据")
    return frame.to_dict("records")


def exclude_closed_rows(rows, code, expected):
    """来源偶发返回周末占位行：仅按已验证完整SSE日历排除，保留异常清单。

    不丢弃开放日坏行、不接纳未来/范围外行情，也不把占位NaN补成有效行情。
    """
    kept, excluded, seen = [], [], set()
    for row in rows:
        day = datetime.strptime(str(row.get("trade_date", "")).replace("-", ""), "%Y%m%d").date().isoformat()
        if str(row.get("ts_code")) != code or day in seen or not expected[0] <= day <= expected[-1]:
            raise ValueError("行情身份、重复或范围校验失败")
        seen.add(day)
        if day in expected:
            kept.append(row)
        else:
            excluded.append(day)
    return kept, sorted(excluded)


def select_active(snapshots, names, days, *, kind):
    """5日同一完整宇宙；不能按有数据的天数平均稀疏序列。"""
    metric = "amount" if kind == "industry" else "turnover_rate"
    values = {}
    for day in days:
        rows = snapshots[day]
        seen = set()
        for row in rows:
            code = str(row.get("ts_code"))
            if str(row.get("trade_date")) != day.replace("-", "") or code in seen:
                raise ValueError("活跃榜日期错误或代码重复")
            seen.add(code)
            if code not in names:
                continue
            if not valid_activity(row, metric):
                continue
            value, pct = number(row.get(metric)), number(row.get("pct_change"))
            values.setdefault(code, {})[day] = (value, pct)
    complete = {c: v for c, v in values.items() if len(v) == len(days)}
    if not complete:
        raise ValueError("活跃榜无连续5日完整板块")
    limit = TOP_INDUSTRIES if kind == "industry" else TOP_CONCEPTS
    avg_rank = sorted(complete, key=lambda c: (-mean(x[0] for x in complete[c].values()), c))[:limit]
    today_rank = sorted(complete, key=lambda c: (-complete[c][days[-1]][1], c))[:TODAY_TOP]
    selected = list(dict.fromkeys(avg_rank+today_rank))
    return [{"code": c, "name": names[c], "kind": kind,
             "activity_metric": metric, "activity_mean": round(mean(x[0] for x in complete[c].values()), 4),
             "selection_reason": [label for group, label in [(avg_rank, "近5日活跃榜"), (today_rank, "当日涨幅榜")] if c in group],
             "activity_dates": days} for c in selected]


def valid_activity(row, metric):
    """单板块缺指标只排除该板块；覆盖审计仍保留它，不连累其余可用行。"""
    try:
        return number(row.get(metric)) >= 0 and number(row.get("pct_change")) is not None
    except (ValueError, TypeError):
        return False


def market_context(pro, days):
    expected = days[-23:]
    series = []
    for code in ("000001.SH", "399106.SZ"):
        raw = records(pro.index_daily(ts_code=code, start_date=expected[0].replace("-", ""), end_date=expected[-1].replace("-", "")))
        normalize(raw, code, expected)
        amounts = {str(r["trade_date"]): number(r.get("amount")) for r in raw}
        if any(x <= 0 for x in amounts.values()):
            raise ValueError("两市成交额缺失或非正")
        series.append([amounts[d.replace("-", "")] for d in expected])
    # 课程以全市场总成交额解释增量环境，不要求沪深分别放量。
    # 三个目标日各自有一个前20日窗口；这里的分子与分母始终是两市合计。
    total = [a+b for a, b in zip(*series)]
    ratios = [total[i]/mean(total[i-20:i]) for i in range(20, 23)]
    return {"status": "complete", "source": "tushare:index_daily:000001.SH+399106.SZ",
            "ratio_to_prior20": round(ratios[-1], 4),
            "three_ratios": [round(x, 4) for x in ratios],
            "sustained_increment": all(x >= 1.1 for x in ratios),
            "definition": "沪深合计成交额连续3日，每日均≥该日前20开放日的合计成交额均值×1.1（工程代理）",
            "dates": expected, "amount_thousand_yuan": total}


def collect(registry, report_date, *, db_path=None, now=None):
    if db_path is None:
        from db.connection import _DEFAULT_DB_PATH
        db_path = _DEFAULT_DB_PATH
    result = failed(report_date, "")
    result["gaps"] = []
    try:
        days = calendar(db_path, report_date, now)
    except (ValueError, TypeError, sqlite3.Error, OSError):
        return failed(report_date, "完成交易日／本地完整SSE日历校验失败")
    if not days:
        return failed(report_date, "SSE休市日，未计算", status="skipped")
    result["expected_dates"] = days
    provider = registry.get_provider("tushare")
    if provider is None or provider.pro is None:
        return failed(report_date, "Tushare数据源不可用")
    pro = provider.pro
    try:
        result["market"] = market_context(pro, days)
    except Exception:
        result["gaps"].append("两市成交额背景缺失，增量例外未计算")
    result["selection"] = {
        "days": days[-ACTIVITY_DAYS:], "industry_top": TOP_INDUSTRIES, "concept_top": TOP_CONCEPTS,
        "today_top_each": TODAY_TOP,
        "definition": "可得且连续5日完整的申万二级平均成交额前10／同花顺概念平均换手率前8，各并入当日涨幅前5；去重。缺失板块不参与排名，不称全市场完整活跃榜。分类为取数时快照，非历史成分回溯。",
        "branches": {},
    }
    minute_unavailable = None
    for kind, api in (("industry", "sw_daily"), ("concept", "ths_daily")):
        try:
            taxonomy = records(pro.index_classify(level="L2", src="SW2021") if kind == "industry" else pro.ths_index(type="N"))
            code_field, name_field = ("index_code", "industry_name") if kind == "industry" else ("ts_code", "name")
            names = {str(r[code_field]): str(r[name_field]) for r in taxonomy}
            if len(names) != len(taxonomy) or not names or any(not x or x == "nan" for x in names.values()):
                raise ValueError("板块分类不完整")
            snapshots = {d: records(getattr(pro, api)(trade_date=d.replace("-", ""))) for d in days[-ACTIVITY_DAYS:]}
            selected = select_active(snapshots, names, days[-ACTIVITY_DAYS:], kind=kind)
            metric = "amount" if kind == "industry" else "turnover_rate"
            covered = set(names).intersection(*[{str(r["ts_code"]) for r in snapshots[d] if valid_activity(r, metric)} for d in days[-ACTIVITY_DAYS:]])
            missing = sorted(set(names)-covered)
            result["selection"]["branches"][kind] = {
                "status": "partial" if missing else "complete", "universe": len(names),
                "covered": len(covered), "selected": len(selected),
                "missing": [{"code": code, "name": names[code]} for code in missing],
            }
            if missing:
                result["gaps"].append(f"{kind}活跃榜仅覆盖{len(covered)}/{len(names)}个分类，缺失板块未参评，完整缺失名单见JSON")
        except Exception:
            result["selection"]["branches"][kind] = {"status": "source_failed"}
            result["gaps"].append(f"{kind}活跃榜来源或连续5日覆盖失败")
            continue
        for item in selected:
            row = {**item, "status": "source_failed", "level": "missing_data", "gaps": [],
                   "daily_source": "tushare:"+api, "minute_source": "tushare:sw_mins" if kind == "industry" else None}
            try:
                raw = records(getattr(pro, api)(ts_code=item["code"], start_date=days[0].replace("-", ""), end_date=days[-1].replace("-", "")))
                raw, excluded = exclude_closed_rows(raw, item["code"], days)
                daily = normalize(raw, item["code"], days)
                minutes = None
                gap = "同花顺概念无已核验同口径60分钟源" if kind == "concept" else minute_unavailable
                if kind == "industry" and not minute_unavailable:
                    try:
                        stamps = [d+" "+t for d in days[-MINUTE_DAYS:] for t in ("10:30:00", "11:30:00", "14:00:00", "15:00:00")]
                        minute_raw = records(pro.sw_mins(ts_code=item["code"], freq="60min", start_date=days[-MINUTE_DAYS]+" 09:00:00", end_date=report_date+" 15:00:00"))
                        minutes = normalize(minute_raw, item["code"], stamps, minute=True)
                    except Exception as exc:
                        gap = "申万60分钟来源失败或时间覆盖不完整"
                        if "权限" in str(exc) or "permission" in str(exc).lower():
                            minute_unavailable = gap = "申万60分钟权限不足（本次运行停止重复请求）"
                row.update(analyze(daily, minutes))
                row.update(status="complete" if minutes and not excluded else "partial", daily_bars=daily,
                           excluded_closed_dates=excluded,
                           minute_bars=minutes, minute_status="complete" if minutes else "source_failed")
                if excluded:
                    row["gaps"].append("源含已核实休市日占位行，已排除："+"、".join(excluded))
                if gap:
                    row["gaps"].append(gap)
            except Exception:
                row["gaps"].append("日线身份、数值或120开放日覆盖失败")
            result["rows"].append(row)
    valid = [r for r in result["rows"] if r["status"] != "source_failed"]
    result["status"] = "source_failed" if not valid else ("partial" if result["gaps"] or any(r["status"] != "complete" for r in result["rows"]) else "complete")
    result["coverage"] = {"selected": len(result["rows"]), "daily_valid": len(valid),
                          "minute_valid": sum(r.get("minute_status") == "complete" for r in result["rows"])}
    return result
