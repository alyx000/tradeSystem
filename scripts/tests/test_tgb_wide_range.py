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


def seal(run, *, rows=None, daily=None, **receipt):
    run.mkdir(parents=True, exist_ok=True)
    if rows is not None:
        receipt["candidates"] = len(rows)
    artifacts = {
        "report.md": "测试归档\n".encode(),
        "verification.txt": "".join(f"{key}={value}\n" for key, value in receipt.items()).encode(),
    }
    if rows is not None:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["code", "name", "close_qfq", "ma30_qfq", "ma30_pass"])
        writer.writerows(rows)
        artifacts["candidates.csv"] = stream.getvalue().encode()
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
