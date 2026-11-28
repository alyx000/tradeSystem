"""前夜固定样本和竞价专用来源；边界、缺口不补零及幂等。"""
from datetime import datetime
import sqlite3
import pytest
from services import course_opening_auction as svc

DAY = "2026-11-27"
CODE = "688820.SH"
PEER = "002242.SZ"


def row(code=CODE, **kwargs):
    return dict(dict(ts_code=code, trade_date="20261127", price=10., vol=400, amount=4000., pre_close=8.), **kwargs)


def roster(conn, day, cal):
    members = [dict(code=CODE, core=True, previous_limit_up=False, groups=[]),
               dict(code=PEER, core=False, previous_limit_up=True, groups=[{"name":"水产"}])]
    x = dict(trade_date=day, as_of_date=cal["previous"], cutoff_at=DAY+"T09:15:00+08:00", members=members, gaps=[])
    return dict(**x, sha256=svc.data.digest(x))


@pytest.fixture
def args(tmp_path):
    db = tmp_path / "db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE trade_calendar(date text,is_open integer,exchange text)")
        c.execute("""WITH RECURSIVE d(x) AS (SELECT '2026-01-01' UNION ALL SELECT date(x,'+1 day') FROM d
                     WHERE x<'2026-12-31') INSERT INTO trade_calendar SELECT x,cast(strftime('%w',x) as integer)
                     between 1 and 5,'SSE' FROM d""")
    return dict(db_path=db, root=tmp_path/"out", now=datetime.fromisoformat("2026-11-28T12:00:00+08:00"),
                build_selection=roster, load_references=lambda *a:{})


def test_complete_is_idempotent_and_gaps_per_stock(args):
    first=svc.capture(DAY, "USER", query=lambda _: [row()], **args)
    assert first["status"]=="partial" and first["coverage"]["observed"]==1
    assert first["coverage"]["explicit_no_match"]==0 and PEER not in first["observations"]
    assert first["observations"][CODE]["open_return_pct"]==25
    args["build_selection"]=lambda *a:pytest.fail("名单已冻结")
    second=svc.capture(DAY, "launchd", query=lambda _:[row(CODE,price=3),row(PEER,price=None,vol=0,amount=0)], **args)
    assert second["observations"][CODE]==first["observations"][CODE]
    assert second["coverage"]["explicit_no_match"]==1 and second["observations"][PEER]["price"] is None
    assert second["status"]=="complete"
    assert svc.capture(DAY, "USER", query=lambda _:pytest.fail("已完整"), **args)==second


@pytest.mark.parametrize("rows", [[row(),row()], [row(trade_date="20261126")], [row(vol=400.4)],
                                  [row(amount=400000)], [row(price=True)], [row(amount=0)], []])
def test_invalid_or_missing_never_becomes_zero(rows, args):
    result=svc.capture(DAY,"USER",query=lambda _:rows,**args)
    assert result["status"]=="source_failed" and not result["observations"]
    assert result["coverage"]["explicit_no_match"]==0


def test_unavailable_keeps_source_row_count_unknown_and_daily_price_separate(args):
    args["load_references"]=lambda *a:{CODE:{"pre_close":8,"open":10}}
    def unavailable(_):
        raise svc.data.EvidenceError("连接失败")
    result=svc.capture(DAY,"USER",query=unavailable,**args)
    assert result["latest_attempt"]["source_rows"] is None and result["coverage"]["observed"]==0
    assert result["independent_daily_reference"][CODE]["open"]==10


def test_reference_fallback_and_conflict():
    f = svc.observation(row(pre_close=None),DAY,dict(open=10,pre_close=9.5))
    assert f["pre_close"]==9.5 and f["pre_close_source"]=="tushare:daily"
    with pytest.raises(svc.data.EvidenceError,match="昨收"):
        svc.observation(row(),DAY,dict(open=10,pre_close=9))
    with pytest.raises(svc.data.EvidenceError,match="开盘价"):
        svc.observation(row(),DAY,dict(open=12,pre_close=8))


def test_retry_conflicting_trade_preserves_first_observed_values(args):
    first=svc.capture(DAY,"USER",query=lambda _:[row(pre_close=None)],**args)
    latest=svc.capture(DAY,"USER",query=lambda _:[row(price=12,amount=4800,pre_close=8)],**args)
    assert first["observations"][CODE]==latest["observations"][CODE]
    assert "不一致" in latest["stock_gaps"][CODE]


def test_skip_early_weekend_and_dry_run(args):
    early=dict(args,now=datetime.fromisoformat(DAY+"T09:24:00+08:00"))
    for day,opts in [(DAY,early),("2026-11-28",args)]:
        result=svc.capture(day,"USER",query=lambda _:pytest.fail("休市/早于披露不取数"),**opts)
        assert result["status"]=="skipped"
    assert not args["root"].exists()
    svc.capture(DAY,"USER",dry_run=True,query=lambda _:[row()],**args)
    assert not args["root"].exists()


def test_nonzero_price_with_zero_volume_and_amount_is_unresolved(args):
    result=svc.capture(DAY,"USER",query=lambda _:[row(vol=0,amount=0,price=9)],**args)
    assert result["coverage"]["explicit_no_match"]==0 and "非零" in result["stock_gaps"][CODE]


def test_selection_only_core_related_or_explicit_tracked(monkeypatch):
    other, tracked, conflict = "600300.SH","600400.SH","600500.SH"
    names = {CODE:"核心",PEER:"同题材",other:"无关对象",tracked:"指定题材股",conflict:"新简称"}
    monkeypatch.setattr(svc.universe, "build_universe", lambda *a, **kw: dict(members=[
        {"code":CODE,"name":"核心","role":"core"}, {"code":conflict,"name":"新简称","role":"core"}],
        source={},sha256="core",gaps=[]))
    raw={"limit_up":{"_source":"tushare:limit_list_d"},svc.tracked_theme_members.KEY:{}}
    monkeypatch.setattr(svc.universe,"_previous_market",lambda *a:(raw,names,{},{}))
    monkeypatch.setattr(svc.data,"limit_rows",lambda *a:[{"code":code,"name":name if code!=conflict else "旧简称"}
                                                          for code,name in names.items() if code!=CODE])
    monkeypatch.setattr(svc.universe,"_theme_groups",lambda *a:([
        ({"name":"无关榜首"},{other:names[other]}), ({"name":"与核心同题材"},{CODE:names[CODE],PEER:names[PEER]})],{},[]))
    monkeypatch.setattr(svc.universe,"_tracked_groups",lambda *a:([({"name":"事前选定"},
                                                                    {tracked:names[tracked]})],{},[]))
    def absent(*args):
        raise ValueError("没有手写来源")
    monkeypatch.setattr(svc,"load_entry_before",absent)
    result=svc.select(None,DAY,{"previous":"2026-11-26","history":["2026-11-25","2026-11-26"]})
    by_code={m["code"]:m for m in result["members"]}
    assert set(by_code)=={CODE,PEER,tracked,conflict}
    assert by_code[conflict]["previous_limit_up"] is None
    assert by_code[tracked]["groups"][0]["explicitly_tracked"]


@pytest.mark.parametrize("local", [True,False])
def test_reference_supplement_keeps_original_source_row_and_acquisition_time(args, local):
    first=svc.capture(DAY,"USER",query=lambda _:[row(pre_close=None)],**args)["observations"][CODE]
    later="2026-11-28T14:00:00+08:00"
    args["now"]=datetime.fromisoformat(later)
    if local:
        args["load_references"]=lambda *a:{CODE:{"open":10.,"pre_close":8.}}
    new=svc.capture(DAY,"USER",query=lambda _:[row()],**args)["observations"][CODE]
    assert first["raw"]==new["raw"] and new["raw"]["pre_close"] is None
    assert new["acquired_at"]==first["acquired_at"] and new["reference_verified_at"]==later
    assert all(new[key]==first[key] for key in ("status","price","volume_shares","amount_cny"))
    assert new["pre_close"]==8. and new["open_return_pct"]==25.
    assert new["reference_supplement"]["source"]==("tushare:daily" if local else svc.SOURCE)


def test_later_conflicting_daily_context_preserves_original_for_finished_stock(args):
    args["load_references"]=lambda *a:{CODE:{"open":10.,"pre_close":8.}}
    first=svc.capture(DAY,"USER",query=lambda _:[row()],**args)
    args["load_references"]=lambda *a:{CODE:{"open":11.,"pre_close":8.}}
    retry=svc.capture(DAY,"USER",query=lambda _:[row(PEER)],**args)
    assert retry["status"]=="partial" and retry["coverage"]["observed"]==2
    assert retry["observations"][CODE]==first["observations"][CODE]
    assert retry["independent_daily_reference"][CODE]["open"]==10
    assert retry["reference_conflicts"][CODE]["received"]["open"]==11
    assert "独立日线参考" in retry["stock_gaps"][CODE]
    args["load_references"]=lambda *a:{CODE:{"open":10.,"pre_close":8.}}
    final=svc.capture(DAY,"USER",query=lambda _:pytest.fail("已有成交不能为刷新参考而重新请求"),**args)
    assert final["status"]=="complete" and not final["reference_conflicts"]
