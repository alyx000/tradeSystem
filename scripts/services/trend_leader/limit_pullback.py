"""涨停后的回撤与再涨停证据；只复用扫描行情和已归档名单，不参与选股。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
import json
import math
import re
import sqlite3
from statistics import mean

from analyzers.theme_feedback import _check_limit_dates
from analyzers.strong_stock_feedback import _code, _row_code, _number as finite_number
from services.volume_concentration.capacity_history import _open_days
from utils.qfq import apply_qfq, OHLC_PRICE_KEYS

SCHEMA = "limit-pullback-v1"
WINDOW = 20
MA_WINDOWS = (5, 10, 20, 30)
NOTE = ("[事实] 来源：《10月31正课1》51:04—57:02。观察范围为趋势扫描主线门内、已取行情的当日涨停候选，"
        "包含未入池者，不代表全市场扫描。回看20个开放日及MA5/10/20/30为工程窗口，不是课程阈值。"
        "历史锚点取窗口内最近已核验涨停；归档缺失时不等于最近实际涨停。回撤使用锚点后、今日之前的最低价；"
        "基准为锚点前一根有效K线收盘价；不合并双板涨幅。阳线实体穿线按今日前复权开盘<均线<收盘计算。"
        "日线一价仅表示OHLC相等，不能核验盘中一字封单持续性；未识别洗盘意图、斜面形态或龙头身份。"
        "只展示事实，不给综合评分或买卖建议，不改变原趋势池。")


def missing(day, reason, status="source_failed"):
    return dict(schema=SCHEMA, trade_date=day, status=status, rows=[], gaps=[reason])


def capture(bars, factors, code, day, quote_source, factor_source):
    """取数时封存请求身份与来源；仅保留足够计算的尾部，先检查全序列重复/未来行。"""
    try:
        dates = [b["trade_date"] for b in bars]
        fd = [b["trade_date"] for b in factors]
        if dates != sorted(set(dates)) or not dates or dates[-1] != day or len(fd) != len(set(fd)):
            raise ValueError("行情日期错序/重复/错日或复权因子重复")
        if any(d > day for d in fd):
            raise ValueError("复权因子含未来日期")
        selected = deepcopy(bars[-50:])
        selected_dates = {b["trade_date"] for b in selected}
        return dict(code=code, trade_date=day, bars=selected,
                    factors=[deepcopy(f) for f in factors if f["trade_date"] in selected_dates],
                    quote_source=quote_source, factor_source=factor_source)
    except (TypeError, KeyError, ValueError, AttributeError):
        return dict(code=code, trade_date=day, error="行情/因子日期未通过核验")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("价格/因子不能是布尔值")
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise ValueError("价格/因子须有限且为正")
    return v


def _bars(item, code, day):
    if (item.get("code") != code or item.get("trade_date") != day
            or item.get("quote_source") != "tushare:daily"
            or item.get("factor_source") != "tushare:adj_factor" or item.get("error")):
        raise ValueError("行情/复权来源或请求身份未核验")
    bars, factors = deepcopy(item["bars"]), deepcopy(item["factors"])
    dates = [date.fromisoformat(b["trade_date"]).isoformat() for b in bars]
    fd = [date.fromisoformat(f["trade_date"]).isoformat() for f in factors]
    if (len(bars) < max(MA_WINDOWS) or dates != sorted(set(dates)) or dates[-1] != day
            or len(fd) != len(set(fd)) or set(fd) != set(dates)):
        raise ValueError("行情不足30根、日期重复/错序/错日或复权不完整")
    for b in bars:
        if _row_code(b if any(k in b for k in ('code', 'ts_code')) else {'code': code}) != _code(code):
            raise ValueError("行情股票身份错位")
        for k in OHLC_PRICE_KEYS:
            b[k] = _number(b[k])
        if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
            raise ValueError("OHLC关系非法")
    for f in factors:
        if any(k in f for k in ('code', 'ts_code')) and _row_code(f) != _code(code):
            raise ValueError("复权因子股票身份错位")
        f["adj_factor"] = _number(f["adj_factor"])
    pre_close = _number(bars[-1]["pre_close"])
    if abs((bars[-1]['close']/pre_close-1)*100-finite_number(bars[-1]['pct_chg'])) > .03:
        raise ValueError("当日价格、昨收与涨跌幅不一致")
    result = apply_qfq(bars, factors, keys=OHLC_PRICE_KEYS)
    if not result:
        raise ValueError("前复权计算失败")
    for b in result:
        for key in OHLC_PRICE_KEYS:
            _number(b[key])  # 有限正因子的比值仍可能上溢/下溢，结果必须再核验。
    return result, pre_close


def calculate(day, evidence):
    """来源原值重算；缺失不等于未出现，正向涨停事实不依赖名单缺席推断。"""
    result = dict(schema=SCHEMA, trade_date=day, status="partial", rows=[], gaps=[], evidence=evidence)
    try:
        date.fromisoformat(day)
        upstream = evidence.get('upstream_gaps', [])
        if not isinstance(upstream, list) or not all(isinstance(g,str) for g in upstream):
            raise ValueError('候选发现来源缺口格式非法')
        result['gaps'].extend('原扫描来源缺口：'+g for g in upstream)
        days = evidence["open_days"]
        if (len(days) != WINDOW or days != sorted(set(days)) or days[-1] != day
                or any(date.fromisoformat(d).isoformat() != d for d in days)):
            raise ValueError("20个开放日窗口未核验")
        current = {r["code"][:6]: r for r in _check_limit_dates(evidence["current_limit"], day)}
        historical = {}
        for d in days[:-1]:
            try:
                historical[d] = {r["code"][:6] for r in _check_limit_dates(evidence["history"].get(d), d)}
            except (ValueError, KeyError, TypeError, AttributeError):
                result["gaps"].append(d+"涨停归档未核验")
        candidates = evidence["candidates"]
        codes = [r["code"] for r in candidates]
        if (len(codes) != len(set(codes)) or any(not isinstance(c, str) or not re.fullmatch(r"\d{6}", c) for c in codes)):
            raise ValueError("候选身份重复或非法")
        for candidate in candidates:
            code = candidate["code"]
            row = dict(code=code, name=candidate["name"], status="partial", anchor_date=None)
            result["rows"].append(row)
            try:
                if code not in current or current[code]["name"] != candidate["name"]:
                    raise ValueError("当日非ST涨停身份/名称未核验")
                bars, pc = _bars(evidence["quotes"].get(code, {}), code, day)
                today = bars[-1]
                if not math.isclose(today['close'], today['high'], rel_tol=1e-8):
                    raise ValueError("涨停名单与收盘未在日高的行情矛盾")
                ma = {str(w): mean(b["close"] for b in bars[-w:]) for w in MA_WINDOWS}
                row.update(low_open=today["open"] < pc,
                           daily_one_price=today["high"] == today["low"],
                           bullish=today["close"] > today["open"],
                           crossed_ma=[int(w) for w,v in ma.items() if today["open"] < v < today["close"]],
                           ma_values=ma)
                anchor_dates = [d for d in days[:-1] if code in historical.get(d, set())]
                if not anchor_dates:
                    row.update(status="partial" if result["gaps"] else "complete",
                               reason="窗口内未见已核验历史涨停；不推断未知归档")
                    continue
                row['anchor_date'] = anchor_dates[-1]
                indexes = {b['trade_date']: i for i,b in enumerate(bars)}
                if anchor_dates[-1] not in indexes:
                    raise ValueError("最近已核验涨停日缺少K线，不能当作无历史涨停或回退旧锚点")
                i = indexes[anchor_dates[-1]]
                row["anchor_date"] = bars[i]["trade_date"]
                middle = bars[i+1:-1]
                if not middle or i == 0:
                    row.update(reason="锚点后无中间K线或缺锚点前收盘，回撤未计算")
                    continue
                base, high = bars[i-1]["close"], bars[i]["high"]
                low = min(b["low"] for b in middle)
                row.update(status="partial" if result["gaps"] else "complete",
                           between_bars=len(middle), anchor_pre_close=base, anchor_high=high,
                           pullback_low=low, retraced_to_pre_limit=low <= base,
                           drawdown_from_anchor_high_pct=(low/high-1)*100,
                           close_vs_anchor_high_pct=(today["close"]/high-1)*100,
                           regained_anchor_high=today["close"] >= high,
                           reason="最近已核验单板锚点的回撤事实；不判定完整课程形态")
            except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
                row.update(status="source_failed", reason=str(exc))
        for row in result["rows"]:
            if row["status"] != "complete":
                result["gaps"].append(row["code"]+"："+row.get("reason", "历史归档不完整"))
        result["status"] = "partial" if result["gaps"] else "complete"
        return result
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError) as exc:
        return missing(day, str(exc))


def collect(conn, day, current_result, candidates, quotes, *, source_errors=None):
    """既有扫描尾部只读；不补行情/历史名单，不触发任何写入。"""
    try:
        days = _open_days(conn, day, WINDOW)
        if not getattr(current_result, "success", False) or not isinstance(current_result.data, dict):
            raise ValueError("当日涨停来源失败")
        current = {**deepcopy(current_result.data), "_source": getattr(current_result, "source", None)}
        history = {}
        for d in days[:-1]:
            r = conn.execute("SELECT raw_data FROM daily_market WHERE date=?", (d,)).fetchone()
            try:
                raw = json.loads(r[0]) if r else {}
                if any(raw[k] != d for k in ('date', 'trade_date') if k in raw):
                    raise ValueError('历史信封日期错位')
                source = raw['raw_data'] if 'raw_data' in raw else raw
                history[d] = source.get("limit_up")
            except (ValueError, TypeError, AttributeError):
                history[d] = None
        computed = calculate(day, dict(open_days=days, current_limit=current, history=history,
                                       candidates=deepcopy(candidates), quotes=deepcopy(quotes),
                                       upstream_gaps=list(source_errors or [])))
        return validate(computed, day)
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as exc:
        return missing(day, "形态证据来源失败："+str(exc))


def validate(payload, day):
    try:
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("trade_date") != day:
            return missing(day, "涨停回撤证据未计算", "not_computed")
        # 新卡坏原值不能让既有review.json的严格序列化或旧模块校验失败。
        json.dumps(payload, allow_nan=False)
        gaps = payload.get('gaps')
        if not isinstance(gaps, list) or not all(isinstance(g, str) for g in gaps):
            raise ValueError('形态缺口字段格式非法')
        if "evidence" not in payload:
            return missing(day, "；".join(gaps) or "形态原始证据缺失",
                           "not_computed" if payload.get("status") == "not_computed" else "source_failed")
        computed = calculate(day, payload["evidence"])
        if any(payload.get(k) != computed.get(k) for k in ("status", "rows", "gaps")):
            return missing(day, "形态汇总与来源重算不一致")
        json.dumps(computed, allow_nan=False)
        return computed
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return missing(day, '形态原始证据损坏；仅降级本卡')


def lines(payload, day):
    p = validate(payload, day)
    out = ["涨停回撤与再涨停证据 [事实]", "状态："+p["status"]]
    flag = lambda v: "未计算" if v is None else "是" if v else "否"
    for r in p["rows"]:
        text = (f'{r["name"]} {r["code"]}：{r["status"]}；历史锚点{r.get("anchor_date") or "未核验"}；'
                f'低开{flag(r.get("low_open"))}；日线一价{flag(r.get("daily_one_price"))}；'
                '阳线实体穿线'+('/'.join('MA'+str(w) for w in r.get('crossed_ma', [])) or '未见/未计算'))
        if "retraced_to_pre_limit" in r:
            text += (f'；中间{r["between_bars"]}根K线；回到涨停前收盘{flag(r["retraced_to_pre_limit"])}；'
                     f'相对锚点最高价最大回撤{r["drawdown_from_anchor_high_pct"]:+.2f}%；'
                     f'今日收盘相对锚点高点{r["close_vs_anchor_high_pct"]:+.2f}%')
        out.append(text+'；'+r.get('reason', '历史证据不完整'))
    if p['gaps']:
        out.append('缺口：'+'；'.join(p['gaps']))
    out.append(NOTE)
    return out, p['gaps']


def render_html(payload, day):
    text, gaps = lines(payload, day)
    checked = validate(payload, day)
    count = len(checked['rows'])
    known = [r for r in checked['rows'] if 'retraced_to_pre_limit' in r]
    overview = (f'[事实] 观察{count}只；可核验历史回撤{len(known)}只。' if count else
                '[事实] 本次主线门内无当日涨停观察对象。' if checked['status'] == 'complete' else
                '[事实] 尚无可展示的已核验证据。')
    html = ('<h4>'+escape(text[0])+'</h4><p>'+overview+'</p>'
            f'<details class="evidence" data-as-of="{escape(day)}" data-items="{max(1,count)}" '
            'data-evidence-kind="limit-pullback"><summary>'+f'展开{max(1,count)}项证据与来源说明</summary>'
            + ''.join('<p>'+escape(t)+'</p>' for t in text[1:])+'</details>')
    return html, gaps
