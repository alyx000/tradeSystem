"""安图生物盘中五个月线：前四月末加本月最新价，并保护历史证据和提醒。"""
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import (
    ANTU_BIO_BREAKOUT_39_44_20261105_1202 as BREAKOUT,
    ANTU_BIO_NEAR_MONTHLY_MA5_20261123_1218 as RULE,
    ANTU_BIO_NEAR_WEEKLY_MA5_20261103_30 as WEEKLY,
    DEFAULT_RULES,
)
from services.intraday_monitor.monthly import monthly_history_dates
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import TZ, _calendar, _DynamicRegistry, _Pusher

MONTH_ENDS = ["2026-07-31", "2026-08-31", "2026-09-30", "2026-10-29"]
DAY_START = date(2026, 7, 1)
DAYS = [DAY_START + timedelta(days=i) for i in range((date(2026, 12, 22) - DAY_START).days)]
# 模拟10月最后一个工作日休市；月末应取上一个开放日。
OPEN = [d.isoformat() for d in DAYS if d.weekday() < 5 and d != date(2026, 10, 30)]
CLOSED = [d.isoformat() for d in DAYS if d.isoformat() not in OPEN]


@pytest.fixture
def monitor(tmp_path):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE trade_calendar ADD COLUMN exchange TEXT NOT NULL DEFAULT 'SSE'")
    r, p = _DynamicRegistry(price=10), _Pusher()
    state = tmp_path / "state.json"
    set_history(r, "2026-11-23")
    return r, p, db, state


def set_history(r, day):
    r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    start = ("2026-07-01" if day < "2026-12-01" else "2026-08-01")
    r.history_dates = [d for d in OPEN if start <= d < day]
    r.history_factors = [1 if d <= "2026-08-31" else 2 for d in r.history_dates]
    qfq_closes = dict(zip(MONTH_ENDS + ["2026-11-30"], (8, 10, 10, 12, 10)))
    r.history_closes = [
        qfq_closes.get(d, 14) * 2 / factor
        for d, factor in zip(r.history_dates, r.history_factors)
    ]
    r.pre_close = r.history_closes[-1]


def tick(monitor, **kwargs):
    r, p, db, state = monitor
    return run_check(r, rules=(RULE,), now=r.now, db_path=db, state_path=state,
                     pusher_factory=lambda: p, **kwargs)


def test_identity_and_twenty_open_days():
    assert {r for r in DEFAULT_RULES if r.code == "603658.SH"} == {RULE, WEEKLY, BREAKOUT}
    assert (RULE.instrument_name, RULE.direction, RULE.inclusive, RULE.proximity_pct) == ("安图生物", "near", True, 1)
    assert (RULE.threshold_period, RULE.threshold_window, RULE.threshold_provider) == ("month", 5, "tushare")
    assert RULE.valid_from == date(2026, 11, 23) and RULE.valid_until == date(2026, 12, 18)
    assert sum(RULE.is_effective_on(date.fromisoformat(d)) for d in OPEN) == 20
    with pytest.raises(ValueError, match="仅支持动态"):
        replace(RULE, threshold_mode="previous_close_ma")


def test_month_end_sampling_and_november_december_rollover(monitor):
    r, p, db, _ = monitor
    history, basis = monthly_history_dates("2026-11-23", 4, db_path=db)
    assert basis == MONTH_ENDS
    assert history[0] == "2026-07-01" and history[-1] == "2026-11-20"
    result = tick(monitor)
    assert result["status"] == "complete"
    event = result["events"][0]
    assert event["threshold"] == pytest.approx(10)
    assert event["threshold_basis_dates"] == basis and event["threshold_period"] == "month"
    assert "4 个已完成交易月" in p.messages[0][1] and "本月最新价" in p.messages[0][1]
    set_history(r, "2026-12-01")
    r.price = 10.5
    result = tick(monitor)
    assert result["status"] == "complete"
    assert result["events"][0]["threshold"] == pytest.approx((42 + 10.5) / 5)
    assert result["events"][0]["threshold_basis_dates"] == MONTH_ENDS[1:] + ["2026-11-30"]
    assert [args[-2:] for _, args in r.history_calls] == [
        ("2026-07-01", "2026-11-20"), ("2026-07-01", "2026-11-20"),
        ("2026-08-03", "2026-11-30"), ("2026-08-03", "2026-11-30"),
    ]


@pytest.mark.parametrize("ratio", [0.99, 1.01])
def test_inclusive_band_edges(ratio):
    price = 40 * ratio / (5 - ratio)
    assert RULE.is_active(price, threshold=RULE.resolve_threshold({"price": price}, historical_closes=[10] * 4))


def test_daily_anchor_refresh_intraday_price_cache_and_repeat_alert(monitor):
    r, p, _, state = monitor
    counts = []
    for price in (10, 10.01, 11, 10):
        r.price = price
        result = tick(monitor)
        assert result["status"] == "complete"
        counts.append(len(result["events"]))
        saved = json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((40 + price) / 5)
        r.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1] and len(r.history_calls) == 2 and len(p.messages) == 2
    set_history(r, "2026-11-24")
    assert tick(monitor)["pushed"]
    assert len(r.history_calls) == 4  # 即使月末基准日与昨收数值不变，仍逐日重验。


@pytest.mark.parametrize("bad", ["calendar", "month-end-bar", "factor", "anchor", "stale"])
def test_missing_or_stale_evidence_never_alerts(monitor, bad):
    r, p, db, state = monitor
    if bad == "calendar":
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-08-02'")
    elif bad == "month-end-bar":
        i = r.history_dates.index("2026-10-29")
        for seq in (r.history_dates, r.history_closes, r.history_factors):
            seq.pop(i)
    elif bad == "factor":
        r.history_factors.pop()
    elif bad == "anchor":
        r.pre_close = 0
    else:
        r.quote_overrides[RULE.code] = {"quote_date": "2026-11-20"}
    result = tick(monitor)
    assert result["status"] == "source_failed" and not result["events"] and not p.messages
    assert not state.exists() or RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_last_open_day_and_expiry(monitor):
    r, p, _, state = monitor
    set_history(r, "2026-12-18")
    r.price = 10.5
    result = tick(monitor, dry_run=True)
    assert result["status"] == "dry_run" and result["events"] and not state.exists() and not p.messages
    p.succeed = False
    assert tick(monitor)["status"] == "push_failed"
    r.requested_codes.clear()
    p.messages.clear()
    r.now = datetime(2026, 12, 21, 10, tzinfo=TZ)
    assert tick(monitor)["status"] == "no_active_rules"
    assert not r.requested_codes and not p.messages
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"]


@pytest.mark.parametrize("kind", ["identical", "certified", "unverified", "wrong_stock", "dirty_date"])
def test_live_rule_replays_factor_duplicates_through_production_provider(monitor, monkeypatch, kind):
    from providers.tushare_provider import TushareProvider
    from services.intraday_monitor.service import rule_status

    registry, pusher, db, state = monitor
    # Establish an older successful state first; failed attempts must leave its clock unchanged.
    assert tick(monitor)["status"] == "complete"
    last_good = rule_status(rules=(RULE,), now=registry.now, state_path=state)["rules"][0]
    set_history(registry, "2026-11-25")
    provider = TushareProvider({})
    provider._initialized = True
    provider.pro = object()
    bars = [{"ts_code": RULE.code, "trade_date": d.replace("-", ""), "adj_factor": f}
            for d, f in zip(registry.history_dates, registry.history_factors)]
    base = next(row for row in bars if row["trade_date"] == "20260930")
    extra = {**base, "adj_factor": "2.0000", "vendor_sequence": 2}
    if kind in ("certified", "unverified"):
        extra["adj_factor"] = 9
    elif kind == "wrong_stock":
        extra["ts_code"] = "603659.SH"
    elif kind == "dirty_date":
        extra["trade_date"] += "-bad"
    bars.append(extra)
    def archive(day, cutoff):
        assert (day, cutoff) == ("2026-09-30", "2026-11-24")
        return {"603658": 2} if kind == "certified" else {}
    monkeypatch.setattr("providers.adj_factor_evidence.archived_factors", archive)
    monkeypatch.setattr(provider, "_query_records", lambda *args, **kwargs: bars)
    monkeypatch.setattr(provider, "get_adj_factor", lambda *args: pytest.fail("禁止取未来证据"))
    original = registry.call_specific
    def routed(source, method, *args):
        if method == "get_stock_adj_factor_range":
            return provider.get_stock_adj_factor_range(*args)
        return original(source, method, *args)
    monkeypatch.setattr(registry, "call_specific", routed)
    result = tick(monitor)
    status = rule_status(rules=(RULE,), now=registry.now, state_path=state)["rules"][0]
    if kind in ("identical", "certified"):
        assert result["status"] == "complete" and status["last_success_at"] == registry.now.isoformat()
        assert result["events"][0]["threshold"] == pytest.approx(10)
    else:
        assert result["status"] == "source_failed" and not result["events"]
        assert status["last_success_at"] == last_good["last_success_at"]
        assert status["last_quote_at"] == last_good["last_quote_at"]
        assert status["last_valid_quote_at"] == registry.now.isoformat()
        assert status["today"]["last_result"] == "calculation_gap"
        assert status["today"]["day"] == registry.now.date().isoformat()
        assert status["today"]["calculation_gaps"] == 1 and status["today"]["quote_gaps"] == 0


def test_status_readonly_for_missing_old_and_future_fields(monitor):
    from services.intraday_monitor.service import rule_status

    registry, _, db, path = monitor
    assert rule_status(rules=(RULE,), now=registry.now, state_path=path)["rules"][0]["last_success_at"] is None
    assert not path.exists() and not path.with_suffix(".json.lock").exists()
    assert tick(monitor)["status"] == "complete"
    saved = json.loads(path.read_text())
    del saved["rules"][RULE.rule_id]["updated_at"]
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    row = rule_status(rules=(RULE,), now=registry.now, state_path=path)["rules"][0]
    assert row["last_success_at"] is None and row["last_quote_at"] == registry.now.isoformat()
    expired = registry.now.replace(month=12, day=21)
    assert rule_status(rules=(RULE,), now=expired, state_path=path)["rules"] == []
    assert rule_status(rules=(RULE,), now=expired, state_path=path, include_inactive=True)["rules"]
    assert path.read_bytes() == before
