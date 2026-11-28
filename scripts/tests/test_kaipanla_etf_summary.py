from copy import deepcopy

import pytest

from automations import kaipanla_etf as etf


def snapshot(day, flow_day, amount, *, count="15", change="-8.80%", duration="4", total="10.71亿"):
    date = f"2026-11-{day}"
    update = date + " 21:06"
    name = "芯片ETF"
    direction = dict(name=name, scope="全部", flow_date=f"2026-11-{flow_day}", flow_amount_raw=amount,
                     disclosure_status="境内已到披露窗口", constituent_count=count)
    motion = dict(name=name, bucket=etf.BUY, updated_at=update, duration_days=duration,
                  interval_change_pct_raw=change, interval_flow_raw=total, constituent_count=count)
    return dict(date=date, flow_date=direction["flow_date"], update=update, reference_date="",
                market=[], directions={name: direction}, motion={name: motion}, flows=[],
                meta={"动向": {"scope": "全部", "boundary": "已核验"}})


def test_three_disclosures_and_adjacent_motion_include_all_paired_values(monkeypatch):
    older = snapshot(25, 24, "1亿")
    prior = snapshot(26, 25, "3亿")
    current = snapshot(27, 26, "-2亿", duration="5", change="-10.40%", total="14.59亿")
    monkeypatch.setattr(etf, "_snapshot", deepcopy)
    result = etf.analyze_etf(current, prior, [older])
    text = result["summary"]
    assert "11/24→11/25→11/26" in text and "1亿→3亿→-2亿" in text and "净申购转净赎回" in text
    assert "11/26 21:06→11/27 21:06" in text and "4→5日" in text
    assert "10.71亿→14.59亿" in text and "-8.80%→-10.40%" in text
    assert result["flow_comparisons"][0]["date_0"] == "2026-11-24"
    assert "2026-11-24 / 1亿" in result["details"]


@pytest.mark.parametrize("problem", ["first", "missing_price", "changed_count", "changed_scope", "same_update", "missing_row_update"])
def test_absent_or_incomparable_motion_keeps_visible_current_values(monkeypatch, problem):
    prior = snapshot(26, 25, "3亿")
    current = snapshot(27, 26, "5亿", duration="5", change="-10.40%", total="14.59亿")
    if problem == "first":
        prior = None
    elif problem == "missing_price":
        current["motion"]["芯片ETF"]["interval_change_pct_raw"] = "--"
    elif problem == "changed_count":
        prior["motion"]["芯片ETF"]["constituent_count"] = "14"
    elif problem == "changed_scope":
        prior["meta"]["动向"]["scope"] = "其他"
    elif problem == "same_update":
        prior["update"] = current["update"]
    else:
        prior["motion"]["芯片ETF"]["updated_at"] = ""
    monkeypatch.setattr(etf, "_snapshot", deepcopy)
    text = etf.analyze_etf(current, prior)["summary"]
    assert "本期值" in text and "4→5日" not in text
    assert ("区间涨跌未披露" if problem == "missing_price" else "区间涨跌-10.40%") in text


def test_duplicate_disclosure_date_skipped_while_motion_uses_adjacent_report(monkeypatch):
    older = snapshot(24, 23, "1亿")
    middle = snapshot(25, 24, "3亿")
    prior = snapshot(26, 25, "4亿")
    current = snapshot(27, 25, "4亿", duration="5", change="-10.40%", total="14.59亿")
    monkeypatch.setattr(etf, "_snapshot", deepcopy)
    text = etf.analyze_etf(current, prior, [middle, older])["summary"]
    assert "11/23→11/24→11/25" in text
    assert "动向11/26 21:06→11/27 21:06" in text
