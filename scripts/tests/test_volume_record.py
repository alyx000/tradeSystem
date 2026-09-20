import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from providers.base import DataResult
from services.volume_record import history as h, service as s
from cli import volume_record as cli

STOCK = "600000.SH"
TARGET = "2026-09-16"
NOW = datetime(2026, 9, 16, 20, tzinfo=ZoneInfo("Asia/Shanghai"))


def bars(values, stock=STOCK):
    return [dict(ts_code=stock, trade_date=d, vol=v) for d, v in values.items()]


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "trade.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)")
    current = date(2026, 9, 1)
    calendar = {}
    while current <= date(2026, 9, 30):
        d = current.isoformat()
        calendar[d] = int(current.weekday() < 5)
        conn.execute("INSERT INTO trade_calendar VALUES('SSE',?,?)", (d, calendar[d]))
        current += timedelta(days=1)
    conn.commit(); conn.close()
    reg, provider = Mock(), Mock()
    reg.get_provider.return_value = provider
    identity = dict(ts_code=STOCK, name="测试", list_date="20260803")
    quotes = bars({TARGET: 300})
    st = [dict(ts_code="600001.SH", trade_date=TARGET)]
    responses = {
        "get_stock_universe_as_of": DataResult(data=[identity], source="tushare:stock_basic:L+D+P:as_of"),
        "get_stock_st": DataResult(data=st, source="tushare:stock_st"),
        "get_market_daily_quotes": DataResult(data=quotes, source="tushare:daily"),
        "get_suspend_list": DataResult(data=[], source="tushare:suspend_d"),
    }
    reg.call_specific.side_effect = lambda p, method, *args: responses[method]
    reg.call.return_value = DataResult(data={STOCK: {"sw_l2": "银行"}}, source="sw")
    august = {"2026-08-03": 100, "2026-08-31": 200}
    september = {d: 50 for d, opened in calendar.items() if opened and d <= TARGET}
    september[TARGET] = 300

    def daily(stock, start, end):
        all_rows = {**august, **september}
        return DataResult(data=bars({d: v for d, v in all_rows.items() if start <= d <= end}), source="tushare:daily")
    provider.get_stock_daily_range.side_effect = daily
    provider._query_records.return_value = bars({"2026-08-31": 30000})
    kwargs = dict(registry=reg, root=tmp_path, db_path=db, now=NOW, min_market_count=1,
                  recent_prefilter_days=0)
    return kwargs, provider, responses, august, september


def test_complete_and_idempotent_with_full_evidence(setup):
    kwargs, provider, *_ = setup
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "complete"
    assert result["matched_count"] == 1
    stock = result["stocks"][0]
    assert (stock["previous_max_date"], stock["multiple"], stock["industry"]) == ("2026-08-31", 1.5, "银行")
    assert stock["history_first_date"] == "2026-08-03"
    saved = kwargs["root"] / "data/reports/volume-record" / f"{TARGET}.json"
    original = saved.read_bytes()
    provider.reset_mock()
    result = s.run(TARGET, "test2", **kwargs)
    assert result["reused"]
    assert saved.read_bytes() == original
    provider.get_stock_daily_range.assert_not_called()


def test_dry_run_creates_no_files_and_sample_never_exhaustive(setup):
    kwargs, *_ = setup
    result = s.run(TARGET, "test", **kwargs, dry_run=True, codes=[STOCK])
    assert result["status"] == "partial" and result["matched_count"] == 1
    assert not result["is_exhaustive"]
    assert not (kwargs["root"] / "data").exists()


@pytest.mark.parametrize("value", [200, 199])
def test_equal_and_lower_volume_do_not_break_record(setup, value):
    kwargs, provider, responses, _, september = setup
    responses["get_market_daily_quotes"].data[0]["vol"] = value
    september[TARGET] = value
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "complete" and result["matched_count"] == 0


@pytest.mark.parametrize("problem", ["monthly_mismatch", "missing_first", "missing_month", "duplicate", "wrong_code", "negative", "nan"])
def test_bad_history_cannot_produce_false_record(setup, problem):
    kwargs, provider, _, august, _ = setup
    if problem == "monthly_mismatch":
        provider._query_records.return_value[0]["vol"] = 80000
    elif problem == "missing_first":
        august.pop("2026-08-03")
        provider._query_records.return_value[0]["vol"] = 20000
    elif problem == "missing_month":
        provider._query_records.return_value = []
    elif problem in ("negative", "nan"):
        august["2026-08-03"] = -1 if problem == "negative" else float("nan")
    else:
        row = bars({"2026-08-03": 100})[0]
        provider.get_stock_daily_range.side_effect = None
        provider.get_stock_daily_range.return_value = DataResult(
            data=[row, {**row, "vol": 120}] if problem == "duplicate" else [{**row, "ts_code": "600001.SH"}], source="tushare:daily")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed" and result["matched_count"] is None
    assert result["stocks"] == [] and result["gaps"]


def test_missing_current_month_day_requires_full_day_suspension(setup):
    kwargs, _, responses, _, september = setup
    september.pop("2026-09-01")
    result = s.run(TARGET, "test", **kwargs, dry_run=True)
    assert result["status"] == "source_failed"
    responses["get_suspend_list"].data = [dict(ts_code=STOCK, trade_date="20260901", suspend_type="S", suspend_timing="")]
    assert s.run(TARGET, "test", **kwargs, dry_run=True)["status"] == "complete"
    responses["get_suspend_list"].data[0]["suspend_timing"] = "09:30-10:00"
    assert s.run(TARGET, "test", **kwargs, dry_run=True)["status"] == "source_failed"


def test_initial_listing_day_excluded(setup):
    kwargs, _, responses, *_ = setup
    responses["get_stock_universe_as_of"].data[0]["list_date"] = TARGET
    result = s.run(TARGET, "test", **kwargs)
    assert result["matched_count"] == 0
    assert result["coverage"]["first_listing_day"] == 1


def test_new_listing_current_month_needs_no_monthly_baseline(setup):
    kwargs, provider, responses, _, _ = setup
    responses["get_stock_universe_as_of"].data[0]["list_date"] = "20260901"
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "complete" and result["matched_count"] == 1
    provider._query_records.assert_not_called()


@pytest.mark.parametrize("problem", ["st_empty", "quotes_empty", "quote_duplicate", "quote_wrong_day", "identity_duplicate", "identity_unmatched"])
def test_market_hard_gates(setup, problem):
    kwargs, _, r, *_ = setup
    if problem == "st_empty": r["get_stock_st"].data = []
    if problem == "quotes_empty": r["get_market_daily_quotes"].data = []
    if problem == "quote_duplicate": r["get_market_daily_quotes"].data *= 2
    if problem == "quote_wrong_day": r["get_market_daily_quotes"].data[0]["trade_date"] = "20260915"
    if problem == "identity_duplicate": r["get_stock_universe_as_of"].data *= 2
    if problem == "identity_unmatched": r["get_market_daily_quotes"].data[0]["ts_code"] = "600003.SH"
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed" and result["matched_count"] is None


@pytest.mark.parametrize("target,now,status", [("2026-09-19", NOW, "source_failed"),
    ("2026-09-12", NOW, "skipped"), (TARGET, NOW.replace(hour=15), "skipped")])
def test_date_gates_without_network(setup, target, now, status):
    kwargs, *_ = setup
    kwargs["now"] = now
    result = s.run(target, "test", **kwargs, dry_run=True)
    assert result["status"] == status
    kwargs["registry"].call_specific.assert_not_called()


def test_calendar_unknown_fail_closed(setup):
    kwargs, *_ = setup
    conn = sqlite3.connect(kwargs["db_path"])
    conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-03'"); conn.commit(); conn.close()
    assert s.run(TARGET, "test", **kwargs)["status"] == "source_failed"
    kwargs["registry"].call_specific.assert_not_called()


def test_cache_older_bound_only_rejects_and_never_confirms(setup):
    kwargs, provider, responses, _, september = setup
    assert s.run(TARGET, "test", **kwargs)["status"] == "complete"
    responses["get_market_daily_quotes"].data = bars({"2026-09-17": 190})
    responses["get_stock_st"].data[0]["trade_date"] = "2026-09-17"
    kwargs["now"] = NOW.replace(day=17)
    provider.reset_mock()
    result = s.run("2026-09-17", "test", **kwargs)
    assert result["status"] == "complete" and result["matched_count"] == 0
    provider.get_stock_daily_range.assert_not_called()
    responses["get_market_daily_quotes"].data = bars({"2026-09-18": 250})
    responses["get_stock_st"].data[0]["trade_date"] = "2026-09-18"
    kwargs["now"] = NOW.replace(day=18)
    september.update({"2026-09-17": 190, "2026-09-18": 250})
    result = s.run("2026-09-18", "test", **kwargs)
    assert result["status"] == "complete" and result["matched_count"] == 0
    assert provider.get_stock_daily_range.called  # 9/16 的 300 高于今日，不能只看8月水位。


def test_partial_keeps_verified_matches(setup):
    kwargs, _, responses, *_ = setup
    responses["get_stock_universe_as_of"].data.append(dict(ts_code="600002.SH", name="第二只", list_date="20260803"))
    responses["get_market_daily_quotes"].data += bars({TARGET: 300}, "600002.SH")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "partial" and result["matched_count"] == 1
    assert not result["is_exhaustive"] and result["gaps"]


def test_failed_refresh_preserves_previous_partial_report(setup):
    kwargs, provider, responses, *_ = setup
    responses["get_stock_universe_as_of"].data.append(dict(ts_code="600002.SH", name="第二只", list_date="20260803"))
    responses["get_market_daily_quotes"].data += bars({TARGET: 300}, "600002.SH")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "partial" and result["matched_count"] == 1
    path = kwargs["root"] / "data/reports/volume-record" / f"{TARGET}.json"
    previous = path.read_bytes()
    provider.get_stock_daily_range.side_effect = RuntimeError("source down")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed" and result["previous_report_preserved"]
    assert path.read_bytes() == previous
    assert path.with_name(f"{TARGET}.attempt.json").exists()


def test_industry_failure_keeps_volume_facts_and_marks_partial(setup):
    kwargs, *_ = setup
    kwargs["registry"].call.return_value = DataResult(data=None, source="sw", error="offline")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "partial" and result["matched_count"] == 1
    assert result["stocks"][0]["industry"] == "未分类"


def test_partial_to_partial_does_not_drop_previously_verified_stock(setup):
    kwargs, provider, responses, *_ = setup
    responses["get_stock_universe_as_of"].data.append(dict(ts_code="600002.SH", name="第二只", list_date="20260803"))
    responses["get_market_daily_quotes"].data += bars({TARGET: 300}, "600002.SH")
    assert s.run(TARGET, "test", **kwargs)["matched_count"] == 1
    path = kwargs["root"] / "data/reports/volume-record" / f"{TARGET}.json"
    previous = path.read_bytes()
    original = provider.get_stock_daily_range.side_effect
    def flip(stock, start, end):
        if stock == STOCK:
            raise RuntimeError("first stock temporarily unavailable")
        result = original(STOCK, start, end)
        for row in result.data: row["ts_code"] = stock
        return result
    provider.get_stock_daily_range.side_effect = flip
    provider._query_records.return_value = bars({"2026-08-31": 30000}, "600002.SH")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "partial" and result["stocks"][0]["code"] == "600002.SH"
    assert result["previous_report_preserved"] and path.read_bytes() == previous


def test_archive_failure_is_not_complete(setup, monkeypatch):
    kwargs, *_ = setup
    original = s.atomic_write
    def fail_markdown(path, content):
        if path.suffix == ".md": raise OSError("disk full fixture")
        return original(path, content)
    monkeypatch.setattr(s, "atomic_write", fail_markdown)
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed" and result["calculation_status"] == "complete"
    assert result["matched_count"] == 1 and not result["is_exhaustive"]
    assert "disk full" in result["error"]
    path = kwargs["root"] / "data/reports/volume-record" / f"{TARGET}.json"
    assert not path.exists()  # 展示文件失败不能发布可被complete复用的JSON。
    monkeypatch.setattr(s, "atomic_write", original)
    recovered = s.run(TARGET, "test", **kwargs)
    assert recovered["status"] == "complete" and not recovered.get("reused")


def test_historical_target_does_not_use_future_delisting_name(setup):
    kwargs, _, responses, *_ = setup
    responses["get_stock_universe_as_of"].data[0].update(name="后来退", delist_date="20261231")
    kwargs["now"] = NOW.replace(day=17)
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "complete" and result["matched_count"] == 1


def test_cached_complete_report_survives_markdown_repair_failure(setup, monkeypatch):
    kwargs, provider, *_ = setup
    assert s.run(TARGET, "test", **kwargs)["status"] == "complete"
    path = kwargs["root"] / "data/reports/volume-record" / f"{TARGET}.json"
    original = path.read_bytes()
    write = s.atomic_write
    def fail_markdown(path, content):
        if path.suffix == ".md": raise OSError("permission fixture")
        return write(path, content)
    monkeypatch.setattr(s, "atomic_write", fail_markdown)
    provider.reset_mock()
    provider.get_stock_daily_range.side_effect = RuntimeError("offline")
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed"
    assert path.read_bytes() == original
    provider.get_stock_daily_range.assert_not_called()


def test_certified_prefix_plus_fresh_suffix_can_confirm_record(setup):
    kwargs, provider, responses, _, september = setup
    assert s.run(TARGET, "test", **kwargs)["status"] == "complete"
    kwargs["now"] = NOW.replace(day=17)
    responses["get_stock_st"].data[0]["trade_date"] = "2026-09-17"
    responses["get_market_daily_quotes"].data = bars({"2026-09-17": 400})
    september["2026-09-17"] = 400
    provider.reset_mock()
    result = s.run("2026-09-17", "test", **kwargs)
    assert result["status"] == "complete" and result["stocks"][0]["previous_max_volume"] == 300
    provider._query_records.assert_not_called()  # 已认证的上市前缀复用，仍必须重取本月后缀。
    provider.get_stock_daily_range.assert_called_once_with(STOCK, "2026-09-01", "2026-09-17")


def test_full_history_chunking_and_internal_month_gap():
    provider = Mock()
    provider.get_stock_daily_range.return_value = DataResult(data=[], source="tushare:daily")
    provider._query_records.return_value = []
    with pytest.raises(ValueError):
        h.build_baseline(provider, STOCK, "1991-01-01", "2026-08-31")
    calls = provider.get_stock_daily_range.call_args_list
    assert len(calls) == 3 and calls[0].args[1] == "1991-01-01"
    for previous, current in zip(calls, calls[1:]):
        assert date.fromisoformat(previous.args[2]) + timedelta(days=1) == date.fromisoformat(current.args[1])
    assert calls[-1].args[-1] == "2026-08-31"
    with pytest.raises(ValueError, match="整月缺口"):
        h.certify_months({"2026-07-01": 100}, bars({"2026-07-31": 10000}), STOCK, "2026-07-01", "2026-08-31")


def test_identical_history_duplicates_collapsed_with_audit(setup):
    kwargs, provider, *_ = setup
    original = provider.get_stock_daily_range.side_effect
    def duplicate(*args):
        result = original(*args)
        result.data.append(dict(result.data[0]))
        return result
    provider.get_stock_daily_range.side_effect = duplicate
    result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "complete"
    assert result["stocks"][0]["duplicate_rows_collapsed"] == 2


def test_corrupt_or_future_cache_not_reused(setup):
    kwargs, *_ = setup
    s.run(TARGET, "test", **kwargs)
    directory = kwargs["root"] / "data/reports/volume-record/baseline"
    assert h.load_baseline(directory, STOCK, "2026-08-03", "2026-07-31") is None
    file = directory / STOCK / "2026-08-31.json"
    row = json.loads(file.read_text()); row["baseline"]["max_volume"] = 1
    file.write_text(json.dumps(row))
    assert h.load_baseline(directory, STOCK, "2026-08-03", "2026-08-31") is None


def test_cli_audit_required_and_sample_guard():
    parser = argparse.ArgumentParser(); cli.register_subparser(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit): parser.parse_args(["volume-record", "daily"])
    args = parser.parse_args(["volume-record", "daily", "--input-by", "test", "--codes", STOCK])
    with pytest.raises(SystemExit, match="dry-run"): cli.handle_command({}, args)


def test_post_wrapper_isolates_failure(monkeypatch, caplog):
    import main
    monkeypatch.setattr(cli, "run_for_post", Mock(side_effect=RuntimeError("fixture_failure")))
    main._run_volume_record_for_post(TARGET, Mock())
    assert "不影响盘后主流程" in caplog.text


def test_concurrent_run_is_blocked_without_clobber(setup):
    kwargs, *_ = setup
    directory = kwargs["root"] / "data/reports/volume-record"
    with s.run_lock(directory, False):
        result = s.run(TARGET, "test", **kwargs)
    assert result["status"] == "source_failed"
    assert not list(directory.glob("*.json"))


@pytest.mark.parametrize('prior_volume,excluded', [(300, True), (301, True), (299, False)])
def test_prefilter_only_prior_equal_or_larger_can_exclude(setup, prior_volume, excluded):
    kwargs, provider, responses, *_ = setup
    kwargs['recent_prefilter_days'] = 1
    reg = kwargs['registry']
    reg.call_specific.side_effect = lambda p, method, *args: (
        DataResult(data=bars({'2026-09-15': prior_volume}), source='tushare:daily')
        if method == 'get_market_daily_quotes' and args[0] != TARGET else responses[method])
    result = s.run(TARGET, 'test', **kwargs)
    assert result['status'] == 'complete'
    assert result['coverage']['excluded_by_prior_observation'] == int(excluded)
    assert result['matched_count'] == int(not excluded)
    assert provider.get_stock_daily_range.called == (not excluded)
    if excluded:
        assert result['prior_observation_exclusions'][0]['prior_date'] == '2026-09-15'


@pytest.mark.parametrize('problem', ['wrong_date', 'wrong_source', 'duplicate', 'negative', 'failure', 'before_listing'])
def test_prefilter_invalid_evidence_never_excludes(setup, problem):
    kwargs, provider, responses, *_ = setup
    kwargs['recent_prefilter_days'] = 1
    result = DataResult(data=bars({'2026-09-15': 900}), source='tushare:daily')
    if problem == 'wrong_date': result.data[0]['trade_date'] = TARGET
    if problem == 'wrong_source': result.source = 'other'
    if problem == 'duplicate': result.data *= 2
    if problem == 'negative': result.data[0]['vol'] = -1
    if problem == 'failure': result = DataResult(data=None, source='tushare:daily', error='offline')
    if problem == 'before_listing':
        responses['get_stock_universe_as_of'].data[0]['list_date'] = TARGET
    kwargs['registry'].call_specific.side_effect = lambda p, method, *args: (
        result if method == 'get_market_daily_quotes' and args[0] != TARGET else responses[method])
    report = s.run(TARGET, 'test', **kwargs)
    assert report['coverage']['excluded_by_prior_observation'] == 0
    assert report['status'] == 'complete'
    assert report['matched_count'] == (0 if problem == 'before_listing' else 1)


def add_amount_fixture(setup, *, today_amount=6000):
    kwargs, provider, responses, *_ = setup
    responses['get_market_daily_quotes'].data[0]['amount'] = today_amount
    provider._query_records.return_value[0]['amount'] = 3000000
    original = provider.get_stock_daily_range.side_effect
    def daily(*args):
        result = original(*args)
        amounts = {'2026-08-03': 1000, '2026-08-31': 2000, TARGET: today_amount}
        for row in result.data:
            row['amount'] = amounts.get(row['trade_date'], 500)
        return result
    provider.get_stock_daily_range.side_effect = daily
    return kwargs


@pytest.mark.parametrize('volume,amount,volume_count,amount_count,both', [
    (150, 6000, 0, 1, 0), (300, 1500, 1, 0, 0), (300, 6000, 1, 1, 1), (200, 2000, 0, 0, 0)])
def test_dual_metrics_are_independent_and_strict(setup, volume, amount, volume_count, amount_count, both):
    kwargs = add_amount_fixture(setup, today_amount=amount)
    setup[2]['get_market_daily_quotes'].data[0]['vol'] = volume
    setup[4][TARGET] = volume
    result = s.run_dual(TARGET, 'test', **kwargs)
    assert result['status'] == 'complete'
    assert result['matched_counts'] == {'volume': volume_count, 'amount': amount_count}
    assert result['both_matched_count'] == both
    folder = kwargs['root'] / 'data/reports/volume-record'
    for path in [folder / f'{TARGET}.json', folder / 'amount' / f'{TARGET}.json', folder / 'dual' / f'{TARGET}.json']:
        payload = json.loads(path.read_text()); digest = payload.pop('sha256')
        assert h.fingerprint(payload) == digest
    if amount_count:
        row = result['metrics']['amount']['stocks'][0]
        assert row['amount'] == amount and row['previous_max_amount'] == 2000
        assert 'volume' not in row and 'previous_max_volume' not in row
    text = s.render(result)
    assert '成交量（手）' in text and '成交额（千元）' in text
    # 相同的历史日/月数据在一次运行中只请求一次。
    setup[1]._query_records.assert_called_once()


@pytest.mark.parametrize('broken', ['volume', 'amount'])
def test_one_metric_missing_does_not_poison_other_metric(setup, broken):
    kwargs = add_amount_fixture(setup)
    setup[2]['get_market_daily_quotes'].data[0][h.metric_spec(broken)['field']] = None
    result = s.run_dual(TARGET, 'test', **kwargs)
    other = 'volume' if broken == 'amount' else 'amount'
    assert result['status'] == 'partial'
    assert result['metrics'][broken]['status'] == 'source_failed'
    assert result['matched_counts'][broken] is None
    assert result['metrics'][other]['status'] == 'complete'
    assert result['matched_counts'][other] == 1
    assert result['both_matched_count'] is None


def test_amount_monthly_units_and_baseline_separation(setup):
    kwargs = add_amount_fixture(setup)
    assert s.run(TARGET, 'test', **kwargs)['status'] == 'complete'
    folder = kwargs['root'] / 'data/reports/volume-record/baseline'
    assert h.load_baseline(folder, STOCK, '2026-08-03', '2026-08-31', 'amount') is None
    setup[1]._query_records.return_value[0]['amount'] = 3000  # 错把千元当元。
    result = s.run(TARGET, 'test', **kwargs, metric='amount')
    assert result['status'] == 'source_failed'
    assert any('总量不符' in gap for gap in result['gaps'])
    assert not (folder.parent / 'amount/baseline' / STOCK / '2026-08-31.json').exists()


def test_amount_prefilter_does_not_use_volume(setup):
    kwargs = add_amount_fixture(setup)
    kwargs['recent_prefilter_days'] = 1
    responses = setup[2]
    prior = bars({'2026-09-15': 100000})
    prior[0]['amount'] = 1000  # 成交量很高，但成交额小于今日，不能排除成交额新高。
    kwargs['registry'].call_specific.side_effect = lambda p, method, *args: (
        DataResult(data=prior, source='tushare:daily')
        if method == 'get_market_daily_quotes' and args[0] != TARGET else responses[method])
    result = s.run_dual(TARGET, 'test', **kwargs, dry_run=True)
    assert result['matched_counts'] == {'volume': 0, 'amount': 1}
    assert not (kwargs['root'] / 'data').exists()


def test_dual_report_downgrade_preserves_previous_report(setup):
    kwargs = add_amount_fixture(setup)
    reg = kwargs['registry']
    reg.call.return_value = DataResult(data=None, source='sw', error='offline')
    assert s.run_dual(TARGET, 'test', **kwargs)['matched_count'] == 1
    path = kwargs['root'] / f'data/reports/volume-record/dual/{TARGET}.json'
    old = path.read_bytes()
    setup[1].get_stock_daily_range.side_effect = RuntimeError('down')
    result = s.run_dual(TARGET, 'test', **kwargs)
    assert result['previous_report_preserved']
    assert path.read_bytes() == old


def test_dual_archive_failure_never_publishes_complete(setup, monkeypatch):
    kwargs = add_amount_fixture(setup)
    original = s.atomic_write
    def fail(path, content):
        if path.parent.name == 'dual' and path.suffix == '.md':
            raise OSError('dual disk full')
        original(path, content)
    monkeypatch.setattr(s, 'atomic_write', fail)
    result = s.run_dual(TARGET, 'test', **kwargs)
    assert result['status'] == 'source_failed' and result['calculation_status'] == 'complete'
    assert not (kwargs['root'] / f'data/reports/volume-record/dual/{TARGET}.json').exists()


def test_default_cli_and_post_use_both_metrics(monkeypatch):
    parser = argparse.ArgumentParser(); cli.register_subparser(parser.add_subparsers(dest='command'))
    args = parser.parse_args(['volume-record', 'daily', '--input-by', 'test'])
    assert args.metric == 'both'
    runner = Mock(return_value={'status': 'complete'})
    monkeypatch.setattr(s, 'run_dual', runner)
    registry = Mock()
    delivery = Mock(return_value={'status': 'sent'})
    monkeypatch.setattr(cli.notification, 'push_result', delivery)
    cli.run_for_post(TARGET, registry)
    delivery.assert_called_once_with(runner.return_value, 'system_post')
    runner.assert_called_once_with(TARGET, 'system_post', registry=registry)
