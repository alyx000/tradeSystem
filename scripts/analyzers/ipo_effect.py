"""次新股盘后横截面事实；无独立调度、写库或推送。"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
import statistics
from zoneinfo import ZoneInfo

from analyzers.low_price_effect import _canonical_code, _finite_float, _rate


MIN_MARKET_COUNT = 4000
MIN_COVERAGE = 0.98
B_SHARE_PREFIXES = ("200", "201", "900")


def _date(value):
    text = str(value or "")
    return datetime.strptime(text, "%Y%m%d" if len(text) == 8 else "%Y-%m-%d").date()


def _failed(trade_date, error, **extra):
    return dict(trade_date=trade_date, status="source_failed", error=str(error), gaps=[], **extra)


def _metrics(rows, eligible_codes):
    returns = [row["pct_chg"] for row in rows]
    intraday = [row["intraday_pct"] for row in rows if row["intraday_pct"] is not None]
    count = len(rows)
    missing = sorted(set(eligible_codes) - {row["code"] for row in rows})
    return {
        "sample_count": count,
        "eligible_count": len(eligible_codes),
        "missing_quote_codes": missing,
        "quote_coverage_ratio": _rate(count, len(eligible_codes)),
        "advance_count": sum(r > 0 for r in returns),
        "flat_count": sum(r == 0 for r in returns),
        "decline_count": sum(r < 0 for r in returns),
        "advance_rate": _rate(sum(r > 0 for r in returns), count),
        "pct_chg_mean": round(statistics.mean(returns), 2) if returns else None,
        "pct_chg_median": round(statistics.median(returns), 2) if returns else None,
        "strong_gain_count": sum(r >= 5 for r in returns),
        "strong_gain_rate": _rate(sum(r >= 5 for r in returns), count),
        "strong_loss_count": sum(r <= -5 for r in returns),
        "strong_loss_rate": _rate(sum(r <= -5 for r in returns), count),
        "intraday_sample_count": len(intraday),
        "intraday_advance_rate": _rate(sum(r > 0 for r in intraday), len(intraday)),
        "intraday_pct_mean": round(statistics.mean(intraday), 2) if intraday else None,
        "intraday_pct_median": round(statistics.median(intraday), 2) if intraday else None,
    }


def calculate_ipo_effect(quote_rows, basic_rows, st_rows, trade_date, *, recent_open_days,
                         sources=None, min_market_count=MIN_MARKET_COUNT):
    """上市天数=目标日-上市日的自然日差；各年龄组嵌套，不能相加。"""
    base = {"trade_date": trade_date, "source": sources or {}, "gaps": []}
    gaps = base["gaps"]
    coverage = base["coverage"] = {}

    def fail(message):
        return {**base, "status": "source_failed", "error": message}

    try:
        target = _date(trade_date)
        days = [_date(d) for d in recent_open_days]
        if len(days) != 5 or days != sorted(set(days)) or days[-1] != target:
            return fail("最近5个开放日缺失或错位")
    except (ValueError, TypeError):
        return fail("目标日或交易日历日期非法")
    if not all(isinstance(rows, list) and rows for rows in (quote_rows, basic_rows, st_rows)):
        return fail("行情、证券身份或ST名单为空/非法")

    identities = {}
    for row in basic_rows:
        if not isinstance(row, Mapping):
            return fail("证券身份行非法")
        code = _canonical_code(row.get("ts_code") or row.get("code"))
        if not code or code in identities:
            return fail("证券身份代码非法或重复")
        try:
            listed = _date(row.get("list_date"))
            delisted = _date(row["delist_date"]) if row.get("delist_date") else None
        except (ValueError, TypeError):
            return fail(f"上市/退市日期缺失或非法:{code}")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            return fail(f"证券名称缺失:{code}")
        identities[code] = dict(code=code, name=name, list_date=listed.isoformat(),
                                age_days=(target-listed).days,
                                active=listed <= target and (delisted is None or delisted > target),
                                exchange=str(row.get("ts_code") or "").partition(".")[2])

    st_codes = set()
    for row in st_rows:
        if not isinstance(row, Mapping):
            return fail("ST名单行非法")
        code = _canonical_code(row.get("ts_code") or row.get("code"))
        if not code or code in st_codes:
            return fail("ST名单代码非法或重复")
        if row.get("trade_date"):
            try:
                if _date(row["trade_date"]) != target:
                    return fail("ST名单日期错位")
            except (ValueError, TypeError):
                return fail("ST名单日期非法")
        st_codes.add(code)

    quotes, seen = {}, set()
    invalid = []
    for row in quote_rows:
        if not isinstance(row, Mapping):
            return fail("日线行非法")
        code = _canonical_code(row.get("ts_code") or row.get("code"))
        if not code or code in seen:
            return fail("日线代码非法或重复")
        seen.add(code)
        try:
            if _date(row.get("trade_date")) != target:
                return fail(f"日线日期错位:{code}")
        except (ValueError, TypeError):
            return fail(f"日线日期非法:{code}")
        if code not in identities:
            return fail(f"日线无法匹配证券身份:{code}")
        close, previous, pct = (_finite_float(row.get(k)) for k in ("close", "pre_close", "pct_chg"))
        if (close is None or close <= 0 or previous is None or previous <= 0 or pct is None
                or abs((close / previous - 1) * 100 - pct) > 0.02):
            invalid.append(code)
            continue
        opening = _finite_float(row.get("open"))
        quotes[code] = dict(code=code, close=close, pre_close=previous, open=opening,
                            pct_chg=pct, intraday_pct=(close/opening-1)*100
                            if opening is not None and opening > 0 else None)

    coverage.update(raw_quote_count=len(quote_rows), valid_quote_count=len(quotes),
                    invalid_quote_codes=invalid, identity_count=len(identities),
                    st_count=len(st_codes), minimum_market_count=min_market_count,
                    minimum_coverage_ratio=MIN_COVERAGE)
    if len(quotes) < min_market_count or len(quotes)/len(quote_rows) < MIN_COVERAGE:
        return fail("全市场有效日线不足或有效率低于98%")

    eligible = {code: row for code, row in identities.items()
                if row["active"] and row["age_days"] > 0 and code not in st_codes
                and not code.startswith(B_SHARE_PREFIXES) and "退" not in row["name"]}
    # 历史简称不是当日ST身份真源；以目标日 stock_st 名单执行ST剔除。
    if len(eligible) < min_market_count:
        return fail("证券宇宙有效数量不足")
    valid = [{**row, **quotes[code]} for code, row in eligible.items() if code in quotes]
    market = _metrics(valid, eligible)
    if len(valid) / len(eligible) < MIN_COVERAGE:
        return fail("目标日证券宇宙行情覆盖低于98%")
    if invalid:
        gaps.append(f"无效行情{len(invalid)}只，未计入统计")
    if market["missing_quote_codes"]:
        gaps.append(f"市场基准缺行情{len(market['missing_quote_codes'])}只，停牌未逐只确认")
    if market["intraday_sample_count"] != market["sample_count"]:
        gaps.append("部分开盘价缺失，开盘至收盘指标仅按有效开盘价计算")

    selectors = [
        ("listed_365d", "上市一年内（365天）", lambda r: r["age_days"] <= 365),
        ("listed_183d", "上市半年内（183天）", lambda r: r["age_days"] <= 183),
        ("listed_90d", "上市90天内", lambda r: r["age_days"] <= 90),
        ("listed_365d_ex_first5", "一年内·剔除上市前5交易日",
         lambda r: r["age_days"] <= 365 and _date(r["list_date"]) < days[0]),
        ("listed_365d_shsz", "一年内·仅沪深", lambda r: r["age_days"] <= 365 and r["exchange"] in {"SH", "SZ"}),
    ]
    cohorts = []
    for key, label, select in selectors:
        codes = {c for c, row in eligible.items() if select(row)}
        metrics = _metrics([r for r in valid if r["code"] in codes], codes)
        median = metrics["pct_chg_median"]
        metrics["median_excess_vs_market_pp"] = (round(median-market["pct_chg_median"], 2)
                                                  if median is not None else None)
        cohorts.append(dict(key=key, label=label, **metrics))
        if metrics["missing_quote_codes"]:
            gaps.append(f"{label}缺行情{len(metrics['missing_quote_codes'])}只")
    base.update(
        status="partial" if gaps else "complete", market_benchmark=market, cohorts=cohorts,
        constituents=[r for r in sorted(valid, key=lambda r: r["code"]) if r["age_days"] <= 365],
        definition={"version": "v1", "age_basis": "目标日减上市日的自然日差，1至365/183/90天（含边界）",
                    "universe": "沪深北A股，剔除当日ST、退市、B股和上市首日；年龄组嵌套不可相加",
                    "return_basis": "当日收盘相对行情昨收；个股等权",
                    "intraday_basis": "当日收盘/开盘-1，不代表可实现交易收益",
                    "first5_open_days": [d.isoformat() for d in days],
                    "identity_basis": "provider当次证券身份快照，按上市/退市日期约束目标日"})
    return base


def collect_ipo_effect(registry, trade_date, *, stock_st_result, now=None, db_path=None):
    """20:00盘后采集调用；严格只读日历，未知/休市/未收盘不调用行情源。"""
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.tzinfo is None:
        return _failed(trade_date, "运行时刻必须包含时区")
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    try:
        target = date.fromisoformat(trade_date)
        if target > now.date():
            return _failed(trade_date, "目标日尚未到达")
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        try:
            start = target-timedelta(days=20)
            calendar = {r[0]: r[1] for r in conn.execute(
                "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ?",
                (start.isoformat(), trade_date))}
        finally:
            conn.close()
        if calendar.get(trade_date) == 0:
            return dict(trade_date=trade_date, status="skipped", reason="非交易日", gaps=[])
        if any(calendar.get((start+timedelta(days=i)).isoformat()) not in (0, 1) for i in range(21)):
            return _failed(trade_date, "交易日历缺失或不完整")
        if target == now.date() and now.hour < 16:
            return dict(trade_date=trade_date, status="skipped", reason="尚未收盘", gaps=[])
        days = sorted(d for d, flag in calendar.items() if flag == 1)[-5:]
        if len(days) != 5:
            return _failed(trade_date, "最近5个开放日不足")
        results = {"st": stock_st_result}
        # ST为硬源，失败时无需额外网络调用。
        if not getattr(stock_st_result, "success", False):
            return _failed(trade_date, "ST名单来源失败")
        for key, method in (("quotes", "get_market_daily_quotes"), ("identity", "get_stock_universe_as_of")):
            results[key] = registry.call(method, trade_date)
            if not results[key].success:
                return _failed(trade_date, f"{key}来源失败:{results[key].error}")
        sources = {key: {"source": result.source, "fetched_at": result.fetched_at,
                         "note": result.note} for key, result in results.items()}
        return calculate_ipo_effect(results["quotes"].data, results["identity"].data,
                                    stock_st_result.data, trade_date, recent_open_days=days, sources=sources)
    except Exception as exc:
        return _failed(trade_date, f"次新股统计失败:{exc}")


def render_ipo_effect(effect):
    """供标准盘后Markdown使用；状态/缺值显式展示。"""
    lines = ["> 口径：上市1～365/183/90自然日，含沪深北，剔除ST/退市/B股及上市首日；"
             "各组嵌套不可相加。等权收盘相对昨收；开盘至收盘仅为价格表现。", ""]
    status = effect.get("status")
    if status not in {"complete", "partial"}:
        lines.append(f"- 状态：**{status or 'missing-data'}**；{effect.get('error') or effect.get('reason') or '状态未知'}。未计算，不代表样本为0。")
        return lines
    lines.append(f"- 状态：**{status}**")
    lines.extend(f"- 缺口：{gap}" for gap in effect.get("gaps", []))
    lines += ["", "| 分组 | 行情样本/应有 | 上涨率 | 涨幅中位 | 涨幅均值 | ≥5%占比 | ≤-5%占比 | 开收盘中位 | 收盘高于开盘 | 中位相对市场(pp) |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for group in [dict(label="全市场基准", **effect["market_benchmark"]), *effect["cohorts"]]:
        def fmt(key, rate=False):
            value = group.get(key)
            return "未计算" if value is None else (f"{value:.1%}" if rate else f"{value:+.2f}%")
        excess = group.get("median_excess_vs_market_pp")
        lines.append(f"| {group['label']} | {group['sample_count']}/{group['eligible_count']} | "
                     f"{fmt('advance_rate', True)} | {fmt('pct_chg_median')} | {fmt('pct_chg_mean')} | "
                     f"{fmt('strong_gain_rate', True)} | {fmt('strong_loss_rate', True)} | "
                     f"{fmt('intraday_pct_median')} | {fmt('intraday_advance_rate', True)} | "
                     f"{f'{excess:+.2f}' if excess is not None else '—'} |")
    lines += ["", "[事实·计算] 缺行情不补零、停牌未确认不视为空样本；完整次新成分及来源收据保存在同日盘后数据。"]
    return lines
