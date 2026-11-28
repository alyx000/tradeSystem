"""Exercise the daily report's missing-data and publication boundaries."""

from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automations"))
import kaipanla_daily as daily


def quote(pct, status="observed"):
    return {"name": "样本", "category": "化工", "pct": pct, "status": status}


def test_full_universe_keeps_slow_rise_and_does_not_cross_gap():
    key = ("样本", "化工")
    history = [{key: quote("1.5")} for _ in range(19)]
    result, = daily.compute_commodity([quote("-0.60")], history)
    assert not result["day_surge"]
    assert result["streak"] == 0
    assert result["up_10"] == 9
    assert "10日累涨" in result["hits"]

    history[-2][key] = quote("", "source_pct_unavailable")
    result, = daily.compute_commodity([quote("1.3")], history)
    assert result["streak"] == 2
    assert result["streak_boundary"] == "至少"
    assert not result["complete_5"]
    assert result["gain_5"] == ""


def test_missing_commodity_tail_blocks_even_with_all_numeric_rows(tmp_path):
    fact = "2026-11-27"
    meta = {"date": fact, "read_at": "2026-11-27T13:00:00Z"}
    daily.write_csv(tmp_path / "category-list.csv", [dict(meta, ordered_categories="化工、能源")])
    daily.write_csv(tmp_path / "commodity.csv", [
        dict(meta, view=view, name="样本", category="化工", raw_pct="0.00%")
        for view in ("精选", "化工")
    ])
    tails = [dict(meta, view=view, tail="true", unique_rows="1") for view in ("精选", "化工")]
    daily.write_csv(tmp_path / "tail.csv", tails)
    with pytest.raises(ValueError, match="能源"):
        daily.check_commodity_views(tmp_path, fact)
    daily.write_csv(tmp_path / "tail.csv", tails + [dict(meta, view="能源", tail="true", unique_rows="0")])
    daily.check_commodity_views(tmp_path, fact)


def test_replay_receipt_cannot_be_used_for_publication(tmp_path, monkeypatch):
    fact = "2026-11-27"
    monkeypatch.setattr(daily, "now", lambda: datetime(2026, 11, 27, 21, tzinfo=daily.SHANGHAI))
    monkeypatch.setattr(daily, "calendar", lambda *_: True)
    for name in daily.FINAL_FILES - {"pending-verification.csv", "coverage-receipt.md"}:
        (tmp_path / name).write_text("checked")
    daily.write_csv(tmp_path / "pending-verification.csv", [], daily.PENDING_FIELDS)
    flags = daily.GATES - {"publication_eligible=true"}
    (tmp_path / "coverage-receipt.md").write_text("\n".join(sorted(flags | {
        f"fact_date={fact}", "run_mode=replay", "publication_eligible=false",
    })))
    daily.seal(tmp_path)
    daily.check_archive(tmp_path, fact, replay=True)
    with pytest.raises(ValueError, match="四模块验收状态不完整"):
        daily.publish(tmp_path, tmp_path, fact, "test", None)
    assert not (tmp_path / "delivery-attempt.md").exists()


def test_old_day_publish_stops_before_opening_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "now", lambda: datetime(2026, 11, 30, 21, tzinfo=daily.SHANGHAI))
    with pytest.raises(ValueError, match="只发布当日"):
        daily.publish(tmp_path, tmp_path, "2026-11-27", "test", None)
    assert list(tmp_path.iterdir()) == []


def test_cli_help_runs_without_credential_loading():
    output = subprocess.run([sys.executable, daily.__file__, "--help"], capture_output=True, text=True, check=True)
    assert "replay" in output.stdout
    assert "publish" in output.stdout


def test_verified_history_repair_preserves_base_and_feeds_replay(tmp_path, monkeypatch):
    fact, day = '2026-11-27', '2026-11-26'
    monkeypatch.setattr(daily, "now", lambda: datetime(2026, 11, 28, 18, tzinfo=daily.SHANGHAI))
    history = tmp_path / 'history'
    history.mkdir()
    original = history / f'{day}.csv'
    daily.write_csv(original, [
        dict(date=day, name='甲醇', category='化工', pct='', raw_pct='--', status='source_pct_unavailable'),
        dict(date=day, name='乙醇', category='化工', pct='0.05', raw_pct='0.05%', status='observed'),
    ])
    before = original.read_bytes()
    sha = daily.digest(original)
    (history / 'coverage-receipt.txt').write_text(f'date={day} pending=0 sha256={sha}\n')
    row = dict(date=day, name='甲醇', category='化工', raw_pct='0.00%', view='化工',
               read_at='2026-11-28T08:00:00Z', evidence=f'日期按钮 {day}，化工分类甲醇 0.00%')
    repair_input = tmp_path / 'input.csv'
    daily.write_csv(repair_input, [row, dict(row, name='乙醇', evidence=f'日期按钮 {day}，化工分类乙醇 0.00%')])
    with pytest.raises(ValueError, match='乙醇 原始行不是已核实缺值'):
        daily.repair_history(tmp_path, repair_input, fact, [day], 'pytest')
    assert not (history / 'repairs').exists()

    for submitted, displayed in (
        ('0.00%', '10.00%'), ('1.10%', '-1.10%'), ('1.10%', '−1.10%'), ('1.10%', '－1.10％'),
    ):
        daily.write_csv(repair_input, [
            dict(row, raw_pct=submitted, evidence=f'日期按钮 {day}，化工分类甲醇 {displayed}')
        ])
        with pytest.raises(ValueError, match='完整涨幅原值'):
            daily.repair_history(tmp_path, repair_input, fact, [day], 'pytest')
        assert not (history / 'repairs').exists()

    daily.write_csv(repair_input, [row])
    daily.repair_history(tmp_path, repair_input, fact, [day], 'pytest')
    daily.repair_history(tmp_path, repair_input, fact, [day], 'pytest')
    assert original.read_bytes() == before
    rows, hashes = daily.history_inputs(tmp_path, [day])
    assert rows[0][('甲醇', '化工')]['pct'] == '0.00'
    assert rows[0][('乙醇', '化工')]['pct'] == '0.05'
    assert hashes[0]['sha256'] == sha and hashes[0]['repair_count'] == 1
    revision, = list((history / 'repairs').iterdir())
    assert hashes[0]['repair_revisions'] == revision.name
    assert (revision / "source.csv").read_bytes() == repair_input.read_bytes()
    wrong_id = revision.with_name("1" * 64)
    revision.rename(wrong_id)
    with pytest.raises(ValueError, match='输入哈希与修订编号不符'):
        daily.history_inputs(tmp_path, [day])
    wrong_id.rename(revision)
    (revision / 'corrections.csv').write_text('tampered')
    with pytest.raises(ValueError, match='归档哈希不一致'):
        daily.history_inputs(tmp_path, [day])
