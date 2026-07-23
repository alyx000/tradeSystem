"""金十快讯采集:max_time 锚点翻页 + 硬预算 + 窗口边界判定。

契约(设计文档「collector 契约」,2026-07-23 实测锚定):
- max_time 是向前翻页锚点(非 start/end 过滤);页边界重复 1 条按 id 去重
- 成功条件:最老已取条目时间 <= 窗口起点(触底),否则不得标 complete
- 任一预算触发即止,状态明示,不伪装全量
"""
from __future__ import annotations

import logging
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

JIN10_URL = "https://flash-api.jin10.com/get_flash_list"
JIN10_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
}
JIN10_CHANNEL = "-8200"
TIME_FMT = "%Y-%m-%d %H:%M:%S"

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 10
MAX_RETRIES = 2              # 单请求重试次数(退避 1s/2s)
MAX_PAGES = 300              # 24h≈105 页 / 54h≈240 页(实测 ~90 条/h,~21 条/页)
MAX_RUNTIME_SECONDS = 120
PAGE_DELAY_SECONDS = 0.2
SCHEMA_DROP_THRESHOLD = 0.2  # 必需字段缺失率超过 → schema_drift

STATUS_COMPLETE = "complete"
STATUS_TRUNCATED = "partial_window_truncated"
STATUS_STALLED = "pagination_stalled"
STATUS_DRIFT = "schema_drift"
STATUS_FAILED = "source_failed"


class Jin10FetchError(RuntimeError):
    """单请求重试耗尽后的源失败。"""


@dataclass
class CollectResult:
    status: str
    items: List[dict] = field(default_factory=list)  # 窗口内、去重后,新→旧
    raw_count: int = 0       # 翻页取回总条数(含窗口外与页边界重复)
    dropped_count: int = 0   # 必需字段缺失被丢弃数
    pages: int = 0
    error: Optional[str] = None


def _fetch_page(session: requests.Session, max_time: str,
                sleep_fn: Callable[[float], None] = time_mod.sleep) -> List[dict]:
    """取一页;重试耗尽抛 Jin10FetchError。doctor 亦复用。

    退避 sleep 走可注入的 sleep_fn:单测注入空函数,避免错误路径真睡 1s+2s。
    """
    params = {"channel": JIN10_CHANNEL, "vip": "1", "max_time": max_time}
    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = session.get(JIN10_URL, params=params, headers=JIN10_HEADERS,
                               timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            resp.raise_for_status()
            data = resp.json().get("data")
            if not isinstance(data, list):
                raise ValueError("响应缺少 data 列表")
            return data
        except Exception as exc:  # noqa: BLE001 — 网络/JSON/schema 统一按源失败重试
            last_err = exc
            if attempt < MAX_RETRIES:
                sleep_fn(attempt + 1)  # 退避 1s / 2s
    raise Jin10FetchError(str(last_err))


def _parse_item(item: dict) -> Optional[datetime]:
    """校验必需字段(id/time/data.content|title),返回条目时间;不合格返回 None。"""
    data = item.get("data") or {}
    if not item.get("id") or not item.get("time"):
        return None
    if not (data.get("content") or data.get("title")):
        return None
    try:
        return datetime.strptime(item["time"], TIME_FMT)
    except (TypeError, ValueError):
        return None


def collect_window(window_start: datetime, window_end: datetime, *,
                   session: Optional[requests.Session] = None,
                   sleep_fn: Callable[[float], None] = time_mod.sleep,
                   clock: Callable[[], float] = time_mod.monotonic) -> CollectResult:
    """按窗口翻页采集。窗口为 naive 上海时间(与 API 返回一致)。"""
    if session is None:
        session = requests.Session()
        session.trust_env = True  # 显式兑现 spec 契约:沿用系统代理(DingTalkPusher 默认相反,勿混淆)
    anchor = window_end.strftime(TIME_FMT)
    seen: set = set()
    kept: List[dict] = []
    raw_count = dropped = pages = 0
    status: Optional[str] = None
    error: Optional[str] = None
    started = clock()

    while pages < MAX_PAGES:
        if clock() - started > MAX_RUNTIME_SECONDS:
            status = STATUS_TRUNCATED
            break
        try:
            page = _fetch_page(session, anchor, sleep_fn)
        except Jin10FetchError as exc:
            status, error = STATUS_FAILED, str(exc)
            break
        pages += 1
        raw_count += len(page)
        if not page:
            status = STATUS_STALLED  # 空页且未触底:锚点无法前进
            break

        page_oldest: Optional[datetime] = None
        new_ids = 0
        for item in page:
            ts = _parse_item(item)
            if ts is None:
                dropped += 1
                continue
            if page_oldest is None or ts < page_oldest:
                page_oldest = ts
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            new_ids += 1
            if window_start <= ts <= window_end:
                kept.append(item)

        if page_oldest is not None and page_oldest <= window_start:
            status = STATUS_COMPLETE  # 触底:窗口起点之前的条目已可见
            break
        new_anchor = page_oldest.strftime(TIME_FMT) if page_oldest else anchor
        if new_ids == 0 or new_anchor == anchor:
            status = STATUS_STALLED
            break
        anchor = new_anchor
        sleep_fn(PAGE_DELAY_SECONDS)

    if status is None:
        status = STATUS_TRUNCATED  # MAX_PAGES 耗尽仍未触底
    if (status in (STATUS_COMPLETE, STATUS_TRUNCATED)
            and raw_count and dropped / raw_count > SCHEMA_DROP_THRESHOLD):
        status = STATUS_DRIFT  # 字段漂移不静默;但不覆盖 source_failed/stalled(保留更硬的故障信号)
    kept.sort(key=lambda i: i["time"], reverse=True)
    if dropped:
        logger.warning("[macro-flash] %d 条必需字段缺失被丢弃(raw %d)", dropped, raw_count)
    return CollectResult(status=status, items=kept, raw_count=raw_count,
                         dropped_count=dropped, pages=pages, error=error)
