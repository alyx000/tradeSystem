"""morning-brief 单测：公告分类 / 渲染与推送预算 / 窗口解析 / 状态合并 / 巨潮分页。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from providers.base import DataResult
from services.macro_flash import collector as flash_collector
from services.morning_brief import classify, constants as C, formatter, service


# ---------- 测试基建 ----------

CONFIG = {
    "morning_brief": {
        "keywords": {
            "海外要闻": ["美股", "美联储", "原油"],
            "国内要闻": ["国务院", "央行", "A股"],
        }
    }
}


class FakeRegistry:
    """按 method 名返回预置 DataResult；未配置的方法返回失败。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list = []

    def call(self, method, *args, **kwargs):
        self.calls.append((method, args))
        value = self.responses.get(method)
        if callable(value):
            return value(*args, **kwargs)
        if value is None:
            return DataResult(data=None, source="fake", error=f"{method} 未配置")
        return value


def _flash_item(ts: str, text: str, important: bool = False, iid: str = None):
    return {"id": iid or f"{ts}-{text[:6]}", "time": ts, "important": important,
            "data": {"content": text}}


def _ann(code: str, name: str, title: str, ts: str):
    return {"code": code, "name": name, "title": title, "time": ts, "url": "http://x"}


def _quote_ok(close=100.0, pct=1.23):
    return DataResult(data={"close": close, "change_pct": pct}, source="fake")


def _full_registry(ann_items=None, ann_status="complete"):
    return FakeRegistry({
        "is_trade_day": lambda d: DataResult(
            data=datetime.strptime(d, "%Y-%m-%d").weekday() < 5, source="fake"),
        "get_global_index": lambda name: _quote_ok(),
        "get_us_tickers_overnight": lambda tickers: DataResult(
            data={"HXC": {"close": 8000, "change_pct": -0.5, "name": "纳斯达克中国金龙"}},
            source="fake"),
        "get_commodity": lambda name: _quote_ok(),
        "get_market_announcements_range": lambda s, e: DataResult(
            data={"items": ann_items or [], "status": ann_status,
                  "raw_count": len(ann_items or [])},
            source="fake"),
    })


# ---------- 公告分类 ----------

class TestClassifyAnnouncements:
    def test_noise_excluded(self):
        items = [_ann("000001", "平安银行", "关于停牌的公告", "2026-08-13 16:00:00"),
                 _ann("000002", "万科A", "北京某律所法律意见书", "2026-08-13 16:01:00")]
        grouped = classify.classify_announcements(items)
        assert list(grouped) == ["停复牌"]
        assert len(grouped["停复牌"]) == 1

    def test_first_hit_group_wins_by_declaration_order(self):
        # 标题同时含「停牌」与「减持」→ 归声明序更靠前的 停复牌
        items = [_ann("000001", "平安银行", "因筹划减持事项停牌", "2026-08-13 16:00:00")]
        grouped = classify.classify_announcements(items)
        assert list(grouped) == ["停复牌"]

    def test_same_stock_same_group_dedup_keeps_newest(self):
        items = [  # provider 契约新→旧
            _ann("300302", "同有科技", "向特定对象发行A股股票之上市公告", "2026-08-13 21:18:00"),
            _ann("300302", "同有科技", "向特定对象发行A股股票申请获受理", "2026-08-13 20:00:00"),
        ]
        grouped = classify.classify_announcements(items)
        assert len(grouped["再融资与重组"]) == 1
        assert "上市公告" in grouped["再融资与重组"][0]["title"]

    def test_unmatched_dropped(self):
        items = [_ann("000001", "平安银行", "第十届董事会会议通知", "2026-08-13 16:00:00")]
        assert classify.classify_announcements(items) == {}


class TestLoadNewsKeywords:
    def test_missing_config_fails_fast(self):
        with pytest.raises(ValueError):
            classify.load_news_keywords({})

    def test_valid_config_keeps_order(self):
        kw = classify.load_news_keywords(CONFIG)
        assert list(kw) == ["海外要闻", "国内要闻"]


# ---------- 渲染与推送预算 ----------

def _payload(**overrides):
    base = {
        "date": "2026-08-14",
        "status": "complete",
        "gaps": [],
        "news_window": (datetime(2026, 8, 13, 20, 0), datetime(2026, 8, 14, 8, 0)),
        "ann_window": (datetime(2026, 8, 13, 15, 0), datetime(2026, 8, 14, 8, 0)),
        "overnight": {
            "indices": [("标普500", {"close": 6440.0, "change_pct": -0.32})],
            "us_china": {"close": 8000, "change_pct": 0.5, "name": "纳斯达克中国金龙"},
            "commodities": [("COMEX黄金", {"error": "超时"})],
        },
        "news": {
            "candidates": [],
            "topic_order": ["海外要闻", "国内要闻"],
            "source_status": "complete",
            "raw_count": 100,
        },
        "announcements": {"grouped": {}, "status": "complete", "raw_count": 10,
                          "error": None},
    }
    base.update(overrides)
    return base


class TestRender:
    def test_sections_and_quote_failure(self):
        md = formatter.render(_payload())
        assert "# 盘前早报 · 2026-08-14" in md
        assert "- [事实] 标普500: 6440.0 (-0.32%)" in md
        assert "- COMEX黄金: 数据获取失败" in md
        assert "## 海外要闻" in md and "## 国内要闻" in md
        assert "## 上市公司公告" in md
        assert "不构成投资建议" in md

    def test_gaps_and_truncated_note(self):
        md = formatter.render(_payload(
            status="partial", gaps=["公告采集触达预算被截断（部分结果）"],
            announcements={"grouped": {}, "status": "truncated", "raw_count": 5,
                           "error": None}))
        assert "> ⚠️ 公告采集触达预算被截断" in md
        assert "公告采集触达预算被截断，以下为部分结果" in md

    def test_ann_group_limit(self):
        rows = [_ann(f"{i:06d}", f"股{i}", "关于停牌的公告", "2026-08-13 16:00:00")
                for i in range(C.ANN_PER_GROUP_LIMIT + 3)]
        md = formatter.render(_payload(
            announcements={"grouped": {"停复牌": rows}, "status": "complete",
                           "raw_count": len(rows), "error": None}))
        assert f"### 停复牌（{C.ANN_PER_GROUP_LIMIT}/{len(rows)}）" in md
        assert md.count("关于停牌的公告") == C.ANN_PER_GROUP_LIMIT

    def test_news_topic_limit_and_star(self):
        cands = service.flash_filter.filter_items(
            [_flash_item(f"2026-08-14 06:{i:02d}:00", f"美股要闻第{i}条", important=(i == 0))
             for i in range(C.NEWS_PER_TOPIC_LIMIT + 5)],
            classify.load_news_keywords(CONFIG))
        md = formatter.render(_payload(news={
            "candidates": cands, "topic_order": ["海外要闻", "国内要闻"],
            "source_status": "complete", "raw_count": 50}))
        assert f"## 海外要闻（{C.NEWS_PER_TOPIC_LIMIT}/{len(cands)} 条）" in md
        assert md.count("美股要闻第") == C.NEWS_PER_TOPIC_LIMIT
        assert "⭐" in md

    def test_push_body_budget(self):
        big_md = "# 盘前早报 · 2026-08-14\n\n" + "\n".join(
            f"## 段落{j}\n" + "\n".join(f"- 条目{j}-{k} " + "占位内容" * 30
                                       for k in range(50))
            for j in range(10))
        push = formatter.build_push_body(big_md, "2026-08-14")
        assert len(push.encode("utf-8")) <= 18_000
        assert "data/reports/morning-brief/2026-08-14.md" in push


# ---------- 窗口解析 ----------

class TestResolveWindows:
    def test_date_backfill_anchors(self):
        reg = _full_registry()
        w = service.resolve_windows(reg, "2026-08-14")  # 周五
        assert w["date"] == "2026-08-14"
        assert w["prev_trade_date"] == "2026-08-13"
        assert w["news_window"] == (datetime(2026, 8, 13, 20, 0),
                                    datetime(2026, 8, 14, 8, 0))
        assert w["ann_window"][0] == datetime(2026, 8, 13, 15, 0)

    def test_monday_spans_weekend(self):
        reg = _full_registry()
        w = service.resolve_windows(reg, "2026-08-17")  # 周一
        assert w["prev_trade_date"] == "2026-08-14"

    def test_now_used_without_date(self):
        reg = _full_registry()
        now = datetime(2026, 8, 14, 7, 55, tzinfo=service.TZ)
        w = service.resolve_windows(reg, None, now=now)
        assert w["news_window"][1] == datetime(2026, 8, 14, 7, 55)

    def test_delayed_run_clamped_to_cutoff(self):
        # launchd 延迟触发/中午手动补跑不得扩窗:08:00 后终点钳制回 08:00
        reg = _full_registry()
        now = datetime(2026, 8, 14, 12, 3, tzinfo=service.TZ)
        w = service.resolve_windows(reg, None, now=now)
        assert w["news_window"][1] == datetime(2026, 8, 14, 8, 0)

    def test_calendar_failure_marks_gap_not_silent(self):
        # 日历源失败不得静默回退昨日缩窗:必须落 gap（→ partial）
        reg = _full_registry()
        reg.responses["is_trade_day"] = None  # 全部失败
        w = service.resolve_windows(reg, "2026-08-17")  # 周一
        assert w["prev_trade_date"] == "2026-08-16"  # 回退昨日
        assert any("无法核实" in g for g in w["gaps"])

    def test_db_calendar_preferred_over_provider(self):
        # DB trade_calendar 可判时不再外呼 provider
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER)")
        conn.execute("INSERT INTO trade_calendar VALUES ('2026-08-13', 1)")
        reg = _full_registry()
        reg.responses["is_trade_day"] = None  # provider 不可用
        w = service.resolve_windows(reg, "2026-08-14", conn=conn)
        assert w["prev_trade_date"] == "2026-08-13"
        assert w["gaps"] == []


# ---------- 状态合并 ----------

def _collect_ok(items=None, status=flash_collector.STATUS_COMPLETE):
    def _fn(start, end):
        return flash_collector.CollectResult(
            status=status, items=items or [], raw_count=len(items or []))
    return _fn


class TestBuildBrief:
    def test_complete(self):
        reg = _full_registry(ann_items=[
            _ann("000001", "平安银行", "关于复牌的公告", "2026-08-13 16:00:00")])
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14",
            collect_fn=_collect_ok([_flash_item("2026-08-14 06:00:00", "美股上涨")]))
        assert brief.status == "complete"
        assert brief.payload["gaps"] == []
        assert brief.payload["news"]["candidates"][0].topic == "海外要闻"
        assert list(brief.payload["announcements"]["grouped"]) == ["停复牌"]

    def test_jin10_failed_is_source_failed(self):
        reg = _full_registry()
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14",
            collect_fn=_collect_ok(status=flash_collector.STATUS_FAILED))
        assert brief.status == "source_failed"
        assert any("金十" in g for g in brief.payload["gaps"])
        # 其余段仍采集（报告含可用段落）
        assert brief.payload["overnight"]["indices"]

    def test_jin10_failed_still_collects_quote_gaps(self):
        # source_failed 与其他缺口同一路径归集:行情缺口不得被金十失败遮蔽（门1 finding）
        reg = _full_registry()
        reg.responses["get_commodity"] = None
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14",
            collect_fn=_collect_ok(status=flash_collector.STATUS_FAILED))
        assert brief.status == "source_failed"
        assert any("金十" in g for g in brief.payload["gaps"])
        assert any("隔夜行情缺口" in g for g in brief.payload["gaps"])

    def test_jin10_truncated_is_partial(self):
        reg = _full_registry()
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14",
            collect_fn=_collect_ok(status=flash_collector.STATUS_TRUNCATED))
        assert brief.status == "partial"

    def test_ann_failure_is_partial_not_fatal(self):
        reg = _full_registry()
        reg.responses["get_market_announcements_range"] = None  # 未配置 → 失败
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14", collect_fn=_collect_ok())
        assert brief.status == "partial"
        assert brief.payload["announcements"]["error"]

    def test_backfill_flag_and_note(self):
        reg = _full_registry()
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14", collect_fn=_collect_ok())
        assert brief.payload["backfill"] is True
        assert "补跑档：隔夜行情为取数时最新快照" in formatter.render(brief.payload)
        live = service.build_brief(
            CONFIG, reg, now=datetime(2026, 8, 14, 8, 0, tzinfo=service.TZ),
            collect_fn=_collect_ok())
        assert live.payload["backfill"] is False
        assert "补跑档" not in formatter.render(live.payload)

    def test_quote_failure_is_partial(self):
        reg = _full_registry()
        reg.responses["get_commodity"] = None
        brief = service.build_brief(
            CONFIG, reg, date_str="2026-08-14", collect_fn=_collect_ok())
        assert brief.status == "partial"
        assert any("隔夜行情缺口" in g for g in brief.payload["gaps"])


# ---------- 巨潮分页（mock requests） ----------

def _ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


def _resp(total, rows):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"totalAnnouncement": total, "announcements": rows})


def _row(ann_id, code, title, dt: datetime):
    return {"announcementId": ann_id, "secCode": code, "secName": f"名{code}",
            "announcementTitle": title, "announcementTime": _ms(dt), "orgId": "org"}


class TestCninfoRange:
    def _provider(self):
        from providers.akshare_provider import AkshareProvider
        return AkshareProvider()

    def test_window_filter_pagination_and_dedup(self):
        p = self._provider()
        day = datetime(2026, 8, 13, 0, 0)
        pages = {
            ("2026-08-13", 1): _resp(60, [
                _row("a1", "000001", "关于停牌的公告", day.replace(hour=21)),
                _row("a2", "000002", "复牌提示", day.replace(hour=20))]),
            ("2026-08-13", 2): _resp(60, [
                _row("a2", "000002", "复牌提示", day.replace(hour=20)),  # 页边界重复
                _row("a3", "000003", "早于窗口", day.replace(hour=10))]),
            ("2026-08-14", 1): _resp(30, [
                _row("b1", "000004", "次日凌晨公告", datetime(2026, 8, 14, 0, 30))]),
        }

        def fake_post(url, data=None, timeout=None, headers=None):
            return pages[(data["seDate"][:10], int(data["pageNum"]))]

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success
        assert r.data["status"] == "complete"
        ids = [i["code"] for i in r.data["items"]]
        assert ids == ["000004", "000001", "000002"]  # 新→旧、去重、窗口过滤

    def test_early_stop_on_page_older_than_window(self):
        p = self._provider()
        day = datetime(2026, 8, 13, 0, 0)
        calls = []

        def fake_post(url, data=None, timeout=None, headers=None):
            calls.append((data["seDate"][:10], data["pageNum"]))
            if data["seDate"][:10] == "2026-08-14":
                return _resp(0, [])
            if int(data["pageNum"]) == 1:
                return _resp(3000, [_row("a1", "000001", "停牌", day.replace(hour=10))])
            raise AssertionError("整页早于窗口起点后不应继续翻页")

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success
        assert ("2026-08-13", "2") not in calls

    def test_budget_truncation_flagged(self):
        p = self._provider()

        def fake_post(url, data=None, timeout=None, headers=None):
            n = int(data["pageNum"])
            return _resp(30000, [_row(f"x{n}-{i}", "000001", "停牌",
                                      datetime(2026, 8, 13, 23, 59 - n))
                                 for i in range(30)])

        with patch.object(type(p), "_CNINFO_ANN_MAX_PAGES", 2), \
             patch("providers.akshare_provider.requests.post", side_effect=fake_post):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success
        assert r.data["status"] == "truncated"

    def test_reversed_window_rejected(self):
        p = self._provider()
        r = p.get_market_announcements_range(
            "2026-08-14 08:00:00", "2026-08-13 15:00:00")
        assert not r.success

    def test_transient_failure_retried(self):
        # 瞬时连接超时重试后成功(实测巨潮偶发)
        p = self._provider()
        day = datetime(2026, 8, 13, 0, 0)
        attempts = []

        def fake_post(url, data=None, timeout=None, headers=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("connect timeout")
            if data["seDate"][:10] == "2026-08-14":
                return _resp(0, [])
            return _resp(1, [_row("a1", "000001", "停牌", day.replace(hour=21))])

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post), \
             patch("providers.akshare_provider.time.sleep"):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success and len(r.data["items"]) == 1

    def test_mid_pagination_failure_degrades_to_truncated(self):
        # 中途页重试耗尽:保留已采页降级 truncated,不整段丢弃也不伪装 complete
        p = self._provider()
        day = datetime(2026, 8, 13, 0, 0)

        def fake_post(url, data=None, timeout=None, headers=None):
            if data["seDate"][:10] == "2026-08-14":
                return _resp(0, [])
            if int(data["pageNum"]) == 1:
                return _resp(60, [_row("a1", "000001", "停牌", day.replace(hour=21))])
            raise ConnectionError("connect timeout")

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post), \
             patch("providers.akshare_provider.time.sleep"):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success
        assert r.data["status"] == "truncated"
        assert [i["code"] for i in r.data["items"]] == ["000001"]

    def test_first_page_failure_is_source_error(self):
        p = self._provider()

        def fake_post(url, data=None, timeout=None, headers=None):
            raise ConnectionError("connect timeout")

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post), \
             patch("providers.akshare_provider.time.sleep"):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert not r.success

    def test_order_violation_disables_early_stop(self):
        # 跨页乱序（后页出现比前页更新的条目）→ 降序假设被推翻,禁用早停继续翻页,
        # 窗口内公告不得被静默丢弃（门1 finding:早停依赖服务端排序假设）
        p = self._provider()
        day = datetime(2026, 8, 13, 0, 0)
        pages = {
            ("2026-08-14", 1): _resp(0, []),
            ("2026-08-13", 1): _resp(90, [
                _row("a1", "000001", "停牌", day.replace(hour=21))]),
            # 页2 max(22:00) > 页1 min(21:00) = 乱序证据;且含早于窗口起点的条目
            ("2026-08-13", 2): _resp(90, [
                _row("a2", "000002", "复牌", day.replace(hour=22)),
                _row("a3", "000003", "早于窗口", day.replace(hour=10))]),
            ("2026-08-13", 3): _resp(90, [
                _row("a4", "000004", "重大合同中标", day.replace(hour=16))]),
        }

        def fake_post(url, data=None, timeout=None, headers=None):
            return pages[(data["seDate"][:10], int(data["pageNum"]))]

        with patch("providers.akshare_provider.requests.post", side_effect=fake_post):
            r = p.get_market_announcements_range(
                "2026-08-13 15:00:00", "2026-08-14 08:00:00")
        assert r.success
        codes = [i["code"] for i in r.data["items"]]
        assert "000004" in codes  # 乱序后第 3 页仍被翻到


class TestWriteReport:
    def test_atomic_write_with_unique_temp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(formatter, "REPORT_DIR", tmp_path)
        path = formatter.write_report("# 早报\n", "2026-08-14")
        assert path.read_text(encoding="utf-8") == "# 早报\n"
        # 无残留临时文件
        assert [p.name for p in tmp_path.iterdir()] == ["2026-08-14.md"]
