"""历史量基线：分段日线、完成月总量对账，不以固定回看窗口冒充历史。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
import re

VERSION = "lifetime-raw-volume-v1"
METRICS = {
    "volume": dict(field="vol", label="成交量", unit="手", value="volume", maximum="max_volume",
                   previous="previous_max_volume", version=VERSION, monthly_divisor=100),
    "amount": dict(field="amount", label="成交额", unit="千元", value="amount", maximum="max_amount",
                   previous="previous_max_amount", version="lifetime-raw-amount-v1", monthly_divisor=1000),
}


def metric_spec(metric):
    if metric not in METRICS:
        raise ValueError("口径必须是volume或amount")
    return METRICS[metric]


class VolumeRows(dict):
    """保留完全相同重复行的折叠计数；冲突行仍然失败。"""
    duplicate_count = 0


def day(value):
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError("日期格式非法")
    return text


def code(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):
        raise ValueError("证券代码非法")
    return value


def number(value):
    if isinstance(value, bool):
        raise ValueError("成交量/成交额不是数值")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("成交量/成交额缺失、非正或非有限")
    return result


def rows_by_day(rows, stock, start, end, metric="volume"):
    spec = metric_spec(metric)
    if not isinstance(rows, list):
        raise ValueError("行情响应不是列表")
    result, originals = VolumeRows(), {}
    for row in rows:
        if code(row.get("ts_code")) != stock:
            raise ValueError("行情股票错位")
        d = day(row.get("trade_date"))
        if not start <= d <= end:
            raise ValueError("行情日期超出请求区间")
        if d in result:
            if originals[d] != row:
                raise ValueError("行情同日重复且内容冲突")
            result.duplicate_count += 1
            continue
        result[d] = number(row.get(spec["field"]))
        originals[d] = row
    return result


def month_keys(start, end):
    current = date.fromisoformat(start).replace(day=1)
    while current.isoformat() <= end:
        yield current.strftime("%Y-%m")
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def certify_months(daily, monthly, stock, start, end, metric="volume"):
    """固定镜像 monthly: vol为股、amount为元；daily: vol为手、amount为千元。
    对账容忍分别为1手/1千元，只影响完整性核验；新高比较始终严格大于。

    空缺整月即使可能为长停牌也不凭空认证，宁可保留 partial。
    """
    spec = metric_spec(metric)
    monthly_rows = rows_by_day(monthly, stock, start, end, metric)
    sums = defaultdict(Decimal)
    for d, vol in daily.items():
        sums[d[:7]] += Decimal(str(vol))
    months = {}
    for d, vol in monthly_rows.items():
        if d[:7] in months:
            raise ValueError("月线月份重复")
        months[d[:7]] = Decimal(str(vol)) / spec["monthly_divisor"]
    expected = set(month_keys(start, end))
    if set(sums) != expected or set(months) != expected:
        raise ValueError("历史日线/月线存在整月缺口，未证明停牌")
    for month in expected:
        if abs(sums[month] - months[month]) > Decimal("1"):
            raise ValueError(f"历史日线与月线总量不符:{month}")
    return monthly_rows.duplicate_count


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def load_baseline(directory, stock, listed, through, metric="volume"):
    spec = metric_spec(metric)
    for path in sorted((directory / stock).glob("*.json"), reverse=True):
        if path.stem > through:
            continue
        try:
            envelope = json.loads(path.read_text())
            row = envelope["baseline"]
            if envelope["sha256"] != fingerprint(row):
                continue
            if (row["version"] != spec["version"] or row["code"] != stock or row["list_date"] != listed
                    or row["through"] != path.stem or row["source"] != "tushare:daily+monthly"
                    or row["first_date"] != listed or row["observations"] < 1
                    or not listed <= day(row["max_date"]) <= day(row["through"])):
                continue
            number(row[spec["maximum"]])
            # 只认自然月尾的完成月快照，不接受当月/未来水位。
            last = date.fromisoformat(row["through"])
            if (last + timedelta(days=1)).day != 1:
                continue
            return row
        except (ValueError, TypeError, KeyError, OSError):
            continue
    return None


def build_baseline(provider, stock, listed, through, previous=None, metric="volume"):
    spec = metric_spec(metric)
    start = listed if previous is None else (
        date.fromisoformat(previous["through"]) + timedelta(days=1)).isoformat()
    if start > through:
        return previous
    daily, duplicate_count = {}, 0
    cursor = date.fromisoformat(start)
    while cursor.isoformat() <= through:
        # 5000 自然日必少于 daily 6000 行上限；区间不重叠，不依赖静默截断。
        end = min(cursor + timedelta(days=4999), date.fromisoformat(through)).isoformat()
        result = provider.get_stock_daily_range(stock, cursor.isoformat(), end)
        if not result.success or result.source != "tushare:daily":
            raise ValueError(f"历史日线失败:{result.error}")
        part = rows_by_day(result.data, stock, cursor.isoformat(), end, metric)
        daily.update(part)
        duplicate_count += part.duplicate_count
        cursor = date.fromisoformat(end) + timedelta(days=1)
    monthly = provider._query_records("monthly", ts_code=stock,
                                      start_date=start.replace("-", ""),
                                      end_date=through.replace("-", ""))
    duplicate_count += certify_months(daily, monthly, stock, start, through, metric)
    if previous is None and min(daily, default=None) != listed:
        raise ValueError("历史未覆盖上市首日")
    peak_date = max(sorted(daily), key=daily.get)
    peak = daily[peak_date]
    if previous and previous[spec["maximum"]] >= peak:
        peak, peak_date = previous[spec["maximum"]], previous["max_date"]
    return dict(version=spec["version"], code=stock, list_date=listed, first_date=listed,
                through=through, **{spec["maximum"]: peak}, max_date=peak_date,
                observations=len(daily) + (previous["observations"] if previous else 0),
                duplicate_rows_collapsed=duplicate_count + (previous.get("duplicate_rows_collapsed", 0) if previous else 0),
                source="tushare:daily+monthly")
