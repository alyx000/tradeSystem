"""东财盘后炸板股池。保留原响应，分辨已核验空池和接口失败。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import requests
import yaml

from utils import is_st_stock

KEY = "broken_board_pool"
SOURCE = "eastmoney:getTopicZBPool"
SCHEMA = "broken-board-pool-v1"
TZ = ZoneInfo("Asia/Shanghai")
URL = "https://push2ex.eastmoney.com/getTopicZBPool"


def _sha(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value, *, zero=False, integer=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(num) or num < 0 or (num == 0 and not zero) or (integer and not num.is_integer()):
        return None
    return int(num) if integer else num


def _first_time(value):
    compact = str(value).zfill(6)
    if not re.fullmatch(r"\d{6}", compact):
        return None
    try:
        stamp = datetime.strptime(compact, "%H%M%S").time()
    except ValueError:
        return None
    if (stamp.hour == 9 and stamp.minute == 25) or time(9, 30) <= stamp <= time(11, 30) or time(13) <= stamp <= time(15):
        return stamp.isoformat()
    return None


def _code(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"\d{6}", raw):
        raise ValueError("炸板股池证券代码缺失或非法")
    if raw.startswith(("4", "8", "92")):
        return raw + ".BJ"
    if raw.startswith(("6", "9")):
        return raw + ".SH"
    if raw.startswith(("0", "2", "3")):
        return raw + ".SZ"
    raise ValueError("炸板股池未知证券代码 " + raw)


def parse(day, payload, fetched_at):
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None or stamp < datetime.combine(date.fromisoformat(day), time(16), TZ):
        raise ValueError("炸板股池取得时刻缺少时区或早于事实日盘后")
    if not isinstance(payload, dict) or type(payload.get("rc")) is not int or payload["rc"] != 0:
        raise ValueError("炸板股池接口状态非法")
    data = payload.get("data")
    if not isinstance(data, dict) or str(data.get("qdate")) != day.replace("-", ""):
        raise ValueError("炸板股池来源日期缺失或不与请求日相符")
    total = _number(data.get("tc"), zero=True, integer=True)
    rows = data.get("pool")
    if not isinstance(rows, list) or total is None or total != len(rows) or total > 5000:
        raise ValueError("炸板股池来源总数与页内条数未核齐")

    stocks, seen, gaps = [], set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("炸板股池行格式非法")
        code = _code(row.get("c"))
        name = row.get("n")
        if code in seen or not isinstance(name, str) or not name.strip():
            raise ValueError("炸板股池重复代码或证券名称缺失 " + code)
        seen.add(code)
        last, upper = _number(row.get("p")), _number(row.get("ztp"))
        stock = dict(code=code, name=name.strip(), first_seal_time=_first_time(row.get("fbt")),
                     break_times=_number(row.get("zbc"), zero=True, integer=True),
                     last_price=last / 1000 if last is not None else None,
                     up_limit=upper / 1000 if upper is not None else None,
                     amount_yuan=_number(row.get("amount"), zero=True),
                     industry=row.get("hybk") or None)
        if last is not None and upper is not None and last >= upper:
            raise ValueError("炸板股池收盘价未低于涨停价 " + code)
        missing = [k for k in ("first_seal_time", "break_times", "last_price", "up_limit", "amount_yuan")
                   if stock[k] is None]
        if missing:
            gaps.append(f"{code}: " + ", ".join(missing))
        stocks.append(stock)
    return dict(schema=SCHEMA, trade_date=day, source=SOURCE, fetched_at=fetched_at,
                status="partial" if gaps else "complete", count=total, count_ex_st=sum(
                    not is_st_stock(stock["name"]) for stock in stocks),
                stocks=stocks, source_sha256=_sha(payload), source_response=payload, gaps=gaps)


def readback(old, day):
    """从同日原响应重算，拒绝受损/无来源/错日摘要。"""
    if (not isinstance(old, dict) or old.get("schema") != SCHEMA or old.get("source") != SOURCE
            or old.get("trade_date") != day):
        return None
    try:
        restored = parse(day, old["source_response"], old["fetched_at"])
        if old.get("source_sha256") == restored["source_sha256"]:
            return restored
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _request(day):
    # 与 AKShare stock_zt_pool_zbgc_em 同一请求；保留被 DataFrame 丢弃的 qdate/tc/空响应状态。
    response = requests.get(URL, params={"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
                            "Pageindex": "0", "pagesize": "5000", "sort": "fbt:asc",
                            "date": day.replace("-", "")}, timeout=(4, 12))
    response.raise_for_status()
    return response.json()


def collect(day, *, fetch=None, now=None):
    clock = now or datetime.now(TZ)
    stamp = clock.astimezone(TZ).isoformat()
    try:
        target = date.fromisoformat(day)
        local = clock.astimezone(TZ)
        if target > local.date() or (target == local.date() and local.time() < time(16)):
            raise ValueError("未到目标日盘后")
        if fetch is None and local.date() - target > timedelta(days=30):
            raise ValueError("目标日已超过原行情接口的近30个自然日窗口")
        payload = (fetch or _request)(day)
        return parse(day, payload, stamp)
    except (ValueError, OSError, TypeError, RuntimeError) as exc:
        # 不保留 requests 异常文本（含上游完整请求URL）。
        reason = ("炸板池行情连接失败（" + type(exc).__name__ + "）"
                  if isinstance(exc, requests.RequestException) else str(exc))
        return dict(schema=SCHEMA, trade_date=day, source=SOURCE, fetched_at=stamp,
                    status="source_failed", count=None, count_ex_st=None, stocks=[], gaps=[reason])


def archived_post(root, day):
    path = Path(root) / "daily" / day / "post-market.yaml"
    if not path.is_file():
        return {}
    try:
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        envelope = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
        if isinstance(envelope, dict) and envelope.get("date") == day:
            raw = envelope.get("raw_data")
            if isinstance(raw, dict) and raw.get("date") == day:
                return raw
    except (ValueError, OSError, yaml.YAMLError):
        pass
    return {}


def prefer_verified(attempt, old):
    previous = readback(old, attempt["trade_date"])
    if not previous:
        return attempt
    preserve = attempt["status"] == "source_failed"
    if attempt["status"] == "partial":
        # 保留原来源报文。禁止拼接两次返回，把截断名单或相互冲突的片段伪造成一个池。
        new_rows = {row["code"]: row for row in attempt["stocks"]}
        preserve = previous["status"] == "complete" or any(
            any(new_rows.get(row["code"], {}).get(k) != value for k, value in row.items() if value is not None)
            for row in previous["stocks"]
        )
    if preserve:
        previous["latest_attempt"] = {k: attempt[k] for k in ("status", "fetched_at", "gaps")}
        if attempt.get("source_sha256"):
            previous["latest_attempt"]["source_sha256"] = attempt["source_sha256"]
        return previous
    return attempt


def apply_rates(data, pool, ex_st, *, sealed_source=None):
    sealed = data.get("stocks", [])
    identities = []
    for stock in sealed:
        raw = stock.get("code")
        try:
            prefix = raw.split(".", 1)[0]
            code = _code(prefix)
            if raw not in (prefix, code):
                raise ValueError("代码和交易所不符")
        except (AttributeError, ValueError):
            code = None
        identities.append(code)
    matched_count = isinstance(data.get("stocks"), list) and data.get("count") == len(sealed)
    valid_sealed = matched_count and None not in identities and len(set(identities)) == len(identities)
    # 两家来源名单出现交集时不能把一票放进两个互斥分组。
    overlap = set(identities) & {s["code"] for s in pool["stocks"]}
    sealed_excludes_st = sealed_source == "tushare:limit_list_d"
    mixed_st = sealed_excludes_st and pool["count"] != pool["count_ex_st"]
    data["seal_rate_scope"] = dict(sealed_source=sealed_source, broken_source=SOURCE,
        sealed_scope="收盘涨停源榜剔ST" if sealed_excludes_st else "收盘涨停源榜",
        broken_scope="东财炸板股池全部返回证券",
        description="剔ST比率在两个来源均按当日证券名称过滤。")
    data["broken_count"] = pool["count"]
    data["broken_count_ex_st"] = pool["count_ex_st"]
    for suffix, up_count, broken in (("", len(sealed), pool["count"]),
                                     ("_ex_st", len(ex_st), pool["count_ex_st"])):
        denominator = up_count + broken if broken is not None and not overlap and valid_sealed else 0
        if not suffix and mixed_st:
            denominator = 0
        data["seal_rate_pct" + suffix] = round(up_count * 100 / denominator, 1) if denominator else None
        data["broken_rate_pct" + suffix] = round(broken * 100 / denominator, 1) if denominator else None
    if not valid_sealed:
        data["seal_rate_gap"] = "收盘涨停名单与计数未核齐"
    elif overlap:
        data["seal_rate_gap"] = "封板与炸板来源代码冲突: " + ",".join(sorted(overlap))
    elif mixed_st:
        data["seal_rate_gap"] = "收盘涨停源排除ST，炸板池含ST，普通比率分母范围不一致"
