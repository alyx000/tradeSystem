from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import sqlite3

import pytest

from analyzers import sector_increment as S


def inputs():
    codes = ["600000.SH", "000001.SZ"]
    identities = [dict(ts_code=c, list_date="20000101", delist_date=None) for c in codes]
    industry = {codes[0]: {"sw_l2": "半导体"}, codes[1]: {"sw_l2": "银行"}}
    def quotes(d, amounts):
        return [dict(ts_code=c, trade_date=d, amount=a*1e5) for c, a in zip(codes, amounts)]
    return quotes("2026-09-18", [160, 90]), quotes("2026-09-17", [100, 100]), identities, industry


def calc(args=None):
    return S.calculate(*(args or inputs()), "2026-09-18", "2026-09-17", min_count=2)


def test_full_sector_net_increment_and_negative_contribution():
    r = calc()
    assert r["status"] == "complete"
    assert r["market"]["delta_yi"] == 50
    assert [x["contribution_pct"] for x in r["rows"]] == [120, -20]
    assert sum(x["share_delta_pp"] for x in r["rows"]) == pytest.approx(0)
    assert S.validate(r, r["trade_date"]) == r
    assert "+120.00" in "\n".join(S.render(r))


@pytest.mark.parametrize("amounts", [[99, 100], [100, 100], [100.1, 100]])
def test_shrink_flat_near_zero_do_not_divide(amounts):
    args = inputs()
    for row, amount in zip(args[0], amounts):
        row["amount"] = amount*1e5
    result = calc(args)
    assert result["status"] == "complete"
    assert all(r["contribution_pct"] is None for r in result["rows"])
    assert "未计算" in "\n".join(S.render(result))


@pytest.mark.parametrize("mutate", [
    lambda a: a[0].append(a[0][0].copy()),
    lambda a: a[0][0].update(amount=None),
    lambda a: a[0][0].update(amount=float("nan")),
    lambda a: a[0][0].update(amount=-1),
    lambda a: a[0][0].update(trade_date="20260916"),
    lambda a: a[0][0].update(ts_code="600000.SZ"),
    lambda a: a[3].clear(),
    lambda a: a[0].pop(),
])
def test_bad_sources_fail_closed(mutate):
    args = inputs()
    mutate(args)
    r = calc(args)
    assert r["status"] == "source_failed"
    assert r["rows"] == []


def test_partial_small_missing_universe_is_not_zero():
    codes = [f"{600000+i}.SH" for i in range(100)]
    identity = [dict(ts_code=c, list_date="20000101") for c in codes]
    mapping = {c: {"sw_l2": "行业"} for c in codes}
    rows = lambda d: [dict(ts_code=c, trade_date=d, amount=1e5) for c in codes[:-1]]
    r = S.calculate(rows("2026-09-18"), rows("2026-09-17"), identity, mapping,
                    "2026-09-18", "2026-09-17", min_count=90)
    assert r["status"] == "partial"
    assert r["coverage"]["2026-09-18"]["missing_codes"] == [codes[-1]]


def test_tampered_snapshot_and_html_escape():
    r = calc()
    r["rows"][0]["contribution_pct"] = 9
    assert S.validate(r, r["trade_date"])["status"] == "source_failed"
    r = calc()
    r["rows"][0]["industry"] = '<script>alert(1)</script>'
    html, gaps = S.render_html(r, r["trade_date"])
    assert "<table>" in html and "<script>" not in html and not gaps
    html, gaps = S.render_html(r, "2026-09-19")
    assert "source_failed" in html and gaps


def test_calendar_closed_missing_and_preclose_do_not_fetch(tmp_path):
    path = tmp_path/"calendar.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar(date TEXT, exchange TEXT,is_open INT)")
    target = datetime(2026, 9, 18, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []
    registry = SimpleNamespace(call=lambda *a: calls.append(a))
    assert S.collect(registry, "2026-09-18", db_path=path, now=target)["status"] == "source_failed"
    for i in range(21):
        d = (target-timedelta(days=i)).date()
        conn.execute("INSERT INTO trade_calendar VALUES (?, 'SSE', ?)", (d.isoformat(), int(d.weekday() < 5)))
    conn.commit()
    assert S.collect(registry, "2026-09-18", db_path=path, now=target)["status"] == "skipped"
    assert not calls
    conn.close()


def test_collector_failure_is_isolated(tmp_path):
    class BadRegistry:
        def call(self, *args):
            raise RuntimeError("offline")
    r = S.collect(BadRegistry(), "2026-09-18", db_path=tmp_path/"missing.db")
    assert r["status"] == "source_failed" and r["gaps"]


def test_official_assembler_owns_block_and_checks_source(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    module = _load_assembler()
    day = "2026-09-18"
    _write_chunks(tmp_path, day)
    block = calc()
    html = module.render_report(tmp_path, day, sector_increment=block)
    assert html.count("data-sector-increment=") == 1
    module.validate_report(html, sector_increment=block)
    tampered = html.replace("+120.00", "+999.00")
    with pytest.raises(module.ReportValidationError, match="sector_increment_source_mismatch"):
        module.validate_report(tampered, sector_increment=block)
    s2 = tmp_path/f"b{day}_s2.html"
    s2.write_text(s2.read_text()+f'<div data-sector-increment="{day}">伪造</div>')
    with pytest.raises(module.ReportValidationError, match="duplicate_sector_increment"):
        module.render_report(tmp_path, day, sector_increment=block)


def test_large_html_table_is_folded():
    r = calc()
    row = r["rows"][0]
    # 拆分同一行成11个分组，保持市场与贡献算术一致。
    r["rows"] = [r["rows"][1]]+[
        {k: (f"行业{i}" if k == "industry" else v/11) for k, v in row.items()}
        for i in range(11)]
    html, gaps = S.render_html(r, r["trade_date"])
    assert '<details class="evidence"' in html and "全量12个行业" in html and not gaps
    assert 'data-as-of="2026-09-18" data-items="12"' in html
    assert " open" not in html
