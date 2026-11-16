"""PK附加证据：只读事实日归档与本次日线，不改变入选和八维加权分。"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
import sqlite3
from statistics import mean, median
from types import SimpleNamespace

from analyzers import market_strength
from analyzers.strong_stock_feedback import _row_code
from services.board_break.rebreak import limit_rows
from utils.limit_identity import verified_streak
from services.board_break.sector_priority import coverage_audit, industry_bounds


def _limit_mapping(raw, day):
    limits = raw.get("limit_up")
    if (not isinstance(limits, dict)
            or limits.get("_source") not in {"tushare:limit_list_d", "akshare:stock_zt_pool_em"}
            or any(limits.get(k) for k in ("error", "_error", "errors"))
            or ("success" in limits and limits["success"] is not True)
            or ("status" in limits and limits["status"] not in {"ok", "success", "complete"})):
        raise ValueError("涨停归档来源失败、状态不完整或来源未识别")
    return limit_rows(SimpleNamespace(success=True, data=limits), day), limits["_source"]


def path_bounds(result):
    """分母核验后，未知日分别全非一价/全一价，得严格占比上下界。"""
    n, known = result.get("expected_count"), len(result.get("rows", []))
    if result.get("denominator_status") != "verified" or not n or known > n:
        return
    one = result["one_price_count"]
    unknown = n-known
    result.update(unknown_count=unknown, one_price_pct_lower=100*one/n,
                  one_price_pct_upper=100*(one+unknown)/n,
                  majority_one_price=True if 2*one > n else False if 2*(one+unknown) <= n else None,
                  all_traded=(result["traded_count"] == n))


def _archive(conn, day):
    row = conn.execute("SELECT raw_data FROM daily_market WHERE date=?", (day,)).fetchone()
    if not row:
        raise ValueError(f"{day}盘后归档缺失")
    envelope = json.loads(row[0])
    raw = envelope.get("raw_data", envelope)
    if envelope.get("date") != day or raw.get("date") != day:
        raise ValueError(f"{day}归档日期错位或缺失")
    return raw


def _open_days(conn, end, count):
    if type(count) is not int or not 1 <= count <= 30:
        raise ValueError("连续板数非法或超过30板证据窗口")
    cursor, days = date.fromisoformat(end), []
    for _ in range(90):
        day = cursor.isoformat()
        row = conn.execute("SELECT is_open,exchange FROM trade_calendar WHERE date=?", (day,)).fetchone()
        if row is None or row[0] not in (0, 1) or row[1] != "SSE":
            raise ValueError(f"{day}SSE日历缺失")
        if day == end and row[0] != 1:
            raise ValueError("涨停段末日非开放日")
        if row[0] == 1:
            days.append(day)
        if len(days) == count:
            return list(reversed(days))
        cursor -= timedelta(days=1)
    raise ValueError("涨停段日历不足")


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("日线数值缺失或非法")
    return value


def board_path(conn, cand, day, previous, load):
    result = dict(status="missing", rows=[], gaps=[], one_price_count=None,
                  traded_count=None, expected_count=None, denominator_status="missing", denominator_sources=[],
                  basis="日线OHLC一价/非一价涨停；非一价仅为换手代理，不证明充分换手或封单持续性")
    try:
        if _open_days(conn, day, 2)[0] != previous:
            raise ValueError("上一开放日错位")
        segments = [(previous, cand.get("limit_times"))]
        if cand.get("branch") == "rebreak":
            evidence = cand.get("pattern_evidence") or {}
            days = _open_days(conn, day, 4)
            if evidence.get("dates") != days or cand.get("limit_times") != 1:
                raise ValueError("反包分支日期链或首板数非法")
            segments.insert(0, (days[0], evidence.get("prior_limit_times")))
        dates = [d for end, n in segments for d in _open_days(conn, end, n)]
        result["expected_count"] = len(dates)
        try:
            for end, n in segments:
                mapping, source = _limit_mapping(load(end), end)
                verified, basis = verified_streak(mapping.get(cand["code"], {}), source)
                if verified != n:
                    raise ValueError(f"{end}连续板数分母未核验或不一致")
                result["denominator_sources"].append(dict(date=end, count=n, source=source, basis=basis))
            result["denominator_status"] = "verified"
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
            result["gaps"].append(str(exc))
        for d in dates:
            try:
                raw = load(d)
                mapping, _ = _limit_mapping(raw, d)
                if cand["code"] not in mapping:
                    raise ValueError("归档未核验为涨停")
                bars = [b for b in cand.get("bars", []) if b.get("trade_date") == d]
                if len(bars) != 1:
                    raise ValueError("日线缺失或重复")
                b = bars[0]
                for field in ("code", "ts_code"):
                    if field in b and str(b[field]).split(".")[0] != cand["code"]:
                        raise ValueError("日线股票身份错位")
                op, hi, lo, cl = (_number(b.get(k)) for k in ("open", "high", "low", "close"))
                if not 0 < lo <= min(op, cl) <= max(op, cl) <= hi:
                    raise ValueError("OHLC范围非法")
                volume = b.get("vol", b.get("volume"))
                if _number(volume) <= 0:
                    raise ValueError("无有效成交量")
                result["rows"].append(dict(date=d, kind="one_price" if hi == lo else "traded",
                                           open=op, high=hi, low=lo, close=cl,
                                           source=raw["limit_up"].get("_source", "盘后涨停归档")))
            except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
                result["gaps"].append(f"{d}:{exc}")
        result["one_price_count"] = sum(r["kind"] == "one_price" for r in result["rows"])
        result["traded_count"] = sum(r["kind"] == "traded" for r in result["rows"])
        result["status"] = "partial" if result["gaps"] else "complete"
        path_bounds(result)
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        result["gaps"].append(str(exc))
    return result


def sector_context(block, cand, day, audit=None):
    """同日申万二级归属与剔除自身后的板块广度，不把个股涨幅重复当板块强度。"""
    out = dict(status="missing", trade_date=day, industry=cand.get("industry"),
               basis="事实日申万二级归档，剔除候选自身的有效样本", gaps=[],
               leadership=dict(status="not_computed", reason="无分时先后及跟随证据，不能从同涨/领涨推断带动"))
    if block.get("status") not in {"complete", "partial"}:
        out["gaps"] = block.get("gaps") or ["同日板块证据缺失"]
        return out
    members = [r for r in block["rows"] if _row_code(r).split(".")[0] == cand["code"]]
    if len(members) != 1 or members[0]["industry"] in {"未分类", "UNCLASSIFIED", ""}:
        out["gaps"] = ["缺少事实日可核验的板块归属"]
        return out
    industry = members[0]["industry"]
    peers = [r["pct_chg"] for r in block["rows"]
             if r["industry"] == industry and _row_code(r).split(".")[0] != cand["code"]]
    if not peers:
        out["gaps"] = ["板块无其他有效样本"]
        return out
    out.update(status=block["status"], industry=industry, sample_count=len(peers),
               mean_pct=round(mean(peers), 4), median_pct=round(median(peers), 4),
               up_pct=round(100 * sum(v > 0 for v in peers) / len(peers), 4),
               strong_pct=round(100 * sum(v >= 5 for v in peers) / len(peers), 4),
               weak_pct=round(100 * sum(v <= -5 for v in peers) / len(peers), 4),
               gaps=["全市场覆盖限制（不是该行业缺口数）：" + str(g) for g in block.get("gaps", [])],
               source="daily_market.raw_data.market_strength",
               sources=block.get("sources", {}))
    bounds = industry_bounds(peers, industry, audit or {})
    out["coverage_audit"] = bounds
    out["status"] = "complete" if bounds["status"] == "complete" else "partial"
    out["global_gaps"] = out["gaps"]
    if bounds["status"] in {"complete", "bounded"}:
        out["gaps"] = ([] if bounds["status"] == "complete" else
                       [f"本行业已定位缺行情{len(bounds['known_missing_codes'])}只，"
                        f"另有{len(bounds['unresolved_codes'])}只归属未知；最不利全部计入本行业计算范围"])
    else:
        out["gaps"] = [*out["gaps"], *bounds.get("gaps", [])]
    return out


def build_contexts(conn, candidates, day, previous):
    cache = {}

    def load(d):
        if d not in cache:
            try:
                cache[d] = _archive(conn, d)
            except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
                cache[d] = str(exc)
        if isinstance(cache[d], str):
            raise ValueError(cache[d])
        return cache[d]

    try:
        raw = load(day)
        block = market_strength.view(raw.get("market_strength"), day)
        audit = coverage_audit(block, raw, day)
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
        block = dict(status="missing", gaps=[str(exc)])
        audit = dict(status="missing", gaps=[str(exc)])
    return {c["code"]: dict(board_path=board_path(conn, c, day, previous, load),
                            sector_context=sector_context(block, c, day, audit)) for c in candidates}


def describe(context):
    path, sector = context.get("board_path") or {}, context.get("sector_context") or {}
    p = (f"{path.get('status', 'missing')}：已核验{len(path.get('rows', []))}/{path.get('expected_count', '—')}板，"
         f"日线一价{path.get('one_price_count', '—')}板、非一价{path.get('traded_count', '—')}板；"
         + path.get("basis", "未计算"))
    if path.get("rows"):
        p += "；" + "、".join(r["date"] + ("一价" if r["kind"] == "one_price" else "非一价") for r in path["rows"])
    if path.get("gaps"):
        p += "；缺口：" + "；".join(path["gaps"])
    if "one_price_pct_lower" in path:
        p += (f"；分母已核验，一价占比范围[{path['one_price_pct_lower']:.2f}%,"
              f"{path['one_price_pct_upper']:.2f}%]；"
              + ("已确定一价居多" if path.get("majority_one_price") else "未确定一价居多"))
    s = f"{sector.get('status', 'missing')}：{sector.get('industry') or '归属待核'}"
    if sector.get("sample_count"):
        s += (f"；{sector['trade_date']}剔除自身后{sector['sample_count']}只，均值{sector['mean_pct']:+.2f}%、"
              f"中位{sector['median_pct']:+.2f}%、上涨占比{sector['up_pct']:.2f}%、"
              f"≥5%占比{sector['strong_pct']:.2f}%、≤-5%占比{sector['weak_pct']:.2f}%；{sector['source']}")
    if sector.get("gaps"):
        s += "；缺口：" + "；".join(sector["gaps"])
    audit = sector.get("coverage_audit") or {}
    if audit.get("status") in {"complete", "bounded"}:
        s += (f"；行业缺口核验={audit['status']}；本行业缺行情名单={audit['known_missing_codes']}；"
              f"归属未知={audit['unresolved_codes']}；已定位其他行业={audit['elsewhere_missing']}；"
              f"中位保守范围[{audit['median_lower']},{audit['median_upper']}]%，"
              f"上涨占比保守范围[{audit['up_pct_lower']:.2f},{audit['up_pct_upper']:.2f}]%")
    return p, s + "；带动板块：未核验，不加分"
