"""趋势观察名单的股东户数与已披露业绩卡。只展示事实，不参与筛选/排序/PK。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from analyzers.low_price_effect import _finite_float
from providers.tushare_provider import TushareProvider

MAX_CARDS = 12  # 有界取数；按代码选展示样本，无质量排名含义。
LOOKBACK_DAYS = 800


def _day(value):
    s = str(value or "")
    return datetime.strptime(s, "%Y%m%d" if len(s) == 8 else "%Y-%m-%d").date().isoformat()


def _visible(rows, code, day, *, income=False):
    """先限制公告可见性；财报复用既有版本优先级，户数取最新公告。"""
    if not isinstance(rows, list):
        raise ValueError("来源行格式非法")
    by_period, rejected, future = {}, 0, 0
    version_keys = {}
    seen_versions = {}
    suffix = "BJ" if code.startswith(("43", "82", "83", "87", "88", "89", "92")) else ("SH" if code.startswith(("60", "68", "90")) else "SZ")
    for row in rows:
        if not isinstance(row, dict) or row.get("ts_code") != f"{code}.{suffix}":
            raise ValueError("来源证券身份不匹配")
        try:
            end = _day(row.get("end_date"))
            ann = _day(row.get("ann_date"))
            if income and row.get("f_ann_date"):
                ann = max(ann, _day(row["f_ann_date"]))
        except (ValueError, TypeError):
            rejected += 1
            continue
        if ann > day or end > day:
            future += 1
            continue
        if ann < end:
            rejected += 1
            continue
        if income and not TushareProvider._financial_is_consolidated_statement(row.get("report_type")):
            continue  # 普通合并1、调整合并4；不混用母公司/单季/调整前报表。
        value = _finite_float(row.get("n_income_attr_p" if income else "holder_num"))
        if value is None or (not income and (value <= 0 or not value.is_integer())):
            rejected += 1
            continue
        item = dict(end_date=end, ann_date=ann, value=value)
        version = (ann,)
        if income:
            update_rank = TushareProvider._financial_update_flag_rank(row.get("update_flag"))
            report_rank = TushareProvider._financial_report_type_rank(row.get("report_type"))
            version = (update_rank, report_rank, ann)
            item.update(report_type=int(float(row["report_type"])), update_flag=update_rank)
        old = by_period.get(end)
        # 同一版本仍冲突时拒绝：不以源返回顺序任意选择金额。
        # 不同修订/报表类型是合法版本，不能误报成来源冲突。
        # 每个版本独立核对，即便该版本已被更高版本覆盖也不能隐藏冲突。
        # 否则同一批来源数据仅重排顺序就可能从 source_failed 变 complete。
        identity = (end, version)
        if identity in seen_versions and seen_versions[identity] != value:
            raise ValueError("同报告期同公告日期数据冲突")
        seen_versions[identity] = value
        if not old or version > version_keys[end]:
            by_period[end] = item
            version_keys[end] = version
    return sorted(by_period.values(), key=lambda r: r["end_date"]), rejected, future


def holder_summary(rows, code, day):
    visible, rejected, future = _visible(rows, code, day)
    base = dict(status="partial", invalid_rows=rejected, future_rows_excluded=future,
                latest=None, previous=None, change_pct=None)
    if visible:
        base["latest"] = visible[-1]
        base["age_days"] = (date.fromisoformat(day)-date.fromisoformat(visible[-1]["end_date"])).days
    if len(visible) >= 2:
        base["previous"] = visible[-2]
        base["change_pct"] = (visible[-1]["value"]/visible[-2]["value"]-1)*100
        base["status"] = "partial" if rejected else "complete"
    else:
        base["reason"] = "截至观察日不足两期有效股东户数"
    return base


def income_summary(rows, code, day):
    visible, rejected, future = _visible(rows, code, day, income=True)
    base = dict(status="partial", invalid_rows=rejected, future_rows_excluded=future,
                latest=None, prior_year=None, profit_change_pct=None, unit="元（归母净利润累计）")
    if not visible:
        base["reason"] = "截至观察日无有效已披露合并利润表"
        return base
    latest = visible[-1]
    base["latest"] = latest
    prior_end = str(int(latest["end_date"][:4])-1)+latest["end_date"][4:]
    prior = next((r for r in visible if r["end_date"] == prior_end), None)
    base["prior_year"] = prior
    if prior and prior["value"] != 0:
        base["profit_change_pct"] = (latest["value"]-prior["value"])/abs(prior["value"])*100
        base["status"] = "partial" if rejected else "complete"
    else:
        base["reason"] = "缺少上年同报告期或上年归母净利为0，同比未计算"
    return base


def collect(registry, code, day):
    start = (date.fromisoformat(day)-timedelta(days=LOOKBACK_DAYS)).isoformat()
    card = dict(code=code, trade_date=day, status="complete", lookback_days=LOOKBACK_DAYS,
                interpretation="户数变化不证明机构持仓或被套；已披露业绩不保证未来业绩")
    for key, method, summarize in (("holders", "get_stock_holder_numbers", holder_summary),
                                    ("income", "get_income_history", income_summary)):
        try:
            r = registry.call(method, code, start, day)
            if not r.success:
                raise ValueError(getattr(r, "error", None) or "来源调用失败")
            card[key] = {**summarize(r.data, code, day), "source": getattr(r, "source", method),
                         "fetched_at": getattr(r, "fetched_at", None)}
        except Exception as exc:
            card[key] = dict(status="source_failed", reason=str(exc))
        if card[key]["status"] != "complete":
            card["status"] = "partial"
    if all(card[k]["status"] == "source_failed" for k in ("holders", "income")):
        card["status"] = "source_failed"
    return card


def render(cards, coverage):
    lines = ["## 股东户数与已披露业绩 [事实·证据卡]",
             f"覆盖：{coverage['collected']}/{coverage['eligible']}；最多{MAX_CARDS}只，按代码取样仅供查找，不是质量排序。",
             "户数不定期披露；公告日之后才可引用；户数减少不证明机构被套，中报好不保证后续业绩。"]
    if coverage.get("not_collected"):
        lines.append("未采集："+"、".join(coverage["not_collected"]))
    for c in cards:
        name = str(c.get("name") or "").strip() or "名称待核验"
        lines += [f"### {name}（{c['code']}） · {c['status']}"]
        for key, title, change_key in (("holders", "股东户数", "change_pct"), ("income", "归母净利累计(元)", "profit_change_pct")):
            s = c[key]
            latest = s.get("latest")
            if not latest:
                lines.append(f"- {title}：{s['status']}；{s.get('reason', '数据不足')}")
                continue
            change = s.get(change_key)
            change_text = "未计算" if change is None else f"{change:+.2f}%"
            comparison = s.get("previous" if key == "holders" else "prior_year")
            lines.append(f"- {title}：{latest['value']:,.0f}；截止{latest['end_date']}／公告{latest['ann_date']}；"
                         f"{'较上期' if key == 'holders' else '同比(差额/上年绝对值)'}{change_text}；状态{s['status']}。")
            if comparison:
                lines.append(f"  对照：{comparison['value']:,.0f}；截止{comparison['end_date']}／公告{comparison['ann_date']}。")
            if s.get("reason") or s.get("invalid_rows"):
                lines.append(f"  缺口：{s.get('reason', '')}；无效记录{s.get('invalid_rows', 0)}。")
            lines.append(f"  来源：{s.get('source', '未核验')}。")
    return lines+[""]
