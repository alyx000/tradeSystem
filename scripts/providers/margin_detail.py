"""逐证券两融明细。原始范围包括基金，按事实日分页并保留交易所披露范围。"""
from collections import Counter
from datetime import date, datetime, time
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from .base import DataResult, Timeliness

SOURCE = "tushare:margin_detail"
SHANGHAI = ZoneInfo("Asia/Shanghai")
PAGE_SIZE = 6000
SECURITY = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")
STOCK = re.compile(r"(?:60[0-9]{4}|68[89][0-9]{3})\.SH|(?:00|30)[0-9]{4}\.SZ|(?:[48][0-9]{5}|92[0-9]{4})\.BJ")
FIELDS = ("rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye")


def _check_row(row, day):
    code = row["ts_code"]
    if (not isinstance(code, str) or not SECURITY.fullmatch(code)
            or row.get("code", code) != code or row.get("trade_date") != day.replace("-", "")):
        raise ValueError("两融明细证券身份或事实日错位")
    if not STOCK.fullmatch(code) and any(STOCK.fullmatch(code[:7] + ex) for ex in ("SH", "SZ", "BJ")):
        raise ValueError("两融明细股票代码与交易所不符")
    if (code.startswith("5") and not code.endswith(".SH")) or (code.startswith(("15", "16", "18")) and not code.endswith(".SZ")):
        raise ValueError("两融明细基金代码与交易所不符")
    values = [row[key] for key in FIELDS if row.get(key) is not None]
    if not values or any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0 for v in values):
        raise ValueError("两融明细金额或数量非法")
    return code


def _receipt(rows, day, pages):
    if not isinstance(rows, list) or not isinstance(pages, list) or not 1 <= len(pages) <= 20:
        raise ValueError("两融明细分页回执缺失")
    if (type(pages[-1]) is not int or pages[-1] != 0 or sum(pages) != len(rows)
            or any(type(n) is not int or not 1 <= n <= PAGE_SIZE for n in pages[:-1])):
        raise ValueError("两融明细分页未闭合")
    codes = {_check_row(row, day) for row in rows}
    if len(codes) != len(rows):
        raise ValueError("两融明细包含重复证券")
    exchanges = dict(sorted(Counter(c[-2:] for c in codes).items()))
    stock_exchanges = {c[-2:] for c in codes if STOCK.fullmatch(c)}
    # 当前三市披露口径；历史仅部分交易所的资料仍能留存，但不得冒充三市齐备。
    stock_buys_missing = any(STOCK.fullmatch(r["ts_code"]) and r.get("rzmre") is None for r in rows)
    status = "pending" if not codes else "published" if len(stock_exchanges) == 3 and not stock_buys_missing else "partial"
    return dict(schema="margin-detail-v1", trade_date=day, page_size=PAGE_SIZE, pages=pages,
                exchanges=exchanges, status=status)


def fetch(query, day):
    """一直取到显式空页；供应端缩短页面时按实际行数移动偏移。"""
    try:
        target = (datetime.strptime(day, "%Y%m%d").date() if isinstance(day, str) and re.fullmatch(r"[0-9]{8}", day)
                  else date.fromisoformat(day))
        if target > datetime.now(SHANGHAI).date():
            raise ValueError("未来事实日不请求两融明细")
        day = target.isoformat()
        rows, seen, pages = [], set(), []
        for _ in range(20):
            batch = query("margin_detail", trade_date=target.strftime("%Y%m%d"), limit=PAGE_SIZE, offset=len(rows))
            if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
                raise ValueError("两融明细来源类型或页大小非法")
            pages.append(len(batch))
            if not batch:
                break
            for row in batch:
                code = _check_row(row, day)
                if code in seen:
                    raise ValueError("两融明细跨页重复或分页未推进")
                seen.add(code)
                rows.append({**row, "code": code})
        meta = _receipt(rows, day, pages)
        return DataResult(rows, SOURCE, note=json.dumps(meta, ensure_ascii=False),
                          fetched_at=datetime.now(SHANGHAI).isoformat(),
                          timeliness=Timeliness.HISTORICAL)
    except Exception as exc:
        return DataResult(None, SOURCE, error=str(exc))


def verified_receipt(rows, source_meta, day):
    try:
        note = json.loads(source_meta["note"])
        received = datetime.fromisoformat(source_meta["fetched_at"])
        if received.tzinfo is None:
            return None
        received = received.astimezone(SHANGHAI)
        target = date.fromisoformat(day)
        if received.date() < target or (rows and received < datetime.combine(target, time(15), SHANGHAI)):
            return None
        if source_meta["source"] == SOURCE and note == _receipt(rows, day, note["pages"]):
            return note
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return None


def receipt_is_published(rows, source_meta, day):
    return (verified_receipt(rows, source_meta, day) or {}).get("status") == "published"


def loses_disclosed_fields(previous_rows, incoming_rows):
    incoming = {r["ts_code"]: r for r in incoming_rows}
    for prior in previous_rows:
        current = incoming.get(prior["ts_code"])
        if current is None or any(prior.get(k) is not None and current.get(k) is None for k in FIELDS):
            return True
    return False


def preserve_previous_rows(raw, digest, previous_meta, rows, day):
    """保留同日已取得的有效数字及未发生变化的已核验回执原取得时点。"""
    try:
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != digest:
            return False
        old = json.loads(raw)
        if old["provider"] != SOURCE or old["biz_date"] != day or old["interface_name"] != "margin_detail":
            return False
        previous = {_check_row(row, day): row for row in old["rows"]}
        incoming = {r["ts_code"]: r for r in rows}
        if loses_disclosed_fields(previous.values(), rows):
            return True
        return (previous == incoming and bool(previous)
                and bool(verified_receipt(old["rows"], json.loads(previous_meta), day)))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False


def summary(rows, source_meta, day):
    receipt = verified_receipt(rows, source_meta, day)
    if not receipt:
        return dict(status="source_failed", requested_date=day, trade_date=None, count=None,
                    reason="逐证券两融来源或分页未核验")
    status = receipt["status"]
    stocks = [row for row in rows if STOCK.fullmatch(row["ts_code"])]
    missing_amount = sum(row.get("rzmre") is None for row in stocks)
    # 只有来源三市报齐、且每只股票都有融资买入额才生成逐股排名。
    top = None
    if status == "published" and stocks and not missing_amount:
        top = sorted(stocks, key=lambda row: (-float(row["rzmre"]), row["ts_code"]))[:20]
    return dict(status=status, requested_date=day, trade_date=day if rows else None,
                count=len(rows) if rows else None, stock_count=len(stocks) if rows else None,
                other_security_count=len(rows)-len(stocks) if rows else None, missing_stock_buy_amount=missing_amount if rows else None,
                top_rzmre=top, exchanges=receipt["exchanges"], stock_exchanges=dict(Counter(r["ts_code"][-2:] for r in stocks)),
                fetched_at=source_meta.get("fetched_at"),
                _source=SOURCE)


def report_lines(detail):
    lines = []
    for label, block in (("指定日逐证券明细", detail), ("上一开放日逐证券明细", detail.get("previous_disclosure"))):
        if not block:
            continue
        day = block.get("requested_date")
        status = block["status"]
        stamp = ""
        if block.get("fetched_at"):
            local = datetime.fromisoformat(block["fetched_at"]).astimezone(SHANGHAI)
            kind = "事后取得" if local.date().isoformat() != day else "当日取得"
            stamp = f"（{kind}于上海时间 {local:%Y-%m-%d %H:%M}）"
        if status in {"pending", "source_failed"}:
            reason = block.get("reason") or ("尚无已披露记录" if status == "pending" else "来源未核验")
            lines.append(f"- {label} {day}，{reason}{stamp}")
            continue
        counts = "、".join(f"{key} {n}只" for key, n in block["exchanges"].items())
        scope = "三市均有披露" if len(block["exchanges"]) == 3 else "部分交易所已披露"
        if block.get("missing_stock_buy_amount"):
            scope += f"，{block['missing_stock_buy_amount']} 只股票融资买入额待核"
        if status == "partial" and len(block["exchanges"]) == 3 and len(block.get("stock_exchanges", {})) < 3:
            scope += "，部分交易所的股票样本待核"
        lines.append(f"- {label} {day}，{scope}（{counts}），含股票 {block['stock_count']} 只、"
                     f"基金等其他证券 {block['other_security_count']} 只{stamp}")
        if block.get("top_rzmre"):
            names = [f"{r['ts_code']} {float(r['rzmre'])/1e8:.2f} 亿" for r in block["top_rzmre"][:3]]
            lines.append("  已披露股票中融资买入额居前："+"、".join(names))
        if block.get("latest_attempt"):
            attempt = block["latest_attempt"]
            lines.append(f"  最近重查：{attempt.get('reason') or attempt['status']}")
    return lines
