"""Compare verified Kaipanla ETF snapshots and render the ETF section."""

import csv
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path


BUY = "连续净申购"
SELL = "连续净赎回"


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _amount(raw):
    if not raw or raw == "--":
        return None
    for unit, multiplier in (("亿", 100_000_000), ("万", 10_000), ("元", 1)):
        if raw.endswith(unit):
            try:
                value = Decimal(raw[:-len(unit)].replace(",", "")) * multiplier
            except InvalidOperation as error:
                raise ValueError(f"ETF金额格式无法核验 {raw!r}") from error
            if value.is_finite():
                return value
    raise ValueError(f"ETF金额格式无法核验 {raw!r}")


def _one(values, field):
    values = {value for value in values if value}
    if len(values) > 1:
        raise ValueError(f"ETF {field} 记录不一致 {sorted(values)}")
    return next(iter(values), "")


def _keyed(rows, field):
    result = {row[field]: row for row in rows}
    if "" in result or len(result) != len(rows):
        raise ValueError(f"ETF {field} 为空或重复")
    return result


def _snapshot(directory):
    location = Path(directory)
    market = _read(location / "etf-market.csv")
    flows = _read(location / "etf-flows.csv")
    motion = _read(location / "etf-trends.csv")
    meta = location / "etf-meta.csv"
    if not meta.is_file():
        meta = location / "ui-resume-evidence" / "etf-meta.csv"
    metadata = _keyed(_read(meta), "view") if meta.is_file() else {}
    fact_date = _one((row["date"] for row in [*market, *flows, *motion, *metadata.values()]), "事实日期")
    date.fromisoformat(fact_date)

    main_flows = [row for row in flows if "跨境T+2" not in row["disclosure_status"]]
    flow_date = _one((row["flow_date"] for row in main_flows), "申赎金额日期")
    references = [row for row in flows if "跨境T+2" in row["disclosure_status"]]
    reference_date = _one((row["flow_date"] for row in references), "跨境参考日期")
    for data_day in (flow_date, reference_date):
        if data_day and date.fromisoformat(data_day) > date.fromisoformat(fact_date):
            raise ValueError(f"ETF申赎日期超出事实日 {data_day}")

    update = _one((row["updated_at"] for row in motion), "动向更新时间")
    if update and datetime.fromisoformat(update).date() > date.fromisoformat(fact_date):
        raise ValueError(f"ETF动向更新时间超出事实日 {update}")
    for row in motion:
        if row["bucket"] not in {BUY, SELL}:
            raise ValueError(f"ETF动向榜别未知 {row['bucket']}")
        amount = _amount(row["interval_flow_raw"])
        if amount is not None and ((row["bucket"] == BUY and amount <= 0) or
                                   (row["bucket"] == SELL and amount >= 0)):
            raise ValueError(f"ETF动向金额和标签冲突 {row['name']}")

    directions = _keyed([row for row in main_flows if row["view"] == "方向汇总"], "name")
    return {
        "date": fact_date, "market": market, "flows": flows, "motion": _keyed(motion, "name"),
        "meta": metadata, "directions": directions, "flow_date": flow_date,
        "reference_date": reference_date, "update": update,
    }


def _same_count(before, after):
    previous_count = before.get("constituent_count", "")
    return previous_count.isdecimal() and int(previous_count) > 0 and previous_count == after.get("constituent_count")


def _flow_comparisons(current, previous):
    before_date = previous.get("flow_date", "")
    if not before_date or not current["flow_date"] or not before_date < current["flow_date"] <= current["date"]:
        return []
    old = previous["directions"]
    result = []
    for name, row in current["directions"].items():
        prior = old.get(name)
        if not prior:
            continue
        before_value = _amount(prior["flow_amount_raw"])
        after_value = _amount(row["flow_amount_raw"])
        if not row["scope"] or row["scope"] != prior["scope"]:
            status = "筛选范围变化"
        elif not _same_count(prior, row):
            status = "基金只数变化或未核实"
        elif before_value is None or after_value is None or not all(
            "境内已到披露窗口" in item["disclosure_status"] for item in (prior, row)
        ):
            status = "金额或披露范围不足"
        elif any(marker in name for marker in ("港股", "恒生", "香港", "跨境")):
            status = "跨境以披露参考为准"
        elif before_value * after_value < 0:
            status = "净申购转净赎回" if after_value < 0 else "净赎回转净申购"
        else:
            status = "同范围可比"
        result.append({
            "name": name, "status": status, "date_1": before_date, "date_2": row["flow_date"],
            "amount_1": prior["flow_amount_raw"], "amount_2": row["flow_amount_raw"],
            "count_1": prior["constituent_count"], "count_2": row["constituent_count"],
            "initial_1": "初值" if "初值" in prior["disclosure_status"] else "",
            "initial_2": "初值" if "初值" in row["disclosure_status"] else "",
            "scope_1": prior["scope"], "scope_2": row["scope"],
        })
    return result


def _motion_changes(current, previous, flow_comparisons):
    scope = current["meta"].get("动向", {}).get("scope")
    old_scope = previous.get("meta", {}).get("动向", {}).get("scope")
    previous_rows = previous.get("motion", {})
    comparable = bool(scope and old_scope == scope and previous["date"] < current["date"])
    comparable = bool(comparable and previous.get("update") and current["update"]
                      and datetime.fromisoformat(previous["update"]) < datetime.fromisoformat(current["update"]))
    old = previous_rows if comparable else {}
    verified_turns = {row["name"]: row["status"] for row in flow_comparisons
                      if row["status"] in {"净申购转净赎回", "净赎回转净申购"}}
    changes = []
    for name in dict.fromkeys([*current["motion"], *old]):
        before = old.get(name, {})
        after = current["motion"].get(name, {})
        before_bucket, after_bucket = before.get("bucket", ""), after.get("bucket", "")
        if not comparable:
            classification = "当日榜单，缺可比基线"
        elif not before:
            classification = f"新进{after_bucket}榜"
        elif not after:
            classification = f"退出{before_bucket}榜"
        elif before_bucket != after_bucket:
            classification = f"由{before_bucket}榜转至{after_bucket}榜"
        else:
            classification = f"仍在{after_bucket}榜"
        matching_count = _same_count(before, after)
        if before and after and (before.get("updated_at") != previous.get("update")
                                 or after.get("updated_at") != current.get("update")):
            scope_note = "行更新时间未核实"
        elif before and after:
            scope_note = "只数一致" if matching_count else "只数变化或未核实"
        else:
            scope_note = ""
        changes.append({
            "name": name,
            "classification": classification,
            "bucket_1": before_bucket,
            "bucket_2": after_bucket,
            "updated_at_1": previous.get("update", "") if before else "",
            "updated_at_2": current["update"] if after else "",
            "count_1": before.get("constituent_count", ""),
            "count_2": after.get("constituent_count", ""),
            "scope_note": scope_note,
            "days_1": before.get("duration_days", ""),
            "days_2": after.get("duration_days", ""),
            "return_1": before.get("interval_change_pct_raw", ""),
            "return_2": after.get("interval_change_pct_raw", ""),
            "flow_1": before.get("interval_flow_raw", ""),
            "flow_2": after.get("interval_flow_raw", ""),
            "verified_daily_turn": verified_turns.get(name, ""),
        })
    return changes


def _short_date(value):
    day = date.fromisoformat(value)
    return f"{day.month}/{day.day}"


def _fund_direction(name):
    return name.partition("ETF")[0].strip() or name


def _market_summary(market):
    parts = []
    for bucket, title in (("涨幅高", "涨幅"), ("涨幅低", "跌幅")):
        rows = [row for row in market if row["bucket"] == bucket]
        if not rows:
            continue
        direction, count = Counter(_fund_direction(row["name"]) for row in rows).most_common(1)[0]
        if count > 1:
            parts.append(f"{title}前{len(rows)}有{count}只{direction}ETF")
        else:
            parts.append(f"{title}榜首{rows[0]['name']}{rows[0]['change_pct_raw']}")
    return "，".join(parts) if parts else "榜单经核验为空"


def _pct_value(raw):
    if not raw or not raw.endswith("%"):
        return None
    try:
        value = Decimal(raw[:-1])
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _motion_summary(changes, update, *, limit):
    # 只在相邻两榜都给出同范围、同方向、同只数、完整原值时展示变化。
    ranked = []
    for bucket in (BUY, SELL):
        candidates = []
        for row in changes:
            if (row["bucket_1"] != bucket or row["bucket_2"] != bucket
                    or row["scope_note"] != "只数一致"
                    or not all(row["days_" + n].isdecimal() for n in ("1", "2"))):
                continue
            first, last = (_pct_value(row["return_" + n]) for n in ("1", "2"))
            before, after = (_amount(row["flow_" + n]) for n in ("1", "2"))
            if None in (first, last, before, after):
                continue
            # 优先说明申赎与价格背离扩大；同类取累计金额变化较大者。
            side = 1 if bucket == BUY else -1
            widening = side * last < 0 and side * (last - first) < 0
            priority = (widening, abs(last - first) if widening else 0, abs(after - before))
            candidates.append((priority, row, side * last))
        if candidates:
            ranked.append(max(candidates, key=lambda item: item[0]))
    if ranked:
        parts = []
        ranked.sort(key=lambda item: item[0], reverse=True)
        for _, row, direction in ranked[:limit]:
            relation = "背离" if direction < 0 else "同向" if direction > 0 else "持平"
            parts.append(f"{row['name']}{row['bucket_2']}{row['days_1']}→{row['days_2']}日，"
                         f"累计{row['flow_1']}→{row['flow_2']}，区间涨跌{row['return_1']}→{row['return_2']}（{relation}）")
        before = ranked[0][1]["updated_at_1"]
        return "动向" + before[5:16].replace("-", "/") + "→" + update[5:16].replace("-", "/") + "，" + "；".join(parts)
    # 只有一期或部分字段缺失，照实显示可见原值，避免把缺项当作零或趋势。
    for row in changes:
        if not row["bucket_2"] or not row["days_2"].isdecimal():
            continue
        amount = row["flow_2"] if _amount(row["flow_2"]) is not None else "未披露"
        price = row["return_2"] if _pct_value(row["return_2"]) is not None else "未披露"
        return (f"动向{update[5:16].replace('-', '/')}，{row['name']}{row['bucket_2']}{row['days_2']}日，"
                f"累计{amount}，区间涨跌{price}（本期值）")
    return "动向" + update[5:16].replace("-", "/") + "，本期无可展示记录"


def _summary(current, changes, comparisons):
    pieces = [f"{_short_date(current['date'])}行情{_market_summary(current['market'])}"]
    turns = [row for row in comparisons if row["status"] in {"净申购转净赎回", "净赎回转净申购"}]
    comparable = [row for row in comparisons if row["status"] == "同范围可比"]
    if turns or comparable:
        row = max(turns or comparable, key=lambda item: abs(
            _amount(item["amount_2"]) - _amount(item["amount_1"])
        ))
        name = row["name"].removesuffix("ETF")
        pairs = [(row["date_" + n], row["amount_" + n] + row.get("initial_" + n, ""))
                 for n in ("0", "1", "2") if row.get("date_" + n)]
        dates = "→".join(_short_date(day) for day, _ in pairs)
        amounts = "→".join(value for _, value in pairs)
        status = row["status"]
        if status == "同范围可比":
            start, end = _amount(row["amount_1"]), _amount(row["amount_2"])
            if end == start:
                status = "净额持平"
            elif start and end and start * end > 0:
                status = ("净申购" if end > 0 else "净赎回") + ("扩大" if abs(end) > abs(start) else "收窄")
            else:
                status = "净额变化"
        pieces.append(f"{dates}申赎{name}{amounts}，最近两期{status}")
    elif current["flow_date"]:
        pieces.append(f"申赎金额列{_short_date(current['flow_date'])}")
    if current["update"]:
        pieces.append(_motion_summary(changes, current["update"], limit=1 if turns or comparable else 2))
    if current["reference_date"]:
        pieces.append(f"跨境参考{_short_date(current['reference_date'])}")
    return "。".join(pieces) + "。"


def _table(title, columns, rows):
    headings = "".join(f'<th scope="col">{escape(column)}</th>' for column in columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(cell or '—')}</td>" for cell in cells) + "</tr>" for cells in rows)
    return (f'<h3>{escape(title)}</h3><div class="table-wrap"><table>'
            f"<thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></div>")


def _details(current, previous, changes, comparisons):
    coverage = [
        f"行情 {current['date']}",
        f"申赎金额列 {current['flow_date'] or '未显示'}",
        f"跨境回读参考 {current['reference_date'] or '未显示'}",
        f"连续动向更新时间 {current['update'] or '未显示'}",
    ]
    if previous:
        coverage.append(f"动向对照报告日 {previous['date']}")
    details = ["<p>" + escape("。".join(coverage) + "。") + "</p>"]
    details.extend(f"<p>{escape(view)}（{escape(row['scope'])}）{escape(row['boundary'])}</p>"
                   for view, row in current["meta"].items())
    details.append("<p>涨跌幅和申赎榜单记录所选范围的两端。连续动向变化依据已核验的页面榜单，区间端点按来源披露。"
                   "基金只数用于检查比较口径，成分身份仍以来源为准。区间涨幅与金额为各自时段原值。</p>")
    details.append(_table("ETF连续动向变化", ("方向", "榜单变化", "基金只数（前→今）", "口径检查",
                                             "前一榜天数 / 区间涨幅 / 金额", "当前榜天数 / 区间涨幅 / 金额",
                                             "已核实日申赎换向"),
                          ((r["name"], r["classification"], f'{r["count_1"] or "—"}→{r["count_2"] or "—"}',
                            r["scope_note"], " / ".join(filter(None, (r["days_1"], r["return_1"], r["flow_1"]))),
                            " / ".join(filter(None, (r["days_2"], r["return_2"], r["flow_2"]))),
                            r["verified_daily_turn"]) for r in changes)))
    if comparisons:
        details.append(_table("方向申赎金额跨日对照", ("方向", "比较状态", "更早金额日 / 金额", "前一金额日 / 金额", "当前金额日 / 金额"),
                              ((r["name"], r["status"], f'{r["date_0"]} / {r["amount_0"]}' if r["date_0"] else "—",
                                f'{r["date_1"]} / {r["amount_1"]}',
                                f'{r["date_2"]} / {r["amount_2"]}') for r in comparisons)))
    details.append(_table("当日涨跌幅榜两端", ("榜单", "基金", "代码", "价格", "涨跌幅", "成交额", "筛选范围"),
                          ((r["bucket"], r["name"], r["code"], r["price_raw"], r["change_pct_raw"],
                            r["turnover_raw"], r["scope"]) for r in current["market"])))
    details.append(_table("方向和个基申赎原值", ("金额日", "视图", "榜单", "名称", "代码", "基金只数",
                                              "金额", "份额", "页面涨幅", "基金规模", "披露口径"),
                          ((r["flow_date"], r["view"], r["bucket"], r["name"], r["code"], r["constituent_count"],
                            r["flow_amount_raw"], r["flow_shares_raw"], r["return_pct_raw"], r["size_raw"],
                            r["disclosure_status"]) for r in current["flows"])))
    return "".join(details)


def analyze_etf(today, previous, earlier_archives=()):
    """动向只比较相邻榜，日申赎按披露日期选择当期及此前最多两期。"""
    current = _snapshot(today)
    prior = _snapshot(previous) if previous is not None else {}
    if prior and prior["date"] >= current["date"]:
        raise ValueError("ETF比较基线必须早于事实日")
    history = []
    latest_flow_date = current["flow_date"]
    if prior.get("flow_date") and prior["flow_date"] < latest_flow_date:
        history.append(prior)
        latest_flow_date = prior["flow_date"]
    if latest_flow_date:
        latest_report_date = (prior or current)["date"]
        for archive in earlier_archives:
            older = _snapshot(archive)
            if older["date"] >= latest_report_date:
                raise ValueError("ETF历史快照日期顺序不符")
            latest_report_date = older["date"]
            if older["flow_date"] and older["flow_date"] < latest_flow_date:
                history.append(older)
                latest_flow_date = older["flow_date"]
            if len(history) == 2:
                break
    prior_flow = history[0] if history else {}
    comparisons = _flow_comparisons(current, prior_flow)
    if len(history) == 2:
        older = history[1]
        verified = {"同范围可比", "净申购转净赎回", "净赎回转净申购"}
        adjacent = {row["name"]: row for row in _flow_comparisons(prior_flow, older)}
        current_names = {row["name"] for row in comparisons}
        for row in comparisons:
            old = adjacent.get(row["name"])
            if row["status"] in verified and old and old["status"] in verified:
                row.update(date_0=old["date_1"], amount_0=old["amount_1"], initial_0=old["initial_1"])
        # 中间一期没有该方向的披露时，可用标明事实日的两端原值。
        comparisons.extend(row for row in _flow_comparisons(current, older)
                           if row["name"] not in current_names and row["status"] in verified)
    for row in comparisons:
        for field in ("date_0", "amount_0", "initial_0"):
            row.setdefault(field, "")
    changes = _motion_changes(current, prior, comparisons)
    return {
        "summary": _summary(current, changes, comparisons),
        "changes": changes,
        "flow_comparisons": comparisons,
        "details": _details(current, prior, changes, comparisons),
    }
