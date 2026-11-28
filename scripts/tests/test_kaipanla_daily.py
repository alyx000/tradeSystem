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
