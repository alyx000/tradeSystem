from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from analyzers import tracked_theme_members as t
from providers.base import DataResult
from providers.tushare_provider import TushareProvider
from services import handwritten_review as hw
from scripts.tests.test_theme_launch import DAY, PREV, THEME, calendar, inputs, save

NEXT = "885002.TI"
NOW = datetime.fromisoformat(DAY + "T20:00:00+08:00")


def catalog():
    return dict(date=DAY, fetched_at=NOW.isoformat(), pages=[2, 0], rows=[
        dict(ts_code=THEME, name="题材甲", exchange="A", type="N"),
        dict(ts_code=NEXT, name="人形机器人", exchange="A", type="N"),
    ])


def snap(day, code, stocks):
    rows = [dict(ts_code=code, con_code=c, con_name=n, is_new="Y") for c, n in stocks]
    return dict(date=day, fetched_at=day + "T20:00:00+08:00",
                themes={code: dict(status="complete", pages=[len(rows), 0], rows=rows)})


def launch(day, membership):
    return dict(trade_date=day, evidence=dict(membership_source=t.SOURCE, membership=membership))


def test_existing_headings_and_explicit_directives():
    entry = dict(raw="普通提及，不算选题", sections={"次日具体条件": "验证条件", "固定对象跨日反馈":
        "#### 题材分组与新增观察（L3）\n**1. 主板块（细分一／细分二）⬆️（次日待验证）**\n"
        "**2. 人形机器人／物理AI ↕️**\n#### 别的标题\n**3. 不应选**"})
    rows, basis = t._requests(entry)
    assert [r["requested"] for r in rows] == ["主板块", "细分一", "细分二", "人形机器人", "物理AI"]
    assert basis == "handwritten_theme_group"
    entry["raw"] = "次日跟踪题材：无"
    assert t._requests(entry) == ([], "user_handwritten")
    entry["raw"] = "次日跟踪题材：人形机器人 885002.TI、PCB"
    assert [r["requested"] for r in t._requests(entry)[0]] == ["人形机器人 885002.TI", "PCB"]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = calendar(tmp_path)
    root = tmp_path / "review"
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(PREV + "T21:00:00+08:00")
    monkeypatch.setattr(hw, "datetime", Clock)
    src = tmp_path / "source.txt"
    src.write_text("次日跟踪题材：题材甲、人形机器人、机器人")
    sup = tmp_path / "supp.md"
    sup.write_text("".join("## " + name + "\n待核\n" for name in hw.SECTIONS))
    hw.import_entry(root, PREV, src, sup, "USER")
    local = tmp_path / "daily"
    old = snap(PREV, NEXT, [("600002.SH", "旧有")])
    save(local, PREV, dict(theme_launch=launch(PREV, old)))
    e = inputs()
    for rank in range(2, 11):
        e["ranking"]["data"].append(dict(ts_code=f"8851{rank:02d}.TI", name=f"其他{rank}", rank=rank,
                                        trade_date=DAY.replace("-", "")))
    raw = dict(date=DAY, limit_cpt_list=e["ranking"], theme_launch=launch(DAY, e["membership"]))
    data = snap(DAY, NEXT, [("600003.SH", "新增")])
    def run(*, registry=True, now=NOW, custom_catalog=None):
        calls = []
        def call(method, *args):
            calls.append((method, args))
            if method == "get_concept_catalog_snapshot":
                return DataResult(custom_catalog or catalog(), t.CATALOG)
            assert method == "get_theme_member_snapshot"
            return DataResult(data, t.SOURCE)
        reg = SimpleNamespace(call=call) if registry else None
        return t.collect(reg, DAY, raw, daily_dir=local, db_path=db, archive_root=root, now=now), calls
    return run, data, raw, local, root


def test_tracks_outside_top_ten_and_reuses_previous_top_evidence(setup):
    run, data, raw, local, root = setup
    block, calls = run()
    assert calls == [("get_concept_catalog_snapshot", (DAY,)), ("get_theme_member_snapshot", (DAY, [NEXT]))]
    assert block["selection"]["date"] == PREV and block["selection"]["source_sha256"]
    assert block["status"] == "partial"
    rows = {r["name"]: r for r in block["rows"]}
    assert rows["机器人"]["status"] == "identity_unverified"
    assert rows["题材甲"]["top10_rank"] == 1 and rows["题材甲"]["added"] is None
    assert rows["人形机器人"]["added"] == ["600003.SH"] and rows["人形机器人"]["removed"] == ["600002.SH"]
    assert rows["人形机器人"]["top10_known"] and rows["人形机器人"]["top10_rank"] is None
    assert block["snapshots"][NEXT] == data
    save(local, DAY, {t.KEY: block, "theme_launch": raw["theme_launch"]})
    recomputed, calls = run(registry=False, now=datetime.fromisoformat("2026-11-03T18:00:00+08:00"))
    assert calls == [] and recomputed == block


def test_incomplete_catalog_and_duplicate_names(setup):
    run, *_ = setup
    broken = catalog()
    broken["pages"] = [2]
    block, calls = run(custom_catalog=broken)
    assert block["status"] == "source_failed" and len(calls) == 1
    duplicate = catalog()
    duplicate["rows"][1]["name"] = "题材甲"
    block, calls = run(custom_catalog=duplicate)
    assert len(calls) == 1 and all(r["status"] == "identity_unverified" for r in block["rows"])
    assert t._resolve("题材甲 " + NEXT, {NEXT: "另有名字"}) is None
    assert t._resolve(NEXT, {NEXT: "人形机器人"}) == NEXT


def test_history_and_unclosed_membership_refuse_to_guess(setup):
    run, data, raw, _, _ = setup
    block, calls = run(now=datetime.fromisoformat("2026-11-03T18:00:00+08:00"))
    assert not calls and block["status"] == "partial" and "目录未归档" in str(block["gaps"])
    data["themes"][NEXT]["pages"] = [1]
    raw.pop("limit_cpt_list")
    block, _ = run()
    row = next(r for r in block["rows"] if r["code"] == NEXT)
    assert not row["top10_known"] and row["status"] == "source_failed" and row["added"] is None
    assert next(r for r in block["rows"] if r["code"] == THEME)["status"] == "complete"


def test_catalog_provider_pages_until_empty_and_rejects_duplicates():
    provider = TushareProvider()
    calls = []
    row = catalog()["rows"][0]
    def query(endpoint, **params):
        calls.append((endpoint, params))
        return pd.DataFrame([] if params["offset"] else [row])
    provider.pro = SimpleNamespace(query=query)
    today = datetime.now(t.SHANGHAI).date().isoformat()
    result = provider.get_concept_catalog_snapshot(today)
    assert result.success and result.data["pages"] == [1, 0]
    assert [p["offset"] for _, p in calls] == [0, 1] and all(p["type"] == "N" for _, p in calls)
    provider.pro = SimpleNamespace(query=lambda *args, **kwargs: pd.DataFrame([row]))
    assert not provider.get_concept_catalog_snapshot(today).success


def test_historical_explicit_code_uses_dated_concept_source_without_catalog(setup, monkeypatch):
    run, _, raw, _, _ = setup
    raw["concept_moneyflow_ths"] = dict(_source="tushare:moneyflow_cnt_ths", data=[
        dict(ts_code=THEME, name="题材甲", trade_date=DAY.replace("-", "")),
        dict(ts_code=NEXT, name="人形机器人", trade_date=DAY.replace("-", ""))])
    monkeypatch.setattr(t, "_requests", lambda _: ([
        {"requested": THEME, "source": "user_handwritten", "quote": THEME},
        {"requested": NEXT, "source": "user_handwritten", "quote": NEXT},
        {"requested": "题材甲", "source": "user_handwritten", "quote": "题材甲"}], "user_handwritten"))
    block, calls = run(registry=False)
    assert not calls
    by_code = {row["code"]: row for row in block["rows"]}
    assert by_code[THEME]["status"] == "complete"
    assert by_code[NEXT]["status"] == "source_failed" and by_code[NEXT]["count"] is None
    assert by_code[None]["status"] == "identity_unverified" and by_code[None]["candidate_codes"] == [THEME]


def test_only_curated_aliases_matching_current_code_and_name():
    catalog = {"885959.TI": "PCB概念", "886033.TI": "共封装光学(CPO)", "885633.TI": "染料",
               "885517.TI": "机器人概念", "885000.TI": "存储芯片"}
    assert t._resolve("PCB", catalog) == "885959.TI"
    assert t._resolve("CPO", catalog) == "886033.TI"
    assert t._resolve("染料", catalog) == "885633.TI"
    assert t._resolve("PCB", {"885959.TI": "已更名"}) is None
    for term in ("物理AI", "机器人", "HBM", "光通信"):
        assert t._resolve(term, catalog) is None


def test_retry_with_unavailable_review_preserves_successful_snapshots(setup):
    run, _, raw, local, root = setup
    before, _ = run()
    save(local, DAY, {t.KEY: before, "theme_launch": raw["theme_launch"]})
    (root / PREV / "source.txt").write_text("来源被改动")
    after, calls = run()
    assert not calls and after["status"] == "fallback_preserved"
    assert after["snapshots"] == before["snapshots"]
    assert after["latest_attempt"]["status"] == "source_failed"


def test_vendor_exception_isolated_to_new_block(setup, monkeypatch):
    run, *_ = setup
    monkeypatch.setattr(t, "_catalog_rows", lambda *_: (_ for _ in ()).throw(RuntimeError("模拟目录接口出错")))
    block, _ = run()
    assert block["status"] == "source_failed" and "RuntimeError" in str(block["gaps"])
