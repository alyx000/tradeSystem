"""固定时点样本、收盘分钟覆盖与失败重试。"""
from datetime import datetime
import json
import sqlite3

import pytest
from providers.base import DataResult
from services import course_core_minutes as service
from services.board_break_intraday import data

DAY = "2026-11-27"
CORE = "688820.SH"
PEER = "002242.SZ"
SOURCE = "sina:CN_MarketDataService.getKLineData:1min"


def complete_minutes(code):
    rows = [dict(ts_code=code, trade_time=t, open=10, low=10, close=10, high=10, vol=100, amount=1000)
            for t in data.expected_minutes(DAY, "15:00")]
    normalized = data.normalize_minutes(rows, code, DAY, "15:00")
    normalized.update(source=SOURCE, requested_at=DAY + "T20:00:00+08:00", raw_sha256=data.digest(rows))
    return normalized


class Market:
    def __init__(self):
        self.missing = {PEER}
        self.suspended = set()
        self.requests = []
        self.no_prices = False

    def contexts(self, day, codes):
        out = {code: dict(suspension=dict(full_day=code in self.suspended)) for code in codes}
        for code in codes - self.suspended:
            out[code]["reference"] = {"pre_close": 9.8, "close": 10}
            if not self.no_prices:
                out[code]["limits"] = {"up_limit": 11, "down_limit": 9}
        return out, []

    def minutes(self, code, day, cutoff, mode, **kwargs):
        self.requests.append(code)
        if code in self.missing:
            raise data.EvidenceError("分钟覆盖不足")
        return complete_minutes(code)


@pytest.fixture
def args(tmp_path):
    db = tmp_path / "db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE trade_calendar(date text, is_open integer, exchange text)")
        c.execute("""WITH RECURSIVE dates(d) AS (SELECT date('2026-01-01') UNION ALL SELECT date(d,'+1 day')
                     FROM dates WHERE d<'2026-12-31') INSERT INTO trade_calendar SELECT d,
                     cast(strftime('%w',d) as integer) BETWEEN 1 AND 5,'SSE' FROM dates""")
    def universe(conn, day):
        result = dict(as_of_date="2026-11-26", trade_date=DAY, status="complete", members=[
            {"code": CORE, "role": "core"}, {"code": PEER, "role": "peer"}], gaps=[],
            cutoff_at=DAY+"T09:30:00+08:00", rule_version="v1")
        return dict(**result, sha256=data.digest(result))

    return dict(db_path=db, root=tmp_path / "out", universe_builder=universe,
                now=datetime.fromisoformat("2026-11-28T10:00:00+08:00"))


def test_retries_missing_only_and_preserves_population(args):
    market = Market()
    def run():
        return service.capture_day(DAY, "USER", source_factory=lambda: market, **args)
    first = run()
    assert first["status"] == "partial" and first["coverage"]["minute_complete"] == 1
    assert len(first["stocks"][CORE]["minutes"]["rows"]) == 240
    market.requests.clear()
    market.missing.clear()
    def forbidden(*_):
        raise AssertionError("已固定名单不得用新名单替换")
    args["universe_builder"] = forbidden
    result = run()
    assert result["status"] == "complete" and market.requests == [PEER]
    assert result["stocks"][CORE] == first["stocks"][CORE]
    assert run() == result
    assert market.requests == [PEER]
    path = args["root"] / f"{DAY}.json"
    obj = json.loads(path.read_text())
    obj["stocks"][CORE]["minutes"]["rows"][0]["close"] = 99
    path.write_text(json.dumps(obj))
    with pytest.raises(data.EvidenceError, match="损坏"):
        run()


def test_suspension_and_missing_context_dont_discard_complete_minutes(args):
    market = Market()
    market.suspended.add(PEER)
    market.no_prices = True
    r = service.capture_day(DAY, "USER", source_factory=lambda: market, **args)
    assert r["coverage"] == {"expected": 2, "complete": 0, "minute_complete": 1, "suspended": 1,
                             "unresolved": 1, "partial_window_only": 0, "reference_verified": 1,
                             "limit_prices_verified": 0, "suspension_verified": 2}
    market.no_prices = False
    again = service.capture_day(DAY, "USER", source_factory=lambda: market, **args)
    assert market.requests == [CORE] and again["status"] == "complete"


@pytest.mark.parametrize("bad", ["missing", "amount", "ordering", "source", "duplicates"])
def test_reject_partial_or_inconsistent_minutes(bad):
    m = complete_minutes(CORE)
    if bad == "missing":
        m["rows"].pop(17)
    if bad == "amount":
        m["rows"][0]["amount"] = 0
    if bad == "ordering":
        m["rows"].reverse()
    if bad == "source":
        m["source"] = "unknown"
    if bad == "duplicates":
        m["rows"][1] = m["rows"][0]
    with pytest.raises(data.EvidenceError):
        service._check_minutes(m, CORE, DAY)


def test_wrong_day_and_duplicate_rows_do_not_poison_other_symbols():
    rows = [{"trade_date": DAY, "ts_code": c, "up_limit": 11, "down_limit": 9} for c in (CORE, PEER, PEER)]
    def parse():
        return service._daily_rows(DataResult(rows, "tushare:stk_limit"), "tushare:stk_limit", DAY,
                                   {CORE, PEER}, ("up_limit", "down_limit"))
    assert set(parse()) == {CORE}
    rows[0]["trade_date"] = "2026-11-26"
    assert parse() == {}


def test_dry_run_not_persisted_and_pre_close_skip(args):
    m = Market()
    service.capture_day(DAY, "USER", dry_run=True, source_factory=lambda: m, **args)
    assert not args["root"].exists()
    args["now"] = datetime.fromisoformat(DAY + "T14:59:59+08:00")
    assert service.capture_day(DAY, "USER", **args)["status"] == "skipped"


def test_hashed_local_limits_and_malformed_source(tmp_path):
    import hashlib
    import pickle
    folder = tmp_path / "volume-buildup" / "runs" / DAY
    folder.mkdir(parents=True)
    target = folder / f"limits-{DAY}.pkl"
    target.write_bytes(pickle.dumps([dict(ts_code=CORE, trade_date=DAY, up_limit=11.5, down_limit=9)]))
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    (folder / "report.json").write_text(json.dumps({"date": DAY, "source_sha256": {str(target): checksum}}))
    (folder / "source-receipts.json").write_text(json.dumps([dict(capability="get_stock_limit_prices", args=[DAY],
        source="tushare:stk_limit", success=True, error=None, fetched_at=DAY+"T21:56:00+08:00")]))
    assert service._local_limits(DAY, {CORE}, tmp_path)[CORE]["source_sha256"] == checksum
    target.write_bytes(b"damaged")
    with pytest.raises(data.EvidenceError, match="SHA"):
        service._local_limits(DAY, {CORE}, tmp_path)


def test_partial_trade_and_full_day_suspension_conflict():
    m = Market()
    m.missing.add(CORE)
    prior = {"partial_minutes": {"rows": [{"vol": 2., "amount": 20.}]}}
    row = service._collect(CORE, DAY, prior, {"suspension": {"full_day": True}}, m)
    assert row["status"] == "partial" and "全天停牌记录与当日交易证据冲突" in row["gaps"]


def test_auto_previous_only_once_on_current_day_after_close(args, monkeypatch):
    market = Market()
    service.capture_day(DAY, "USER", source_factory=lambda: market, **args)
    calls = []
    def fake_capture(day, input_by, **kwargs):
        calls.append(day)
        return {"status": "complete" if day != DAY else "partial", "trade_date": day}
    monkeypatch.setattr(service, "capture_day", fake_capture)
    next_day = "2026-11-30"
    opts = {k: args[k] for k in ("db_path", "root")}
    monday = datetime.fromisoformat(next_day + "T20:00:00+08:00")
    result = service.capture_with_previous(next_day, "launchd", now=monday, **opts)
    assert result["current"]["status"] == "complete" and result["previous"]["status"] == "partial"
    assert calls == [next_day, DAY]
    assert service.capture_with_previous(next_day, "launchd", now=monday, **opts)["previous"]["reused"]
    assert calls == [next_day, DAY, next_day]
    for early in ("2026-11-30T14:59:00+08:00", "2026-12-01T20:00:00+08:00"):
        assert service.capture_with_previous(next_day, "USER", now=datetime.fromisoformat(early),
                                             **opts)["previous"]["status"] == "skipped"


def test_explicit_universe_revision_requires_prior_hash_and_keeps_original(args):
    m = Market()
    first = service.capture_day(DAY, "USER", source_factory=lambda: m, **args)
    old = first["universe"]
    corrected = {k:v for k,v in old.items() if k != "sha256"}
    corrected.update(members=[corrected["members"][0]], rule_version="v2")
    corrected = dict(**corrected, sha256=data.digest(corrected))
    args["universe_builder"] = lambda *_: corrected
    with pytest.raises(data.EvidenceError, match="摘要"):
        service.capture_day(DAY, "USER", expected_prior_universe_sha="bad", **args)
    after = service.capture_day(DAY, "USER", expected_prior_universe_sha=old["sha256"], **args)
    assert set(after["stocks"]) == {CORE} and after["universe"]["sha256"] == corrected["sha256"]
    backup = service.Path(after["supersedes"]["previous_file"])
    assert service._read(backup, DAY) == first


def test_checked_sparse_source_kept_without_combining_sources(monkeypatch):
    from types import SimpleNamespace
    code = CORE
    full = complete_minutes(code)["rows"]
    def sparse(skip):
        return [dict(r, ts_code=code) for i, r in enumerate(full) if i not in skip]
    market = object.__new__(service.ClosedMarketSource)
    market.has_daily = False
    market.tdx = None
    market.sina = SimpleNamespace(get_stock_minutes=lambda *args: DataResult(sparse({8, 11}), SOURCE))
    market.eastmoney = SimpleNamespace(get_stock_minutes=lambda *args: DataResult(sparse({5}), service.eastmoney.SOURCE))
    with pytest.raises(data.EvidenceError) as caught:
        market.minutes(code, DAY, "15:00", "history", keep_partial=True)
    best = caught.value.partial_minutes
    assert best["source"] == service.eastmoney.SOURCE and len(best["rows"]) == 239
    assert best["missing"] == [full[5]["time"]] and best["status"] == "partial"
    with pytest.raises(data.EvidenceError) as normal:
        market.minutes(code, DAY, "15:00", "history")
    assert not hasattr(normal.value, "partial_minutes")
