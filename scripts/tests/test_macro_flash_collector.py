"""collector 单测:翻页/触底/停滞/预算/schema 全 mock,不碰外网。"""
import datetime as dt

import pytest

from services.macro_flash import collector


def _item(iid, time_str, content="央行公开市场操作", **extra):
    it = {"id": iid, "time": time_str, "type": 0, "important": 0,
          "tags": [], "channel": [-8200], "data": {"content": content}}
    it.update(extra)
    return it


class FakeSession:
    """按锚点返回预置页;记录调用序列。"""
    def __init__(self, pages_by_anchor):
        self.pages = pages_by_anchor  # {max_time字符串: [item, ...]}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        anchor = params["max_time"]
        self.calls.append(anchor)
        page = self.pages.get(anchor)
        if page is None:
            raise AssertionError(f"未预置锚点 {anchor}")
        if isinstance(page, Exception):
            raise page

        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"data": page}
        return R()


W_START = dt.datetime(2026, 7, 22, 16, 30)
W_END = dt.datetime(2026, 7, 23, 16, 30)
ANCHOR0 = "2026-07-23 16:30:00"


def _collect(session):
    return collector.collect_window(W_START, W_END, session=session,
                                    sleep_fn=lambda s: None)


def test_touches_bottom_is_complete():
    """第二页含早于窗口起点的条目 → 触底 complete,窗口外条目不入 items。"""
    p1 = [_item("a", "2026-07-23 12:00:00"), _item("b", "2026-07-23 10:00:00")]
    p2 = [_item("b", "2026-07-23 10:00:00"),  # 页边界重复(锚点自身)
          _item("c", "2026-07-22 20:00:00"),
          _item("d", "2026-07-22 15:00:00")]  # 早于窗口起点 → 触底信号,不入 items
    s = FakeSession({ANCHOR0: p1, "2026-07-23 10:00:00": p2})
    r = _collect(s)
    assert r.status == collector.STATUS_COMPLETE
    assert [i["id"] for i in r.items] == ["a", "b", "c"]  # 去重 + 窗口过滤
    assert r.pages == 2


def test_max_pages_budget_truncates(monkeypatch):
    """页数预算触发 → partial_window_truncated,已取条目保留。"""
    monkeypatch.setattr(collector, "MAX_PAGES", 1)
    p1 = [_item("a", "2026-07-23 12:00:00")]
    s = FakeSession({ANCHOR0: p1})
    r = _collect(s)
    assert r.status == collector.STATUS_TRUNCATED
    assert [i["id"] for i in r.items] == ["a"]


def test_max_runtime_budget_truncates(monkeypatch):
    """墙钟预算触发 → partial_window_truncated。"""
    ticks = iter([0.0, 999.0])  # 第二页检查时已超 120s
    p1 = [_item("a", "2026-07-23 12:00:00")]
    s = FakeSession({ANCHOR0: p1})
    r = collector.collect_window(W_START, W_END, session=s,
                                 sleep_fn=lambda s2: None, clock=lambda: next(ticks))
    assert r.status == collector.STATUS_TRUNCATED


def test_anchor_not_advancing_is_stalled():
    """整页无新 id(锚点不前进)→ pagination_stalled。"""
    p1 = [_item("a", "2026-07-23 12:00:00")]
    p2 = [_item("a", "2026-07-23 12:00:00")]  # 与上页完全相同
    s = FakeSession({ANCHOR0: p1, "2026-07-23 12:00:00": p2})
    r = _collect(s)
    assert r.status == collector.STATUS_STALLED
    assert [i["id"] for i in r.items] == ["a"]


def test_empty_page_is_stalled():
    s = FakeSession({ANCHOR0: []})
    r = _collect(s)
    assert r.status == collector.STATUS_STALLED
    assert r.items == []


def test_required_field_missing_dropped_and_drift():
    """必需字段缺失丢弃计数;缺失率 >0.2 → schema_drift。"""
    p1 = [_item("a", "2026-07-23 12:00:00"),
          {"id": "x", "time": "2026-07-23 11:00:00", "data": {}},   # 无 content/title
          {"id": None, "time": "2026-07-23 10:30:00", "data": {"content": "y"}},  # 无 id
          _item("b", "2026-07-22 15:00:00")]  # 触底
    s = FakeSession({ANCHOR0: p1})
    r = _collect(s)
    assert r.dropped_count == 2
    assert r.status == collector.STATUS_DRIFT  # 2/4 > 0.2,触底也不给 complete


def test_fetch_error_after_retries_is_source_failed():
    s = FakeSession({ANCHOR0: RuntimeError("boom")})
    r = _collect(s)
    assert r.status == collector.STATUS_FAILED
    assert r.error and "boom" in r.error
    assert r.items == []


def test_mid_pagination_error_keeps_partial():
    """翻页中途失败:已取条目保留,状态 source_failed。"""
    p1 = [_item("a", "2026-07-23 12:00:00")]
    s = FakeSession({ANCHOR0: p1, "2026-07-23 12:00:00": RuntimeError("mid")})
    r = _collect(s)
    assert r.status == collector.STATUS_FAILED
    assert [i["id"] for i in r.items] == ["a"]
