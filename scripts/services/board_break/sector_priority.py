"""按行业定位缺口，并在最不利补全下执行明确的板块优先规则。"""
from __future__ import annotations

import math
from statistics import median

from analyzers import sector_increment
from analyzers.strong_stock_feedback import _row_code


def coverage_audit(block, raw, day):
    out = dict(status="missing", mapped_missing=[], unresolved_codes=[], gaps=[])
    try:
        if block.get("status") not in {"complete", "partial"}:
            raise ValueError("板块行情未通过核验")
        for key in ("industry", "quotes"):
            s = block.get("sources", {}).get(key, {})
            if s.get("status") != "success" or s.get("error") or str(s.get("fetched_at", ""))[:10] != day:
                raise ValueError("事实日行情/行业来源未核验")
        if block["sources"]["industry"].get("source") != "tushare:index_member_all":
            raise ValueError("申万二级行业来源未识别")
        covered = {_row_code(r): r["industry"] for r in block["rows"]}
        c = block["coverage"]
        codes = c.get("missing_codes")
        if not isinstance(codes, list):
            raise ValueError("全市场缺行情名单缺失")
        missing = [_row_code(dict(code=code)) for code in codes]
        if (len(set(missing)) != len(missing) or set(missing) & covered.keys()
                or len(missing) != c["eligible_count"] - c["sample_count"]):
            raise ValueError("缺行情名单与覆盖计数不一致")
        unknown = [code for code, name in covered.items() if name in {"未分类", "UNCLASSIFIED", ""}]
        # 只从事实日同一申万口径的既有增量证据补身份；不请求当前成分回填历史。
        extra = sector_increment.validate(raw.get("sector_increment"), day)
        mapping = {}
        if extra.get("status") in {"complete", "partial"}:
            try:
                sources = extra.get("sources", [])
                industry_sources = [s for s in sources if s.get("source") == "tushare:index_member_all"]
                if len(industry_sources) != 1:
                    raise ValueError("辅助行业身份来源缺失或重复")
                source = industry_sources[0]
                if (str(source.get("fetched_at", ""))[:10] != day
                        or any(source.get(k) for k in ("error", "_error", "errors"))
                        or source.get("status", "success") not in {"success", "complete", "ok"}):
                    raise ValueError("辅助行业身份缺有效事实日来源")
                for r in extra["components"]:
                    code, industry = _row_code(r), r.get("industry")
                    if code in mapping or not isinstance(industry, str) or not industry.strip():
                        raise ValueError("辅助行业身份重复或非法")
                    if code in covered and covered[code] not in {"未分类", "UNCLASSIFIED", ""} and covered[code] != industry:
                        raise ValueError("同日行业身份来源冲突")
                    mapping[code] = industry
            except (ValueError, TypeError, KeyError, AttributeError):
                mapping = {}
        for code in missing:
            industry = mapping.get(code)
            if industry and industry not in {"未分类", "UNCLASSIFIED"}:
                out["mapped_missing"].append(dict(code=code, industry=industry,
                                                   source="同日sector_increment.components/申万二级"))
            else:
                out["unresolved_codes"].append(code)
        # 已有行情但归属不明的对象也可能属于任何行业，纳入保守范围，不漏计。
        out["unresolved_codes"].extend(unknown)
        out.update(status="ok", global_missing_count=len(missing), unclassified_count=len(unknown))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        out["gaps"].append(str(exc))
    return out


def industry_bounds(peers, industry, audit):
    if audit.get("status") != "ok":
        return dict(status="unknown", gaps=audit.get("gaps") or ["行业覆盖未核验"])
    local = [r["code"] for r in audit["mapped_missing"] if r["industry"] == industry]
    elsewhere = [r for r in audit["mapped_missing"] if r["industry"] != industry]
    unresolved = audit["unresolved_codes"]
    m, n = len(local) + len(unresolved), len(peers)
    lower = median([*peers, *([-math.inf] * m)])
    upper = median([*peers, *([math.inf] * m)])
    up = sum(v > 0 for v in peers)
    return dict(status="bounded" if m else "complete", known_missing_codes=local,
                elsewhere_missing=elsewhere, unresolved_codes=unresolved,
                potential_missing_max=m, sample_count=n,
                median_lower=lower if math.isfinite(lower) else None,
                median_upper=upper if math.isfinite(upper) else None,
                up_pct_lower=100*up/(n+m), up_pct_upper=100*(up+m)/(n+m),
                basis="已知本行业缺行情加归属未知对象全部归入本行业，涨跌方向任意的最不利补全范围")


def decide(a, b):
    """两项核心广度指标的保守范围严格同向分离，才由程序决定；其它情况交LLM。"""
    sa, sb = a.get("sector_context") or {}, b.get("sector_context") or {}
    if (not sa.get("trade_date") or sa.get("trade_date") != sb.get("trade_date")
            or sa.get("industry") == sb.get("industry")
            or sa.get("source") != "daily_market.raw_data.market_strength"
            or sb.get("source") != sa.get("source")):
        return None
    ca, cb = sa.get("coverage_audit") or {}, sb.get("coverage_audit") or {}
    if ca.get("status") not in {"complete", "bounded"} or cb.get("status") not in {"complete", "bounded"}:
        return None
    for c in (ca, cb):
        for key in ("median_lower", "median_upper", "up_pct_lower", "up_pct_upper"):
            if type(c.get(key)) not in (int, float) or not math.isfinite(c[key]):
                return None
        if c["median_lower"] > c["median_upper"] or not 0 <= c["up_pct_lower"] <= c["up_pct_upper"] <= 100:
            return None
    winner = None
    if ca["median_lower"] > cb["median_upper"] and ca["up_pct_lower"] > cb["up_pct_upper"]:
        winner = "A"
    elif cb["median_lower"] > ca["median_upper"] and cb["up_pct_lower"] > ca["up_pct_upper"]:
        winner = "B"
    if winner is None:
        return None

    def ranges(s, c):
        return (f"{s['industry']}中位[{c['median_lower']:.2f},{c['median_upper']:.2f}]%、"
                f"上涨占比[{c['up_pct_lower']:.2f},{c['up_pct_upper']:.2f}]%")

    reason = (f"[程序·板块优先] A{ranges(sa, ca)}；B{ranges(sb, cb)}。"
              f"缺口最不利补全后{winner}两项仍严格占优，板块决定胜负；个股涨幅不能推翻。")
    return dict(winner=winner, reason=reason, method="robust_sector_priority")
