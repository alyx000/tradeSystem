"""value-watch service 编排 + formatter：三档运行语义 / 推送账本闭环 / 红线文案。"""
from __future__ import annotations

import datetime
import unittest.mock as mock

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from db import queries as Q
from db.connection import get_connection
from db.schema import init_schema
from services.value_watch import repo, service

SH = ZoneInfo("Asia/Shanghai")
TARGET = "2026-07-21"   # 周二
NOW_EVENING = datetime.datetime(2026, 7, 21, 21, 45, tzinfo=SH)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "vw_svc.db")
    init_schema(c)
    _seed_calendar(c)
    yield c
    c.close()


def _seed_calendar(c):
    rows = []
    d = datetime.date(2026, 1, 1)
    while d.year == 2026:
        rows.append({"date": d.isoformat(), "is_open": 1 if d.weekday() < 5 else 0})
        d += datetime.timedelta(days=1)
    Q.upsert_trade_calendar(c, rows)


def _mk_provider(bank_closes):
    """bank_closes: [(date,close)] → sw_daily DataFrame。"""
    p = mock.MagicMock()
    p.pro.sw_daily.return_value = pd.DataFrame({
        "trade_date": [d.replace("-", "") for d, _ in bank_closes],
        "close": [c for _, c in bank_closes],
    })
    return p


def _mk_registry(stock_map):
    """stock_map: code → [(date,close,vol)]。"""
    r = mock.MagicMock()

    def call(cap, code=None, *a, **k):
        m = mock.MagicMock()
        if cap == "get_stock_daily_range":
            rows = stock_map.get(code)
            m.success = rows is not None
            m.data = ([{"trade_date": d, "close": c, "vol": v} for d, c, v in rows]
                      if rows else None)
            m.error = "" if rows else "no data"
        else:   # is_trade_day / get_trade_calendar 等走真实 conn 路径,不经此 mock
            m.success, m.data, m.error = False, None, "not mocked"
        return m

    r.call.side_effect = call
    return r


def _drawdown_bank(pct_from=0.0, pct_to=12.0, days=140):
    """构造银行指数序列:前段横盘 100,末段回撤到位。"""
    out = []
    d = datetime.date(2026, 1, 5)
    flat = days - 10
    for i in range(days):
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        if i < flat:
            close = 100.0
        else:
            frac = (i - flat + 1) / 10
            close = 100.0 * (1 - (pct_from + (pct_to - pct_from) * frac) / 100)
        out.append((d.isoformat(), round(close, 2)))
        d += datetime.timedelta(days=1)
    # 末日对齐 TARGET
    out[-1] = (TARGET, out[-1][1])
    return out


def _quiet_stock(code_days=140, end=TARGET):
    out = []
    d = datetime.date(2026, 1, 5)
    for _ in range(code_days):
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        out.append((d.isoformat(), 10.0, 100.0))
        d += datetime.timedelta(days=1)
    out[-1] = (end, 10.0, 100.0)
    return out


def _stock_map(**overrides):
    from services.value_watch.config import DRAWDOWN_TARGETS, LADDER_CODES, SCARCITY_CODES
    codes = (set(LADDER_CODES) | set(SCARCITY_CODES) |
             {c for c in DRAWDOWN_TARGETS if not c.endswith(".SI")})
    m = {c: _quiet_stock() for c in codes}
    m.update(overrides)
    return m


def _pusher_cls(sent, ok=True):
    inst = mock.MagicMock()
    inst.initialize.return_value = True
    def send_markdown(title, content):
        sent.append((title, content))
        return ok
    inst.send_markdown.side_effect = send_markdown
    return mock.MagicMock(return_value=inst)


def _run(conn, *, persist, push, sent=None, push_ok=True, bank=None, stocks=None,
         now=NOW_EVENING, date=TARGET):
    provider = _mk_provider(bank or _drawdown_bank())
    registry = _mk_registry(stocks or _stock_map())
    with mock.patch.object(service, "DingTalkPusher",
                           _pusher_cls(sent if sent is not None else [], ok=push_ok)):
        return service.run_daily(conn, registry, provider, date,
                                 persist=persist, push=push, now=now)


def test_dry_run_no_persist_no_push(conn):
    sent = []
    md = _run(conn, persist=False, push=False, sent=sent)
    assert md is not None and "候选事件" in md
    assert repo.get_snapshot(conn, TARGET) is None      # 不落库
    assert sent == []                                    # 不推送


def test_persist_push_writes_ledger(conn):
    sent = []
    md = _run(conn, persist=True, push=True, sent=sent)
    assert md is not None
    snap = repo.get_snapshot(conn, TARGET)
    assert snap is not None
    assert len(sent) >= 1                                # 回撤 12% 应触发 10% 档事件
    assert any("drawdown:801780.SI:10" in k for k in snap["sent_events"])
    # 红线:不含祈使句买卖指令;含出处与免责声明
    body = sent[0][1]
    assert "teacher_notes#391" in body and "不构成投资建议" in body


def test_same_day_rerun_no_repush(conn):
    sent = []
    _run(conn, persist=True, push=True, sent=sent)
    n1 = len(sent)
    _run(conn, persist=True, push=True, sent=sent)      # 同日重跑
    assert len(sent) == n1                               # 账本去重不重推


def test_push_failure_not_ledgered_retry_next_run(conn):
    sent = []
    _run(conn, persist=True, push=True, sent=sent, push_ok=False)
    snap = repo.get_snapshot(conn, TARGET)
    assert snap["sent_events"] == []                     # 失败不落账本
    sent2 = []
    _run(conn, persist=True, push=True, sent=sent2, push_ok=True)
    assert len(sent2) >= 1                               # 下次运行补推


def test_historical_date_never_pushes(conn):
    """persist 历史日期:落库但闸门拦推(now 在目标日之后)。"""
    sent = []
    later = datetime.datetime(2026, 7, 22, 21, 45, tzinfo=SH)
    md = _run(conn, persist=True, push=True, sent=sent, now=later, date=TARGET)
    assert md is not None
    assert repo.get_snapshot(conn, TARGET) is not None
    assert sent == []


def test_no_push_flag_persists_without_push(conn):
    sent = []
    _run(conn, persist=True, push=False, sent=sent)
    assert repo.get_snapshot(conn, TARGET) is not None
    assert sent == []


def test_source_failure_isolated(conn):
    """银行指数源失败:该标的标 source_failed,其余标的照常,任务不中断。"""
    provider = mock.MagicMock()
    provider.pro.sw_daily.side_effect = RuntimeError("down")
    registry = _mk_registry(_stock_map())
    with mock.patch.object(service, "DingTalkPusher", _pusher_cls([])):
        md = service.run_daily(conn, registry, provider, TARGET,
                               persist=True, push=False, now=NOW_EVENING)
    assert md is not None
    snap = repo.get_snapshot(conn, TARGET)
    assert snap["payload"]["source_status"]["801780.SI"] == "source_failed"
    assert snap["payload"]["source_status"]["600436.SH"] == "ok"


def test_no_push_state_same_as_push(conn, tmp_path):
    """状态-通知分离回归:--no-push 与推送两跑的 payload 状态一致(通知开关不影响重放)。"""
    c2 = get_connection(tmp_path / "vw_svc2.db")
    init_schema(c2)
    _seed_calendar(c2)
    _run(conn, persist=True, push=False)
    _run(c2, persist=True, push=True, sent=[])
    p1 = repo.get_snapshot(conn, TARGET)["payload"]
    p2 = repo.get_snapshot(c2, TARGET)["payload"]
    assert p1["drawdown"] == p2["drawdown"]
    assert p1["scarcity"] == p2["scarcity"]
    c2.close()


def test_insufficient_identity_reported_not_pushed(conn):
    Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                     entry_price=9.0, status="active", input_by="manual")
    conn.execute("UPDATE holdings SET entry_date=NULL WHERE stock_code='601939.SH'")
    conn.commit()
    sent = []
    _run(conn, persist=True, push=True, sent=sent)
    snap = repo.get_snapshot(conn, TARGET)
    ladder = snap["payload"]["ladder"]
    assert any(p.get("insufficient_identity") for p in ladder)
    assert not any("ladder" in k for k in snap["sent_events"])   # 身份不完整不推阶梯


def test_stale_source_downgraded_not_ok(conn):
    """门2 G3 high:目标日为交易日而源末根停在 T-1 → stale_source 降级,不按当日重放。"""
    stale_end = "2026-07-20"   # 目标日 07-21(交易日),源只给到 07-20
    bank = [(d, c) for d, c in _drawdown_bank() if d <= stale_end]
    stocks = {c: [r for r in rows if r[0] <= stale_end]
              for c, rows in _stock_map().items()}
    sent = []
    _run(conn, persist=True, push=True, sent=sent, bank=bank, stocks=stocks)
    snap = repo.get_snapshot(conn, TARGET)
    assert snap["payload"]["source_status"]["801780.SI"] == "stale_source"
    assert snap["payload"]["drawdown"]["801780.SI"] is None
    assert sent == []   # 全部标的陈旧 → 无事件可推


def test_truncation_error_isolated_per_code(conn):
    """门2 G3 med-3:sw_daily 恰 2000 行截断 raise → 该标的降级,其余标的照常完成。"""
    provider = mock.MagicMock()
    provider.pro.sw_daily.return_value = pd.DataFrame({
        "trade_date": [f"2026{i:04d}" for i in range(2000)], "close": [1.0] * 2000})
    registry = _mk_registry(_stock_map())
    with mock.patch.object(service, "DingTalkPusher", _pusher_cls([])):
        md = service.run_daily(conn, registry, provider, TARGET,
                               persist=True, push=False, now=NOW_EVENING)
    assert md is not None
    snap = repo.get_snapshot(conn, TARGET)
    assert snap["payload"]["source_status"]["801780.SI"] == "source_failed"
    assert snap["payload"]["source_status"]["600436.SH"] == "ok"


def test_dry_run_uses_real_ledger(conn):
    """门2 G3 med-2:dry-run 只读真实账本——已发送事件不得重新出现在候选预览。"""
    sent = []
    _run(conn, persist=True, push=True, sent=sent)          # 先真实发送记账
    assert len(sent) >= 1
    md = _run(conn, persist=False, push=False)              # dry-run
    assert "drawdown:801780.SI:10" not in md.split("## 本次候选事件")[-1]


def test_report_fact_judgment_layering(conn):
    """门2 G3 med-4:③ 段周数/日期标[事实]、状态标[判断];候选区带[事实]。"""
    _run(conn, persist=True, push=False)
    md = service.run_report(conn, None)
    scarcity_seg = md.split("## ③")[-1]
    assert "[事实] 600436.SH: 完成周" in scarcity_seg
    assert "[判断] 状态" in scarcity_seg


def test_week_remaining_open_days_calendar_missing_conservative(tmp_path):
    """live contract check 回归:空库无日历 → 保守 True(当周未完成),不产伪完成周。"""
    c = get_connection(tmp_path / "empty_cal.db")
    init_schema(c)
    assert service._week_has_remaining_open_days(c, "2026-07-22") is True   # 周三,无日历
    c.close()


def test_week_partial_calendar_gap_conservative(conn):
    """门2 G3 round3:窗口内有休市行但缺后续开放日记录 → 保守未完成,不产伪完成周。"""
    # 2026-07-22(周三):删掉周五 07-24 的记录,留周四 07-23(改为休市)与周末
    conn.execute("DELETE FROM trade_calendar WHERE date='2026-07-24'")
    conn.execute("UPDATE trade_calendar SET is_open=0 WHERE date='2026-07-23'")
    conn.commit()
    # 窗口 07-23~07-26 应有 4 行,现仅 3 行(缺 07-24) → 无法确认 → True(未完成)
    assert service._week_has_remaining_open_days(conn, "2026-07-22") is True


def test_week_remaining_open_days_with_calendar(conn):
    assert service._week_has_remaining_open_days(conn, "2026-07-22") is True   # 周三,周四五开市
    assert service._week_has_remaining_open_days(conn, "2026-07-17") is False  # 周五,周六日休
    assert service._week_has_remaining_open_days(conn, "2026-07-19") is False  # 周日天然完成


def test_run_report_renders_snapshot_only(conn):
    assert "无快照" in service.run_report(conn, None)
    _run(conn, persist=True, push=False)
    md = service.run_report(conn, None)
    assert TARGET in md and "logic_version" in md
    assert "[事实]" in md or "[判断]" in md
