"""低位连续堆量证据（teacher_notes#1178）；只读派生，不参与池状态转换。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
import json
import math
import re
from statistics import mean

from services.trend_leader import observations, sector_sample
from utils.qfq import apply_qfq, OHLC_PRICE_KEYS

SCHEMA = "low-volume-cluster-v1"
PARAMETERS = dict(history_bars=120, position_window=60, peak_window=45, prior_low_window=20,
                  rebound_min_pct=8, cluster_bars=5, bullish_min=3,
                  volume_ratio_min=1.2, low_position_max=.35,
                  drawdown_min_pct=20, cluster_age_max=20, pullback_ratio_max=.75)
LABELS = {"not_observed": "窗口内未见完整结构", "cluster_observed": "低位连续堆量",
          "pullback_observed": "堆量后缩量回踩", "above_prior_peak": "收盘超过前次反弹高点",
          "invalidated": "堆量后跌破结构低点", "uncomputed": "待核验"}
NOTE = ("来源：鞠磊2026-11-10课程，teacher_notes#1178。仅覆盖本次趋势扫描已请求行情的"
        "主线门内候选及原在池对象（含未入池、当日退池者），不是全市场提前发现。"
        "120根行情、60根位置窗、45根反弹锚点窗、20根前低窗、反弹≥8%、5根内至少3根阳线且每根阳线量"
        "≥前次反弹均量1.2倍、5根均量≥1.2倍、位置≤35%且距窗口高点≥20%、"
        "回踩最近最多3根均量≤堆量均量75%均为工程口径。K线根数不是连续开放日；"
        "未核实中间停牌或漏数。比较段跨复权因子变化时量能待核验。"
        "板块仅采用同日申万二级成交额及份额，不代替概念题材、不证明机构入场；"
        "形态是待验证观察，不是必过前高、买卖信号或收益评分。")


def missing(day, reason, status="source_failed"):
    return dict(schema=SCHEMA, trade_date=day, status=status, rows=[],
                coverage=dict(requested=None, price_volume_verified=None, sector_verified=None), gaps=[reason])


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("日期非法")
    return value


def _number(value, positive=True):
    if isinstance(value, bool):
        raise ValueError("数值不能为布尔值")
    value = float(value)
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError("数值非有限或范围非法")
    return value


def capture(bars, factors, code, day, quote_source, factor_source):
    """封存既有请求，不增加取数；先检查全序列，再保留尾部120根。"""
    try:
        dates = [_day(b["trade_date"]) for b in bars]
        fd = [_day(f["trade_date"]) for f in factors]
        if (not dates or dates != sorted(set(dates)) or dates[-1] != day
                or len(fd) != len(set(fd)) or any(d > day for d in fd)):
            raise ValueError("行情或因子日期重复、错序、未来或陈旧")
        # 只封存重算必需字段；无关的amount/pct_chg等可能含NaN，不得污染整个日报序列化。
        quote_keys = ("trade_date", "code", "ts_code", *OHLC_PRICE_KEYS, "vol")
        factor_keys = ("trade_date", "code", "ts_code", "adj_factor")
        selected = [{k: b[k] for k in quote_keys if k in b} for b in bars[-PARAMETERS["history_bars"]:]]
        needed = {b["trade_date"] for b in selected}
        captured = dict(code=code, trade_date=day, quote_source=quote_source, factor_source=factor_source,
                        bars=selected, factors=[{k: f[k] for k in factor_keys if k in f}
                                                for f in factors if f["trade_date"] in needed])
        json.dumps(captured, allow_nan=False)
        return deepcopy(captured)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return dict(code=code, trade_date=day, error=str(exc))


def _prepare(item, code, day):
    if (item.get("code") != code or item.get("trade_date") != day or item.get("error")
            or item.get("quote_source") != "tushare:daily"
            or item.get("factor_source") != "tushare:adj_factor"):
        raise ValueError("行情/复权来源或请求身份未核验")
    bars, factors = deepcopy(item["bars"]), deepcopy(item["factors"])
    dates = [_day(b["trade_date"]) for b in bars]
    fd = [_day(f["trade_date"]) for f in factors]
    if (len(bars) != PARAMETERS["history_bars"] or dates != sorted(set(dates))
            or dates[-1] != day or len(fd) != len(set(fd)) or set(fd) != set(dates)):
        raise ValueError("不足120根有效行情、日期错位或复权缺失")
    for row in bars + factors:
        for key in ("code", "ts_code"):
            if key in row and str(row[key]).split(".")[0] != code:
                raise ValueError("行情或因子股票身份错位")
    for b in bars:
        for key in OHLC_PRICE_KEYS:
            b[key] = _number(b[key])
        b["vol"] = _number(b["vol"])
        if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
            raise ValueError("OHLC关系非法")
    for f in factors:
        f["adj_factor"] = _number(f["adj_factor"])
    adjusted = apply_qfq(bars, factors, keys=OHLC_PRICE_KEYS)
    if not adjusted:
        raise ValueError("前复权计算失败")
    for b in adjusted:
        for key in OHLC_PRICE_KEYS:
            _number(b[key])
    by_date = {f["trade_date"]: f["adj_factor"] for f in factors}
    return adjusted, [by_date[d] for d in dates]


def analyze(bars, factors):
    """确定性锚点：最近已确认反弹峰→其后新低→首次合格5根堆量；不倒推赢家。"""
    n, p = len(bars), PARAMETERS
    # 反弹峰用左右各2根确认；候选峰之前已有20根前低样本。
    for peak in range(n - p["cluster_bars"] - 2, n - p["peak_window"] - 1, -1):
        high = bars[peak]["high"]
        if (high <= max(b["high"] for b in bars[peak-2:peak])
                or high < max(b["high"] for b in bars[peak+1:peak+3])):
            continue
        prior = min(range(peak-p["prior_low_window"], peak), key=lambda i: (bars[i]["low"], -i))
        if (high / bars[prior]["low"] - 1)*100 < p["rebound_min_pct"]:
            continue
        # 从峰确认后开始找新低。每个峰固定首次合格堆量，避免滚动窗口改写回踩基准。
        for start in range(peak+3, n-p["cluster_bars"]+1):
            low = min(range(peak+1, start), key=lambda i: (bars[i]["low"], -i))
            if bars[low]["low"] >= bars[prior]["low"]:
                continue
            end = start+p["cluster_bars"]-1
            cluster = bars[start:end+1]
            if min(b["low"] for b in cluster) < bars[low]["low"]:
                continue
            # 位置只用堆量结束时已可见的历史，不包含后来的涨跌。
            visible = bars[end+1-p["position_window"]:end+1]
            floor, ceiling = min(b["low"] for b in visible), max(b["high"] for b in visible)
            top = max(b["high"] for b in cluster)
            position = (top-floor)/(ceiling-floor) if ceiling > floor else 1.
            drawdown = (1-top/ceiling)*100
            if position > p["low_position_max"] or drawdown < p["drawdown_min_pct"]:
                continue
            bulls = [b for b in cluster if b["close"] > b["open"]]
            if len(bulls) < p["bullish_min"]:
                continue
            # 因子变化不能把原始成交量的不可比误判成“未出现堆量”。
            if len(set(factors[prior:])) != 1:
                return dict(state="uncomputed", price_volume_status="partial",
                            reason="比较段跨复权因子变化，原始成交量可比性未核验")
            reference = mean(b["vol"] for b in bars[prior:peak+1])
            cluster_mean = mean(b["vol"] for b in cluster)
            if (min(b["vol"] for b in bulls)/reference < p["volume_ratio_min"]
                    or cluster_mean/reference < p["volume_ratio_min"]):
                continue
            if n-1-end > p["cluster_age_max"]:
                break  # 不用同一峰后更晚的窗口刷新一个陈旧堆量事件。
            after = bars[end+1:]
            broken = next((b["trade_date"] for b in after if b["low"] < bars[low]["low"]), None)
            pullback_ratio = mean(b["vol"] for b in after[-3:])/cluster_mean if after else None
            crossed = bars[-1]["close"] > high
            shrink = bool(after and bars[-1]["close"] < top and pullback_ratio <= p["pullback_ratio_max"])
            state = ("invalidated" if broken else "above_prior_peak" if crossed else
                     "pullback_observed" if shrink else "cluster_observed")
            return dict(state=state, price_volume_status="complete", prior_low_date=bars[prior]["trade_date"],
                        rebound_peak_date=bars[peak]["trade_date"], rebreak_low_date=bars[low]["trade_date"],
                        cluster_start=bars[start]["trade_date"], cluster_end=bars[end]["trade_date"],
                        cluster_age_bars=n-1-end, bullish_bars=len(bulls),
                        reference_volume=reference, cluster_mean_volume=cluster_mean,
                        cluster_volume_ratio=cluster_mean/reference, low_position=position,
                        drawdown_from_window_high_pct=drawdown, pullback_volume_ratio=pullback_ratio,
                        close_vs_prior_peak_pct=(bars[-1]["close"]/high-1)*100,
                        invalidated_date=broken)
    return dict(state="not_observed", price_volume_status="complete")


def calculate(day, evidence):
    try:
        _day(day)
        # 即使传入旧/损坏快照，子块也只能返回可严格序列化的证据或失败收据。
        json.dumps(evidence, allow_nan=False)
        candidates, quotes = evidence["candidates"], evidence["quotes"]
        if not isinstance(candidates, list) or not isinstance(quotes, dict) or len(candidates) > 1000:
            raise ValueError("观察名单格式非法")
        codes = [r["code"] for r in candidates]
        if (len(codes) != len(set(codes)) or any(not isinstance(c, str) or not re.fullmatch(r"\d{6}", c) for c in codes)
                or set(quotes) - set(codes)):
            raise ValueError("观察身份重复、非法或与缓存不符")
        upstream = evidence.get("upstream_gaps", [])
        if not isinstance(upstream, list) or not all(isinstance(g, str) for g in upstream):
            raise ValueError("扫描来源缺口格式非法")
        sector = evidence.get("sector_block")
        if sector is not None:
            sector = observations.validate_sector(sector, day)
        rows, gaps = [], ["原扫描来源缺口："+g for g in upstream]
        samples, industries, sample_coverage = {}, {}, {}
        if evidence.get("sector_sample_basis") == "both-days-quoted-v1":
            try:
                if not sector or sector["status"] != "partial":
                    raise ValueError("部分覆盖行业块不可用")
                samples, industries, sample_coverage = sector_sample.calculate(sector, day)
                gaps.append("板块原始覆盖为partial；共同样本只用于成交额对照")
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                gaps.append("行业共同样本待核验："+str(exc))
        for candidate in candidates:
            code = candidate["code"]
            if not all(isinstance(candidate.get(k), str) and candidate[k] for k in ("name", "industry")):
                raise ValueError("观察名称或行业缺失")
            support = observations.sector_support(sector, candidate["industry"], day)
            row = dict(code=code, name=candidate["name"], industry=candidate["industry"], sector_support=support)
            if industries.get(code) == candidate["industry"] and candidate["industry"] in samples:
                row["sector_sample"] = samples[candidate["industry"]]
            try:
                bars, factors = _prepare(quotes.get(code, {}), code, day)
                row.update(analyze(bars, factors))
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as exc:
                row.update(state="uncomputed", price_volume_status="partial", reason=str(exc))
            row["status"] = "complete" if row["price_volume_status"] == "complete" and support["supported"] is not None else "partial"
            if row["status"] != "complete":
                gap = "全行业支持待核验" if row.get("sector_sample") else "板块背景待核验"
                gaps.append(code+"："+(row.get("reason") or gap))
            rows.append(row)
        result = dict(schema=SCHEMA, trade_date=day, status="partial" if gaps else "complete", rows=rows, gaps=gaps,
                    parameters=deepcopy(PARAMETERS), evidence=deepcopy(evidence),
                    coverage=dict(requested=len(rows), price_volume_verified=sum(r["price_volume_status"] == "complete" for r in rows),
                                  sector_verified=sum(r["sector_support"]["supported"] is not None for r in rows)))
        if sample_coverage:
            result["sector_sample_coverage"] = sample_coverage
            result["coverage"]["sector_sample_available"] = sum("sector_sample" in r for r in rows)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return missing(day, str(exc))


def collect(day, candidates, quotes, sector, upstream):
    evidence = dict(candidates=candidates, quotes=quotes, sector_block=sector, upstream_gaps=upstream)
    if isinstance(sector, dict) and sector.get("status") == "partial":
        evidence["sector_sample_basis"] = "both-days-quoted-v1"
    return calculate(day, evidence)


def validate(payload, day):
    if payload is None:
        return missing(day, "旧快照未计算低位连续堆量", "not_computed")
    try:
        if payload.get("schema") != SCHEMA or payload.get("trade_date") != day:
            raise ValueError("堆量证据日期/版本错位")
        if "evidence" not in payload:
            if payload.get("status") in {"not_computed", "source_failed"}:
                return missing(day, "；".join(payload["gaps"]), payload["status"])
            raise ValueError("缺少堆量原始证据")
        recomputed = calculate(day, payload["evidence"])
        keys = ("schema", "trade_date", "status", "parameters", "rows", "coverage", "gaps", "sector_sample_coverage")
        encode = lambda p: json.dumps({k: p.get(k) for k in keys}, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if encode(payload) != encode(recomputed):
            raise ValueError("堆量结果与来源重算不符")
        return recomputed
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return missing(day, str(exc))


def _row_text(r):
    support = r["sector_support"]
    sector = (f'{r["industry"]}：成交额变化{support["delta_yi"]:+.2f}亿元、份额{support["share_delta_pp"]:+.2f}个百分点'
              if support["supported"] is not None else f'{r["industry"]}：板块背景待核验')
    sample = r.get("sector_sample")
    if sample:
        sector = (f'{r["industry"]}：两日共同有行情{sample["member_count"]}只，成交额'
                  f'{sample["previous_amount_yi"]:.2f}→{sample["amount_yi"]:.2f}亿元'
                  f'（{sample["delta_yi"]:+.2f}亿）；全行业支持待核验')
    text = f'{r["name"]} {r["code"]}：[判断] {LABELS[r["state"]]}；[事实] {sector}'
    if "cluster_start" in r:
        text += (f'；堆量{r["cluster_start"]}至{r["cluster_end"]}，5根中{r["bullish_bars"]}根放量阳线，'
                 f'均量为前次反弹{r["cluster_volume_ratio"]:.2f}倍；前次反弹高点日{r["rebound_peak_date"]}，'
                 f'再破低点日{r["rebreak_low_date"]}；收盘相对前高{r["close_vs_prior_peak_pct"]:+.2f}%')
        if r["pullback_volume_ratio"] is not None:
            text += f'；后续最近最多3根均量/堆量均量{r["pullback_volume_ratio"]:.2f}'
        if r["invalidated_date"]:
            text += f'；结构低点跌破日{r["invalidated_date"]}'
    if r.get("reason"):
        text += '；待核验：'+r["reason"]
    return text


def _overview(p):
    coverage = p["coverage"]
    if coverage["requested"] is None:
        return "低位连续堆量待核验："+"；".join(p["gaps"])
    count = sum(r["state"] not in {"uncomputed", "not_observed", "invalidated"} for r in p["rows"])
    verdict = (f'已核验对象中{count}只保留堆量结构。' if coverage["price_volume_verified"] else
               '本次无观察对象。' if coverage["requested"] == 0 and not p["gaps"] else '堆量结论待核验。')
    sample_summary = ""
    if p.get("sector_sample_coverage"):
        sample_summary = (f'；另有{coverage["sector_sample_available"]}只可查行业共同样本。'
                          + "；".join(f'{d}沪深缺行情中已核全天停牌{len(s["verified_full_day"])}只、'
                                      f'原因待核{len(s["unexplained"])}只'
                                      for d, s in sorted(p["sector_sample_coverage"].items())))
    return (f'[事实] 观察{coverage["requested"]}只；量价可核验{coverage["price_volume_verified"]}只，'
            f'板块可核验{coverage["sector_verified"]}只{sample_summary}。[判断] {verdict}'
            + ("其余缺口待核验。" if p["gaps"] else ""))


def render_markdown(payload, day):
    p = validate(payload, day)
    safe = lambda text: escape(text).replace("|", "&#124;").replace("\n", " ")
    return ["## 低位连续堆量与板块背景", safe(_overview(p)), "",
            *[safe(_row_text(r)) for r in p["rows"]], "", safe(NOTE)]


def render_html(payload, day):
    p = validate(payload, day)
    rows = p["rows"]
    visible = [r for r in rows if r["state"] not in {"not_observed", "uncomputed"}][:3]
    text = '<h4>低位连续堆量与板块背景</h4><p>'+escape(_overview(p))+'</p>'
    text += ''.join('<p>'+escape(_row_text(r))+'</p>' for r in visible)
    text += (f'<details class="evidence" data-as-of="{escape(day)}" data-items="{max(1,len(rows))}" '
             f'data-evidence-kind="volume-cluster"><summary>展开{max(1,len(rows))}项观察、口径与来源</summary>')
    text += ''.join('<p>'+escape(_row_text(r))+'</p>' for r in rows)
    text += '<p>'+escape(NOTE)+'</p>'
    if p["gaps"]:
        text += '<p>缺口：'+escape('；'.join(p["gaps"]))+'</p>'
    return text+'</details>', p["gaps"]
