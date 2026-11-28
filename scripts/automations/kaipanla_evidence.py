"""Read and verify the facts collected from the Kaipanla UI."""

from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
ALERT_SECTIONS = {"次日测算", "当日异动", "近期严重异动", "热门股偏离值", "监管期证券", "历史监管证券"}


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(folder, filename, required):
    entries = {}
    for line in (folder / filename).read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        require(name == Path(name).name and name not in entries, f"清单路径或重复项无效：{name}")
        require(digest(folder / name) == expected, f"归档哈希不一致：{folder / name}")
        entries[name] = expected
    require(set(required) <= entries.keys(), f"清单缺项：{set(required) - entries.keys()}")
    return entries


def pct(raw):
    require(raw.endswith("%"), f"涨幅格式无效：{raw}")
    value = Decimal(raw[:-1])
    require(value.is_finite(), f"涨幅数值无效：{raw}")
    return value


def flow_amount(raw):
    multiplier = {"元": 1, "万": 10_000, "亿": 100_000_000}
    require(raw and raw[-1] in multiplier, f"申赎金额单位无效：{raw}")
    value = Decimal(raw[:-1].replace(",", "")) * multiplier[raw[-1]]
    require(value.is_finite(), f"申赎金额无效：{raw}")
    return value


def shanghai(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, f"读取时间缺少时区：{value}")
    return result.astimezone(SHANGHAI)


def dated(rows, fact, label):
    require(
        all(row["date"] == fact and shanghai(row["read_at"]).date().isoformat() >= fact for row in rows),
        f"{label}事实日或取数时间未核实",
    )
    return rows


def calendar(db_path, fact):
    start = (date.fromisoformat(fact) - timedelta(days=90)).isoformat()
    end = (date.fromisoformat(fact) + timedelta(days=35)).isoformat()
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = dict(db.execute(
            "SELECT date, is_open FROM trade_calendar WHERE exchange='SSE' "
            "AND date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        ))
    require(fact in rows, "缺少事实日交易日历")
    if rows[fact] == 0:
        return None
    require(rows[fact] == 1, "事实日交易状态无效")
    before = [day for day, is_open in rows.items() if day < fact and is_open == 1][-19:]
    after = next((day for day, is_open in rows.items() if day > fact and is_open == 1), None)
    require(len(before) == 19 and after, "交易日历缺少所需的前后开放日")
    first, last = date.fromisoformat(before[0]), date.fromisoformat(after)
    expected = ((first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1))
    require(all(rows.get(day) in {0, 1} for day in expected), "所用区间交易日历不连续")
    return before, after


def stock_facts(ui, fact):
    headers = dated(read(ui / "header.csv"), fact, "百日新高摘要")
    groups = dated(read(ui / "group.csv"), fact, "百日新高分组")
    stocks = dated(read(ui / "stock.csv"), fact, "百日新高个股")
    tails = {r["view"]: r for r in dated(read(ui / "tail.csv"), fact, "页尾")}
    require(len(headers) == 1, "百日新高缺少唯一摘要")
    header = headers[0]
    total, new = int(header["total"]), int(header["new"])
    require(len({r["name"] for r in groups}) == len(groups), "百日新高分组重复")
    require(len({r["code"] for r in stocks}) == len(stocks) == total, "个股覆盖与摘要不符")
    require(sum(int(r["count"]) for r in groups) == total, "分组数量与摘要不符")
    require(sum(r["new"] == "true" for r in stocks) == new, "个股 NEW 与摘要不符")
    for view, count in (("百日新高按个股", total), ("百日新高按板块", len(groups))):
        tail = tails.get(view, {})
        require(tail.get("tail") == "true" and int(tail["unique_rows"]) == count, f"{view}未核对尾页")
    for row in stocks:
        require(re.fullmatch(r"\d{6}", row["code"]) and row["new"] in {"true", "false"}, f"个股身份无效：{row}")
        pct(row["raw_pct"])
    return groups, stocks, total, new


def split_cell(value):
    first, _, detail = value.partition(" / ")
    return first, detail


def verify_update(row, field, fact):
    match = re.search(r"(今天|\d{4}-\d{2}-\d{2})\s*(\d\d:\d\d)", row[field])
    require(match, "来源更新时间没有可核实的日期与时刻")
    update_day = shanghai(row["read_at"]).date().isoformat() if match[1] == "今天" else match[1]
    require(update_day == fact, "来源更新时间与事实日不符")
    return datetime.fromisoformat(f"{update_day} {match[2]}")


ALERT_FIELDS = (
    "fact_date section source name code tag status event_date forecast_date actual_pct actual_price "
    "forecast_display_pct forecast_reference_price trigger_pct trigger_price deviation_pct deviation_window "
    "pattern today_space today_rule next_space next_rule sector_tags monitor_start monitor_end source_updated_at "
    "event_description captured_at raw_values"
).split()


def alert_facts(ui, fact, next_open, prior):
    raw = dated(read(ui / "alert-raw.csv"), fact, "异动提醒")
    tails = dated(read(ui / "alert-tail.csv"), fact, "异动尾页")
    headers = dated(read(ui / "alert-headers.csv"), fact, "异动表头")
    counts = Counter(row["section"] for row in raw)
    require(len(tails) == len(ALERT_SECTIONS) and {t["section"] for t in tails} == ALERT_SECTIONS,
            "异动提醒六个分区的验收证据不足")
    require(set(counts) <= ALERT_SECTIONS, "异动提醒出现未知分区")
    require(all(counts[t["section"]] == int(t["unique_rows"]) and t["boundary"] for t in tails),
            "异动提醒实采条数和尾页不符")
    monitor = [r for r in headers if r["title"] == "重点监控"]
    require(len(monitor) == 1 and all(t in monitor[0]["evidence"] for t in ("09:00", "15:00")),
            "重点监控未验证时间边界")
    updated = verify_update(monitor[0], "evidence", fact).isoformat(sep=" ", timespec="minutes")
    data = []
    for record in raw:
        section = record["section"]
        require(re.fullmatch(r"\d{6}", record["code"]), "异动证券代码不完整")
        row = dict.fromkeys(ALERT_FIELDS, "")
        row.update(fact_date=fact, section=section, source="开盘啦 异动提醒",
                   name=record["name"], code=record["code"], captured_at=record["read_at"],
                   raw_values=record["raw_values"])
        if section in {"监管期证券", "历史监管证券"}:
            start = date.fromisoformat(record["tag"]).isoformat()
            end = date.fromisoformat(record["raw_values"]).isoformat()
            require(start <= end and (end < fact) == (section == "历史监管证券"), "监管日期与分区冲突")
            status = "待生效" if start > fact else "已结束" if end < fact else "监管中"
            if start == fact:
                status = "今日开始"
            row.update(status=status, monitor_start=start + " 09:00", monitor_end=end + " 15:00",
                       source_updated_at=f"{updated} Asia/Shanghai", raw_values=f"{start} / {end}")
        else:
            row["tag"] = record["tag"]
            cells = record["raw_values"].split(", ")
            if section == "近期严重异动":
                event = date.fromisoformat("20" + cells[0]).isoformat() if len(cells[0]) == 8 else date.fromisoformat(cells[0]).isoformat()
                require(fact[:7] + "-01" <= event <= fact, "近期严重异动超出本月验收范围")
                status, description = split_cell(cells[1])
                row.update(event_date=event, status=status, event_description=description)
            else:
                require(len(cells) == (5 if section == "次日测算" else 6), f"异动分区列数不符：{section}")
                first, second = split_cell(cells[0])
                level, hint = split_cell(cells[1])
                space, rule = split_cell(cells[2])
                if section == "次日测算":
                    pct(level)
                    row.update(status="下一开放日测算", forecast_date=next_open,
                               forecast_display_pct=first, forecast_reference_price=second,
                               trigger_pct=level, trigger_price=hint, next_space=space, next_rule=rule,
                               sector_tags="、".join(cells[3:]))
                else:
                    pct(first)
                    next_space, next_rule = split_cell(cells[3])
                    row.update(actual_pct=first, next_space=next_space, next_rule=next_rule,
                               today_space=space, today_rule=rule, sector_tags="、".join(cells[4:]))
                    if section == "当日异动":
                        triggered = space == "触发严重异动"
                        row.update(actual_price=second, trigger_pct=level, trigger_price=hint,
                                   status=space if triggered else "当日候选", today_space="" if triggered else space)
                    else:
                        row.update(pattern=second, deviation_pct=level, deviation_window=hint)
        data.append(row)
    key = lambda r: (r["section"], r["code"], r["event_date"], r["monitor_start"], r["monitor_end"])
    require(len({key(r) for r in data}) == len(data), "异动记录身份重复")
    for section, field in (("近期严重异动", "event_date"), ("历史监管证券", "monitor_end")):
        boundary = next(t["boundary"] for t in tails if t["section"] == section)
        if prior is None:
            require("全月" in boundary or "上月" in boundary, f"{section}无前日基线，需要可核实的全月边界")
        old_keys = {(r["code"], r[field]) for r in (prior or ()) if r["section"] == section and r[field][:7] == fact[:7]}
        new_keys = {(r["code"], r[field]) for r in data if r["section"] == section}
        require(old_keys <= new_keys, f"{section}未覆盖历史重叠边界")
    today = {r["code"]: r for r in data if r["section"] == "当日异动"}
    hot = {r["code"]: r for r in data if r["section"] == "热门股偏离值"}
    require(all(r["name"] == hot[code]["name"] and pct(r["actual_pct"]) == pct(hot[code]["actual_pct"])
                for code, r in today.items() if code in hot), "异动提醒两表同行涨幅或身份冲突")
    return data


def etf_flow_key(fact):
    return f"{int(fact[5:7])}月{int(fact[8:10])}日申赎列"


def etf_field_issues(data, fact, cross_reference=None):
    """Validate collected ETF rows before the UI is closed, using the same rules as finalization."""
    metadata = {row["view"]: row for row in data.get("etf-meta", [])}
    flow_key = etf_flow_key(fact)
    issues = []

    def add(slot, page, row, field, reason):
        identity = row.get("code") or row.get("name") or ""
        issues.append({
            "slot": f"ETF:{slot}", "page": page, "identity": identity, "name": row.get("name", ""),
            "field": field, "reason": reason, "read_at": row.get("read_at", ""),
        })

    def numeric(slot, page, row, field, parse, proof, *, required=False):
        value = row.get(field)
        if value in {None, "", "--"}:
            identity = row.get("code") or row.get("name")
            marker = re.escape(f"源缺字段 {identity}/{field}")
            explicit_gap = identity and re.search(rf"{marker}(?:[\s，。；、]|$)", proof)
            if required or not explicit_gap:
                add(slot, page, row, field, "缺少明确原值或已核实来源缺值证据")
            return
        try:
            require(parse(value).is_finite(), "数值非有限")
        except (ArithmeticError, ValueError):
            add(slot, page, row, field, "原值格式不符")

    def positive_int(slot, page, row, field):
        value = row.get(field) or ""
        if not (value.isdecimal() and int(value) > 0):
            add(slot, page, row, field, "须核实正整数")

    market_page = "涨幅榜"
    market_proof = metadata.get(market_page, {}).get("boundary", "")
    for row in data.get("etf-market", []):
        if not re.fullmatch(r"\d{6}", row["code"]):
            add(market_page, market_page, row, "code", "基金代码不完整")
        numeric(market_page, market_page, row, "change_pct_raw", pct, market_proof, required=True)
        numeric(market_page, market_page, row, "price_raw", Decimal, market_proof)
        numeric(market_page, market_page, row, "turnover_raw", flow_amount, market_proof)

    for row in data.get("etf-flows", []):
        page = row["view"]
        cross = (row["flow_date"] == cross_reference if cross_reference is not None
                 else "跨境T+2" in row["disclosure_status"])
        slot, fallback = ("跨境参考", "跨境参考") if cross else ("申赎列", flow_key)
        if page not in {"方向汇总", "申购赎回"}:
            add(slot, page, row, "view", "申赎视图未知")
            continue
        proof = metadata.get(page, metadata.get(fallback, {})).get("boundary", "")
        ranked = not cross and row["bucket"] in {"净申购", "净赎回"}
        numeric(slot, page, row, "flow_amount_raw", flow_amount, proof, required=ranked)
        numeric(slot, page, row, "return_pct_raw", pct, proof)
        if page == "方向汇总":
            positive_int(slot, page, row, "constituent_count")
            numeric(slot, page, row, "size_raw", flow_amount, proof)
        else:
            if not re.fullmatch(r"\d{6}", row["code"]):
                add(slot, page, row, "code", "基金代码不完整")
            numeric(slot, page, row, "flow_shares_raw", flow_amount, proof)

    motion = metadata.get("动向", {})
    update = None
    if motion:
        try:
            update = verify_update(motion, "boundary", fact).isoformat(sep=" ", timespec="minutes")
        except ValueError as exc:
            add("动向", "动向", motion, "updated_at", str(exc))
    for row in data.get("etf-trends", []):
        if not row["updated_at"] or (update and row["updated_at"] != update):
            add("动向", "动向", row, "updated_at", "与事实日动向表头时间不符")
        for field in ("duration_days", "constituent_count"):
            positive_int("动向", "动向", row, field)
        proof = motion.get("boundary", "")
        numeric("动向", "动向", row, "interval_change_pct_raw", pct, proof)
        numeric("动向", "动向", row, "interval_flow_raw", flow_amount, proof)
    return issues


def etf_gate(ui, fact, previous, cross_reference):
    market = dated(read(ui / "etf-market.csv"), fact, "ETF行情")
    flows = dated(read(ui / "etf-flows.csv"), fact, "ETF申赎")
    trends = dated(read(ui / "etf-trends.csv"), fact, "ETF动向")
    all_meta = dated(read(ui / "etf-meta.csv"), fact, "ETF验收证据")
    meta = {row["view"]: row for row in all_meta}
    require(len(all_meta) == len(meta) and all(row["boundary"] and row["scope"] for row in all_meta),
            "ETF验收边界不完整")
    flow_key = etf_flow_key(fact)
    require({"涨幅榜", "动向", "跨境参考", flow_key} <= meta.keys(), "ETF未验收行情、申赎或动向")
    issues = etf_field_issues({"etf-market": market, "etf-flows": flows, "etf-trends": trends,
                               "etf-meta": all_meta}, fact, cross_reference)
    if issues:
        details = "、".join(f'{row["page"]}/{row["identity"]}/{row["field"]} {row["reason"]}'
                           for row in issues[:5])
        raise ValueError(f"ETF有{len(issues)}个字段待核：{details}")
    # 旧归档按标准前五验收。短榜的元数据须含实际条数和页尾或空榜依据。
    expected_by_view = {}
    for group in ("涨幅榜", flow_key):
        boundary = meta[group]["boundary"]
        declaration = re.search(r"分页条数\s*[=:：]\s*([0-9/]+)", boundary)
        count = 2 if group == "涨幅榜" else 4
        expected = [int(value) for value in declaration[1].split("/")] if declaration else [5] * count
        require(len(expected) == count and all(0 <= n <= 5 for n in expected), f"{group}验收条数无效")
        require(all(n == 5 for n in expected) or any(word in boundary for word in ("尾", "空榜", "来源未披露")),
                f"{group}短榜缺少来源边界")
        expected_by_view[group] = expected
    recent_flow_dates = {r["flow_date"] for r in flows if "跨境T+2" not in r["disclosure_status"]}
    require(recent_flow_dates <= {previous}, "ETF境内申赎数据日异常")
    coverage = [
        (market, "涨幅榜", "涨幅高", "涨幅低", "change_pct_raw", expected_by_view["涨幅榜"]),
        ([r for r in flows if r["view"] == "方向汇总" and r["flow_date"] == previous],
         "方向汇总", "净申购", "净赎回", "flow_amount_raw", expected_by_view[flow_key][:2]),
        ([r for r in flows if r["view"] == "申购赎回" and r["flow_date"] == previous],
         "申购赎回", "净申购", "净赎回", "flow_amount_raw", expected_by_view[flow_key][2:]),
    ]
    for records, view, high, low, field, declared_counts in coverage:
        for bucket, expected in zip((high, low), declared_counts):
            selected = [r for r in records if r["bucket"] == bucket]
            require(len(selected) == expected and all(r[field] for r in selected), f"{view} {bucket} 条数或值未核齐")
            expected_scope = meta.get(view, meta["涨幅榜" if view == "涨幅榜" else flow_key])["scope"]
            require(all(r["scope"] == expected_scope for r in selected), f"{view}榜单筛选范围不一致")
            parse = pct if view == "涨幅榜" else flow_amount
            numbers = [parse(r[field]) for r in selected]
            require(numbers == sorted(numbers, reverse=bucket == high), f"{view} {bucket} 排序与原值不符")
            if view != "涨幅榜":
                require(all((value > 0 if bucket == high else value < 0) for value in numbers),
                        f"{view} {bucket} 申赎符号不一致")
    require(len({r["code"] for r in market}) == len(market), "ETF行情名单重复")
    require(len({(r["view"], r["flow_date"], r["code"] or r["name"]) for r in flows}) == len(flows),
            "ETF申赎记录重复")
    require(all(r["flow_date"] in {previous, cross_reference} for r in flows), "ETF申赎数据日异常")
    require(any(r["flow_date"] == cross_reference for r in flows) or "来源未披露" in meta["跨境参考"]["boundary"],
            "ETF跨境参考未核实")

    motion = meta["动向"]["boundary"]
    require("尾" in motion, "ETF动向未验收页尾")
    require(len({r["name"] for r in trends}) == len(trends), "ETF动向记录重复")
    trend_counts = Counter(r["bucket"] for r in trends)
    require(set(trend_counts) <= {"连续净申购", "连续净赎回"}, "ETF动向出现未知分区")
    for bucket in ("连续净申购", "连续净赎回"):
        match = re.search(rf"{bucket}(\d+)条|(\d+)条(?:{bucket}|{bucket[2:]})", motion)
        require(match and trend_counts[bucket] == int(match[1] or match[2]), f"ETF动向 {bucket} 行数未核齐")
    return len(market) + len(flows) + len(trends)
