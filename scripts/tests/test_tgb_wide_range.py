import csv
import hashlib
import io
import json
import math
import sys
import textwrap

import pandas as pd
import pytest

from tools import tgb_wide_range_notify as notify
from tools import tgb_wide_range_prepare as prepare


def csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows(rows)
    return stream.getvalue().encode()


def seal(run, *, rows=None, daily=None, candidate_fields=(), attachments=None, **receipt):
    run.mkdir(parents=True, exist_ok=True)
    if rows is not None:
        receipt["candidates"] = len(rows)
    artifacts = {
        "report.md": "测试归档\n".encode(),
        "verification.txt": "".join(f"{key}={value}\n" for key, value in receipt.items()).encode(),
    }
    if rows is not None:
        artifacts["candidates.csv"] = csv_bytes(
            ["code", "name", "close_qfq", "ma30_qfq", "ma30_pass", *candidate_fields], rows,
        )
    artifacts.update(attachments or {})
    if daily is not None:
        buffer = io.BytesIO()
        daily.to_pickle(buffer)
        artifacts["daily.pkl"] = buffer.getvalue()
    for name, content in artifacts.items():
        (run / name).write_bytes(content)
    (run / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in artifacts.items()
    ))


def test_prepare_changes_match_and_finalization_reasons():
    collector = textwrap.dedent('''
        import math
        screened, result, reason = [], [], {'calculated':0}
        for g, amp in scenarios:
         ok=amp>=10
         screened.append(dict(match=ok))
         reason['calculated']+=1
         if not ok:continue
         result.append(dict(code='candidate'))
        x=pd.DataFrame(result) if result else pd.DataFrame(columns=['code','name','amp5'])
    ''')
    patched = prepare.collection_script(collector)
    assert patched == prepare.collection_script(patched)
    finalizer = prepare.finalization_script(
        "def why(z):\n reasons=[]\n return '；'.join(reasons)\np=r/'report.md';pass\n"
    )
    assert finalizer == prepare.finalization_script(finalizer)
    reason = {}
    exec(finalizer[:finalizer.index("\nma30_only")], {"pd": pd}, reason)
    for close, amp, expected in [(9, 12, False), (10, 12, True), (11, 12, True), (11, 8, False),
                                 (math.inf, 12, False)]:
        g = pd.DataFrame({"close": [10] * 29 + [close]})
        namespace = {"scenarios": [(g, amp)], "pd": pd}
        exec(compile(patched, "collect.py", "exec"), namespace)
        row = namespace["screened"][0]
        assert row["match"] is expected
        assert len(namespace["x"]) == int(expected)
        if close == 9:
            assert reason["why"](type("Result", (), row)) == "收盘低于30日均线"


def test_prepare_does_not_edit_a_started_run(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "RUNS", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prepare", "--run-date", "2026-11-16", "--input-by", "test"])
    run = tmp_path / "2026-11-16"
    run.mkdir()
    (run / "collect.log").touch()
    collector = run / "collect.py"
    collector.write_text("unchanged\n")
    with pytest.raises(SystemExit):
        prepare.main()
    assert collector.read_text() == "unchanged\n"


def test_full_notification_ma30_and_silent_branches(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    monkeypatch.setattr(notify, "read_industries", lambda day, allow_live: (
        {"000001.SZ": {"sw_l2": "行业甲"}, "000003.SZ": {"sw_l2": "行业丙"}}, "archive", day, "hash", "archived",
    ))
    previous, day = "2026-11-13", "2026-11-16"
    old = tmp_path / "runs" / previous
    run = tmp_path / "runs" / day
    retained = ("000001.SZ", "保留甲", 10, 10, True)
    new = ("000002.SZ", "新增乙", 11, 10, True)
    gone = ("000003.SZ", "退出丙", 11, 10, True)
    seal(old, rows=[retained, gone], run_date=previous, market_date=previous, status="partial",
         screen_rules="ma30_v1")
    fields = dict(run_date=day, market_date=day, status="partial", screen_rules="ma30_v1",
                  candidates_added=1, candidates_removed=1, comparison_baseline_run=previous,
                  comparison_baseline_market=previous)
    seal(run, rows=[retained, new], **fields)
    event, _, body = notify.plan(run, day, require_ma30=True)
    assert event == ["delta", day, [new[0]], [gone[0]]]
    assert "保留甲（000001.SZ｜行业甲）" in body and "新增乙（000002.SZ｜板块待核验） **新增**" in body
    assert "退出丙（000003.SZ｜行业丙）" in body and "30日均线" in body
    bumped = "2026-11-17"
    future = tmp_path / "runs" / bumped
    seal(future, rows=[retained, new], run_date=bumped, market_date=bumped, status="partial",
         screen_rules="ma30_v1", candidates_added=0, candidates_removed=0,
         comparison_baseline_run=day, comparison_baseline_market=day)
    assert notify.plan(future, bumped, require_ma30=True) is None
    seal(future, run_date=bumped, status="skipped")
    assert notify.plan(future, bumped, require_ma30=True) is None
    seal(future, run_date=bumped, status="source_failed", failed_interface="get_adj_factor")
    assert "复权因子" in notify.plan(future, bumped, require_ma30=True)[2]
    (run / "candidates.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="归档校验"):
        notify.plan(run, day, require_ma30=True)
    seal(run, rows=[retained, (new[0], new[1], 9, 10, True)], **fields)
    with pytest.raises(ValueError, match="MA30"):
        notify.plan(run, day, require_ma30=True)
    seal(run, rows=[retained, new], **{**fields, "market_date": bumped})
    with pytest.raises(ValueError, match="基线"):
        notify.plan(run, day, require_ma30=True)
    seal(run, rows=[retained, new], **{**fields, "market_date": "2026-11-12"})
    with pytest.raises(ValueError, match="基线"):
        notify.plan(run, day, require_ma30=True)


def test_multipart_delivery_preserves_every_stock_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    attempts = []

    class Pusher:
        def initialize(self):
            return True

        def send_markdown(self, title, content):
            attempts.append((title, content))
            return True

    monkeypatch.setattr(notify, "configured_pusher", Pusher)
    body = "".join(f"- 股票编号 {index}｜行业甲\n" for index in range(1000))
    request = (["delta", "test"], "测试摘要", body, "2026-11-16", "test")
    receipt = notify.deliver(*request)
    assert receipt["status"] == "sent" and len(attempts) > 1
    assert "".join(content for _, content in attempts) == body
    with open(receipt["receipt"]) as f:
        assert json.load(f)["confirmed_parts"] == list(range(1, len(attempts) + 1))
    assert notify.deliver(*request)["status"] == "already_sent"


@pytest.mark.parametrize("change", ["delta", "only_rule", "bad_archive"])
def test_ma30_transition_compares_with_rescreened_baseline(tmp_path, monkeypatch, change):
    previous, day = "2026-11-16", "2026-11-17"
    old, run = (tmp_path / "runs" / date for date in (previous, day))
    retained = ("000001.SZ", "保留甲", 10, 10, True)
    rule_only = ("000002.SZ", "规则变更过滤乙", 11, 10, True)
    exiting = ("000003.SZ", "退出丙", 11, 10, True)
    entering = ("000004.SZ", "新增丁", 11, 10, True)
    dates = pd.bdate_range(end=previous, periods=30).strftime("%Y-%m-%d")
    daily = pd.DataFrame([
        {"ts_code": code, "trade_date": date, "close": close, "adj_factor": factor}
        for code, last_close in [(retained[0], 10), (rule_only[0], 8), (exiting[0], 11)]
        for date, close, factor in zip(dates, [5] * 29 + [last_close], [2] * 29 + [1])
    ])
    seal(old, rows=[retained, rule_only, exiting], daily=daily, run_date=previous,
         market_date=previous, status="partial")
    if change == "bad_archive":
        (old / "daily.pkl").write_bytes(b"corrupted")

    actual_delta = change == "delta"
    seal(run, rows=[retained, entering if actual_delta else exiting], run_date=day, market_date=day,
         status="partial", screen_rules="ma30_v1", candidates_added=int(actual_delta),
         candidates_removed=1 + int(actual_delta), comparison_baseline_run=previous,
         comparison_baseline_market=previous)
    monkeypatch.setattr(notify, "REPORTS", tmp_path)

    def read_industries(day, *, allow_live):
        assert actual_delta and not allow_live
        return ({exiting[0]: {"sw_l2": "行业丙"}, entering[0]: {"sw_l2": "行业丁"}},
                "archive", day, "hash", "archived")

    monkeypatch.setattr(notify, "read_industries", read_industries)
    prior_events = []
    if change == "bad_archive":
        with pytest.raises(ValueError, match="归档校验"):
            notify.plan(run, day, require_ma30=True, previous_policy_events=prior_events)
        return
    payload = notify.plan(run, day, require_ma30=True, previous_policy_events=prior_events)
    if change == "only_rule":
        assert payload is None
        return

    event, _, body = payload
    assert event == ["delta", day, [entering[0]], [exiting[0]]]
    assert "上次 2 只，本次 2 只，新增 1 只，退出 1 只" in body
    assert "退出丙（000003.SZ｜行业丙）" in body
    assert "新增丁（000004.SZ｜行业丁） **新增**" in body
    assert rule_only[0] not in body
    old_event = ["delta", day, [entering[0]], [rule_only[0], exiting[0]]]
    assert prior_events == [old_event]
    archive = tmp_path / "dingtalk"
    archive.mkdir()
    old_hash = hashlib.sha256(json.dumps(old_event).encode()).hexdigest()
    receipt = archive / f"{old_hash}.json"
    saved = json.dumps({"status": "sent"})
    receipt.write_text(saved)
    monkeypatch.setattr(notify, "configured_pusher", lambda: pytest.fail("attempted a duplicate push"))
    assert notify.deliver(*payload, day, "test", previous_policy_events=prior_events) == {
        "status": "already_sent", "receipt": str(receipt),
    }
    assert receipt.read_text() == saved


def test_retry_baseline_uses_the_sealed_archive_and_keeps_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    old, day = "2026-11-18", "2026-11-19"
    failed, run = (tmp_path / "runs" / date for date in (old, day))
    seal(failed, run_date=old, status="source_failed", market_date_target=old)
    original = (failed / "verification.txt").read_bytes()
    before = [(str(i), f"原候选{i}", 11, 10, True) for i in range(14)]
    after = before[:2] + [(str(i), f"新候选{i}", 11, 10, True) for i in range(14, 24)]
    retry = failed / "retry-230739"
    seal(retry, rows=before, run_date=old, market_date=old, status="partial", screen_rules="ma30_v1")
    fields = dict(run_date=day, market_date=day, status="partial", screen_rules="ma30_v1",
                  candidates_added=10, candidates_removed=12, comparison_baseline_run=old,
                  comparison_baseline_market=old, comparison_baseline_archive=f"{old}/retry-230739")
    seal(run, rows=after, **fields)
    event, _, body = notify.plan(run, day, require_ma30=True)
    assert event == ["delta", day, sorted(r[0] for r in after[2:]), sorted(r[0] for r in before[2:])]
    assert "上次 14 只，本次 12 只，新增 10 只，退出 12 只" in body
    assert "保留 2 只" in body and (failed / "verification.txt").read_bytes() == original

    (failed / "outside").symlink_to(run, target_is_directory=True)
    for wrong in (f"{old}/outside", f"{old}/../{day}", str(retry), day, old):
        seal(run, rows=after, **{**fields, "comparison_baseline_archive": wrong})
        with pytest.raises(ValueError, match="基线"):
            notify.plan(run, day)
    seal(run, rows=after, **{**fields, "comparison_baseline_market": "2026-11-17"})
    with pytest.raises(ValueError, match="基线"):
        notify.plan(run, day)
    seal(run, rows=after, **fields)
    (retry / "candidates.csv").write_text("corrupt\n")
    with pytest.raises(ValueError, match="归档校验"):
        notify.plan(run, day)


def test_summary_explains_changes_and_reentry_without_filtering_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    first, middle, previous, day = "2026-11-20", "2026-11-22", "2026-11-26", "2026-11-27"
    runs = tmp_path / "runs"
    rules = dict(status="partial", screen_rules="ma30_v1")
    back = ("A", "重新入选甲", 12, 10, True)
    held = ("B", "保留乙", 12, 10, True)
    fresh = ("C", "新增丙", 12, 10, True)
    out = ("D", "退出丁", 12, 10, True)
    suspended = ("E", "停牌戊", 12, 10, True)
    seal(runs / first, rows=[back, held], run_date=first, market_date=first, **rules)
    seal(runs / middle, rows=[back, held], run_date=middle, market_date=first,
         comparison_baseline_run=first, comparison_baseline_market=first, **rules)
    columns = ["code", "match", "drift", "ma30_pass", "exclusion_reason"]
    seal(runs / previous, rows=[held, out, suspended], run_date=previous, market_date=previous,
         comparison_baseline_run=middle, comparison_baseline_market=first, **rules, attachments={
             "full-screen.csv": csv_bytes(columns, [["A", False, 0, False, "收盘低于30日均线；修复不足2次"],
                                                    ["C", False, -23, True, "20日涨跌超出±20%"]]),
         })
    seal(runs / day, rows=[(*back, first, False, "4.0"), (*held, previous, True, 2),
                           (*fresh, previous, False, 1)], candidate_fields=["onset", "left_censored", "post_rebounds"],
         run_date=day, market_date=day, comparison_baseline_run=previous, comparison_baseline_market=previous,
         candidates_added=2, candidates_removed=2, **rules, attachments={
             "full-screen.csv": csv_bytes(columns, [["D", False, 25, True, "20日涨跌超出±20%"]]),
             "exits.csv": csv_bytes(["code", "reason"], [["D", "20日涨跌超出±20%"],
                                                         ["E", "suspend_d_reported_no_daily_quote"]]),
         })
    event, _, body = notify.plan(runs / day, day, require_ma30=True)
    assert event == ["delta", day, ["A", "C"], ["D", "E"]]
    lines = {code: next(line for line in body.splitlines() if f"（{code}｜" in line) for code in "ABCDE"}
    assert f"重新入选（上次 {first}）" in lines["A"] and "新增" in lines["A"]
    assert "堆量后修复 4 次" in lines["A"] and "收盘低于MA30" in lines["A"]
    assert "堆量起点待核" in lines["B"] and "修复 2 次" not in lines["B"]
    assert "堆量后修复 1 次" in lines["C"] and "20日跌幅超过20%" in lines["C"]
    assert "20日涨幅超过20%" in lines["D"] and "停牌" in lines["E"]
    assert "收盘低于MA30 0只" not in body  # One exit has no quote.
    assert "修复至少2次 1只" in body and "修复1次 1只" in body and "起点待核 1只" in body
    (runs / previous / "full-screen.csv").write_text("bad\n")
    with pytest.raises(ValueError, match="归档校验"):
        notify.plan(runs / day, day)


def test_preview_industries_use_only_matching_verified_evidence(tmp_path, monkeypatch):
    day = "2026-11-27"
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    industry = tmp_path / "industry"
    industry.mkdir()
    columns = ["market_date", "fetched_at", "status", "source", "code", "sw_l2"]
    valid = [day, day + "T20:00:00+08:00", "verified", "tushare:index_member_all"]
    for rows in (
        [[*valid, "A", "行业甲"], [*valid, "B", "行业乙"],
         ["2026-11-28", *valid[1:], "C", "跨日标签"],
         [day, *valid[1:2], "unverified", valid[3], "D", "待核验"],
         [day, "2026-11-28T08:00:00+08:00", *valid[2:], "E", "错日取数"]],
        [[*valid, "B", "行业丙"]],
    ):
        raw = csv_bytes(columns, rows)
        (industry / f"{day}-{hashlib.sha256(raw).hexdigest()[:16]}.csv").write_bytes(raw)
    (industry / f"{day}-0000000000000000.csv").write_bytes(csv_bytes(columns, [[*valid, "A", "损坏附件"]]))
    assert notify.saved_industries(day, day) == {"A": ("行业甲", (valid[3], valid[1], "", ""))}
    previous = "2026-11-26"
    old, run = (tmp_path / "runs" / date for date in (previous, day))
    seal(old, rows=[], run_date=previous, market_date=previous, status="partial", screen_rules="ma30_v1")
    seal(run, rows=[("A", "甲", 10, 10, True), ("F", "己", 10, 10, True)], run_date=day, market_date=day,
         status="partial", screen_rules="ma30_v1", comparison_baseline_run=previous,
         comparison_baseline_market=previous, candidates_added=2, candidates_removed=0)
    new_source = ("同日全市场归档", day + "T21:00:00+08:00", "other-hash", "archived")
    monkeypatch.setattr(notify, "read_industries", lambda *a, **kw: ({"F": {"sw_l2": "行业己"}}, *new_source))
    before = set(industry.iterdir())
    event = notify.plan(run, day)
    assert set(industry.iterdir()) == before and "A｜行业甲" in event[2]
    assert notify.plan(run, day, archive_industry_by="test") == event
    saved, = set(industry.iterdir()) - before
    with saved.open() as f:
        rows = {r["code"]: r for r in csv.DictReader(f)}
    assert rows["A"]["source"] == valid[3] and rows["A"]["fetched_at"] == valid[1]
    assert rows["F"]["source"] == new_source[0] and rows["F"]["source_sha256"] == new_source[2]


def test_entry_exit_coverage_and_partial_repair_evidence(tmp_path):
    columns = ["code", "match", "drift", "ma30_pass", "exclusion_reason"]
    seal(tmp_path, attachments={"full-screen.csv": csv_bytes(columns, [
        ["D", False, 25, True, "20日涨跌超出±20%；收盘处于前15根范围外"],
        ["E", False, -23, True, "20日涨跌超出±20%"],
    ])})
    notes, summary = notify.change_evidence(tmp_path, ["D", "E"], additions=False, ma30=True)
    assert summary.count("收盘低于MA30 0只") == 1
    assert notes == {"D": "20日涨幅超过20%、收盘处于前15根范围外", "E": "20日跌幅超过20%"}
    notes, summary = notify.change_evidence(tmp_path, ["A", "X"], additions=True, ma30=True, legacy_filtered={"A"})
    assert notes == {"A": "收盘低于MA30", "X": "原因待核"} and "原因待核 1只" in summary

    row = dict(onset="2026-11-20", left_censored="False", post_rebounds="0.0")
    assert notify.repair_count(row, "2026-11-27") == 0
    for missing in ("", "NaN", "-1", "1.5"):
        assert notify.repair_count(dict(row, post_rebounds=missing), "2026-11-27") == "修复次数待核"
    assert notify.repair_count(dict(row, onset="2026-11-28"), "2026-11-27") == "起点待核"


def test_reentry_replays_the_first_comparable_ma30_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "REPORTS", tmp_path)
    first, middle, previous = "2026-11-16", "2026-11-17", "2026-11-26"
    runs = tmp_path / "runs"
    dates = pd.bdate_range(end=first, periods=30).strftime("%Y-%m-%d")
    prices = pd.DataFrame([
        (code, day, last if day == first else 10, 1)
        for code, last in (("A", 11), ("B", 9)) for day in dates
    ], columns=["ts_code", "trade_date", "close", "adj_factor"])
    rows = [("A", "甲", 11, 10, True), ("B", "乙", 9, 10, False)]
    seal(runs / first, run_date=first, market_date=first, rows=rows, daily=prices, status="partial")
    seal(runs / middle, run_date=middle, market_date=middle, rows=[], status="partial", screen_rules="ma30_v1",
         comparison_baseline_run=first, comparison_baseline_market=first)
    baseline = dict(run_date=previous, market_date=previous, comparison_baseline_run=middle,
                    comparison_baseline_market=middle)
    reentries, coverage = notify.previous_appearances(baseline, ["A", "B"], "ma30_v1")
    assert reentries == {"A": first} and first in coverage
    (runs / first / "daily.pkl").write_bytes(b"corrupt")
    reentries, coverage = notify.previous_appearances(baseline, ["A", "B"], "ma30_v1")
    assert reentries == {} and "缺少可核验归档" in coverage
