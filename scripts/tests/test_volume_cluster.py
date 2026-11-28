from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from analyzers import sector_increment as S
from services.trend_leader import volume_cluster as V, review_evidence as R

DAY = "2026-11-10"
CODE = "600000"


def sample():
    days, d = [], date.fromisoformat(DAY)
    while len(days) < 120:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    days.reverse()
    bars = [dict(trade_date=d, ts_code=CODE+".SH", open=12., high=12.1, low=11.9,
                 close=12., vol=100.) for d in days]
    for i in range(45, 65):
        bars[i].update(open=30., high=30.1, low=29.9, close=30.)
    bars[89].update(open=9.5, high=9.6, low=9., close=9.2)
    bars[94].update(open=12.6, high=13., low=12.5, close=12.8)
    bars[99].update(open=8.5, high=8.6, low=8., close=8.2)
    for i in range(100, 105):
        px=9.+(i-100)*.2
        bars[i].update(open=px, close=px+.2, low=px-.1, high=px+.3, vol=180.)
    for b in bars[105:]:
        b.update(open=9.7, high=9.8, low=9.3, close=9.5, vol=70.)
    factors=[dict(ts_code=CODE+".SH", trade_date=b["trade_date"], adj_factor=1.) for b in bars]
    return bars, factors


def sector():
    identities=[dict(ts_code=c, list_date="20000101") for c in (CODE+".SH", "000001.SZ")]
    mapping={CODE+".SH":dict(sw_l2="银行"), "000001.SZ":dict(sw_l2="其他")}
    rows=lambda day, amounts:[dict(ts_code=c["ts_code"], trade_date=day, amount=a*1e5) for c,a in zip(identities,amounts)]
    return S.calculate(rows(DAY,[150,100]),rows("2026-11-09",[100,100]),identities,mapping,DAY,"2026-11-09",min_count=2)


def payload(bars=None, factors=None, block=True):
    original_bars,original_factors=sample()
    item=V.capture(bars if bars is not None else original_bars,
                   factors if factors is not None else original_factors,CODE,DAY,"tushare:daily","tushare:adj_factor")
    return V.collect(DAY,[dict(code=CODE,name="测试证券",industry="银行")],{CODE:item},sector() if block else None,[])


def test_cluster_structure_and_paired_sector_evidence():
    p=payload(); r=p["rows"][0]; bars,_=sample()
    assert p["status"] == "complete"
    assert r["state"] == "pullback_observed"
    assert r["rebound_peak_date"] == bars[94]["trade_date"]
    assert r["prior_low_date"] == bars[89]["trade_date"]
    assert r["rebreak_low_date"] == bars[99]["trade_date"]
    assert r["cluster_start"] == bars[100]["trade_date"]
    assert r["cluster_end"] == bars[104]["trade_date"]
    assert r["cluster_volume_ratio"] == 1.8
    assert r["pullback_volume_ratio"] == pytest.approx(70/180)
    assert r["sector_support"]["supported"] is True
    assert p["coverage"] == dict(requested=1,price_volume_verified=1,sector_verified=1)


def test_one_spike_high_position_and_no_rebreak_are_not_cluster():
    bars,f=sample()
    for b in bars[100:105]: b["vol"]=100.
    bars[102]["vol"]=1000.
    assert payload(bars,f)["rows"][0]["state"] == "not_observed"
    bars,f=sample()
    for b in bars[45:65]: b.update(open=12.,high=12.1,low=11.9,close=12.)
    assert payload(bars,f)["rows"][0]["state"] == "not_observed"
    bars,f=sample();bars[89].update(open=7.5,low=7.,high=7.8,close=7.6)
    assert payload(bars,f)["rows"][0]["state"] == "not_observed"


def test_first_cluster_anchor_and_invalidation_do_not_revive():
    bars,f=sample();bars[110].update(open=8.,low=7.9,high=8.2,close=8.1)
    r=payload(bars,f)["rows"][0]
    assert r["state"] == "invalidated" and r["invalidated_date"] == bars[110]["trade_date"]
    assert r["cluster_end"] == bars[104]["trade_date"]
    bars[-1].update(open=13.,high=14.,low=12.8,close=13.5)
    assert payload(bars,f)["rows"][0]["state"] == "invalidated"


def test_price_crossing_prior_high_is_independent_of_sector():
    bars,f=sample();bars[-1].update(open=12.9,low=12.8,high=13.6,close=13.5)
    p=payload(bars,f,block=False)
    assert p["rows"][0]["state"] == "above_prior_peak"
    assert p["rows"][0]["sector_support"]["supported"] is None
    assert p["status"] == "partial" and p["coverage"]["price_volume_verified"] == 1


@pytest.mark.parametrize("change", [
    lambda b,f:b.append(deepcopy(b[-1])),
    lambda b,f:f.append(deepcopy(f[-1])),
    lambda b,f:b[-1].update(trade_date="2026-11-11"),
    lambda b,f:f[-1].update(trade_date="2026-11-11"),
    lambda b,f:f.pop(),
    lambda b,f:b[-1].update(ts_code="000001.SZ"),
    lambda b,f:f[-1].update(ts_code="000001.SZ"),
    lambda b,f:b[-1].update(vol=float("nan")),
    lambda b,f:b[-1].update(vol=True),
    lambda b,f:b[-1].update(high=1),
    lambda b,f:f[-1].update(adj_factor=0),
    lambda b,f:(b.pop(0),f.pop(0)),
])
def test_invalid_sources_are_unknown_not_negative(change):
    bars,f=sample();change(bars,f)
    p=payload(bars,f)
    assert p["status"] == "partial" and p["rows"][0]["state"] == "uncomputed"
    assert p["coverage"]["price_volume_verified"] == 0


def test_factor_change_in_volume_comparison_is_not_silently_compared():
    bars,f=sample()
    # Keep adjusted prices unchanged to isolate the volume-comparability gate.
    for i in range(110,len(bars)):
        f[i]["adj_factor"]=2.
        for key in ("open","high","low","close"): bars[i][key]/=2
    r=payload(bars,f)["rows"][0]
    assert r["state"] == "uncomputed" and "可比性" in r["reason"]


@pytest.mark.parametrize("change", [
    lambda p:p["rows"][0].update(cluster_volume_ratio=99),
    lambda p:p["rows"][0].update(state="above_prior_peak"),
    lambda p:p["rows"][0]["sector_support"].update(supported=False),
    lambda p:p["coverage"].update(requested=0),
    lambda p:p["parameters"].update(volume_ratio_min=1),
    lambda p:p.update(trade_date="2026-11-09"),
])
def test_replay_rejects_derived_value_tampering(change):
    p=payload();change(p)
    assert V.validate(p,DAY)["status"] == "source_failed"


def test_source_error_empty_and_old_snapshot_are_distinct():
    assert V.collect(DAY,[],{},None,[])["status"] == "complete"
    assert V.collect(DAY,[],{},None,["limit_list_failed"])["status"] == "partial"
    assert V.validate(None,DAY)["status"] == "not_computed"
    p=payload();p["evidence"]["quotes"][CODE]["quote_source"]="unverified"
    p=V.calculate(DAY,p["evidence"])
    assert p["rows"][0]["state"] == "uncomputed"


def test_render_source_replay_and_isolated_degradation(tmp_path):
    from tests.test_trend_review_evidence import summary
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    # Use the same day as the existing parent fixture; sources retain exact day binding.
    s=summary();day=s["date"]
    p=payload();shift=date.fromisoformat(DAY)-date.fromisoformat(day)
    def shift_dates(obj):
        if isinstance(obj,dict): return {k:shift_dates(v) for k,v in obj.items()}
        if isinstance(obj,list): return [shift_dates(v) for v in obj]
        if isinstance(obj,str) and len(obj)==10:
            try:return (date.fromisoformat(obj)-shift).isoformat()
            except ValueError:pass
        return obj
    evidence=shift_dates(p["evidence"])
    evidence["candidates"][0]["name"]="<script>测试</script>"
    s["volume_cluster"]=V.calculate(day,evidence)
    checked=R.build(s,"original")
    (tmp_path/f"{day}.md").write_text("original")
    R.write(tmp_path,day,checked)
    assert R.load(tmp_path,day)["volume_cluster"]["status"] == "complete"
    assembler=_load_assembler();_write_chunks(tmp_path,day)
    html=assembler.render_report(tmp_path,day,trend_review_evidence=checked)
    assembler.validate_report(html,trend_review_evidence=checked)
    assert "&lt;script&gt;测试" in html and "<script>测试" not in html
    assert "低位连续堆量与板块背景" in html
    with pytest.raises(assembler.ReportValidationError):
        assembler.validate_report(html.replace("1.80倍","9.99倍"),trend_review_evidence=checked)
    checked["volume_cluster"]["rows"][0]["cluster_volume_ratio"]=99
    degraded=R.validate(checked,day)
    assert degraded["status"] == "complete"  # new card failure never closes original observations
    assert degraded["volume_cluster"]["status"] == "source_failed"
    _,gaps=R.render(degraded,day)
    assert any("低位连续堆量" in g for g in gaps)


def test_scanner_includes_rejected_candidates_and_exited_objects_without_new_calls(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from db.schema import init_schema
    from services.trend_leader import scanner, pool, renderer
    conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row;init_schema(conn)
    conn.execute("INSERT INTO daily_market(date,raw_data) VALUES (?,?)",(DAY,json.dumps({"sector_increment":sector()})))
    conn.commit()
    pool.record(conn,code="600001",name="旧观察",sw_l2="银行",first_limit_date="2026-11-05",date="2026-11-09")
    monkeypatch.setattr(scanner,"_main_sectors",lambda *a:(["银行"],dict(status="exact",source_date=DAY,snapshot_count=1,required_hits=1)))
    class Registry:
        def __init__(self): self.calls=[]
        def call(self,method,*args):
            self.calls.append((method,args))
            if method == "get_limit_up_list":
                return SimpleNamespace(success=True,data={"stocks":[dict(code=CODE,name="未入池案例")]},source="test")
            if method == "get_stock_sw_industry_map":
                return SimpleNamespace(success=True,data={c+".SH":dict(sw_l2="银行") for c in (CODE,"600001")})
            if method == "get_market_daily_changes": return SimpleNamespace(success=True,data=[])
            bars,factors=sample()
            if method in {"get_stock_daily_range","get_stock_adj_factor_range"}:
                for r in bars+factors:r["ts_code"]=args[0]+".SH"
                if args[0]=="600001":bars[-1].update(open=8.,high=8.1,low=7.,close=7.5)
                data=bars if method=="get_stock_daily_range" else factors
                source="tushare:daily" if method=="get_stock_daily_range" else "tushare:adj_factor"
                return SimpleNamespace(success=True,data=data,source=source)
            return SimpleNamespace(success=False,data=None)
    reg=Registry();result=scanner.run_daily(conn,reg,DAY,main_line="l2")
    assert result["entered"] == [] and result["exited"] == ["600001"]
    assert result["volume_cluster"]["coverage"]["requested"] == 2
    assert [r["code"] for r in result["volume_cluster"]["rows"]] == [CODE,"600001"]
    assert result["volume_cluster"]["rows"][0]["state"] == "pullback_observed"
    assert result["volume_cluster"]["rows"][1]["state"] == "invalidated"
    assert sum(m=="get_stock_daily_range" for m,a in reg.calls) == 2
    assert sum(m=="get_stock_adj_factor_range" for m,a in reg.calls) == 2
    assert "低位连续堆量与板块背景" in renderer.render_daily(conn,result)
    assert pool.list_pool(conn,status="active") == []
    conn.close()


def test_second_request_failure_does_not_preserve_first_success():
    from types import SimpleNamespace
    from services.trend_leader import scanner
    class Registry:
        def call(self,*args):return SimpleNamespace(success=False,data=None)
    cache={CODE:payload()["evidence"]["quotes"][CODE]}
    scanner._bars(Registry(),CODE,"2026-08-01",DAY,cluster_cache=cache)
    assert "error" in cache[CODE] and "bars" not in cache[CODE]


def test_first_window_after_confirmed_peak_can_finish_today():
    bars,f=sample()
    for b in bars[89:]: b.update(open=12.,high=12.1,low=11.9,close=12.,vol=100.)
    bars[107].update(open=9.5,high=9.6,low=9.,close=9.2)
    bars[112].update(open=12.6,high=13.,low=12.5,close=12.8)
    bars[114].update(open=8.5,high=8.6,low=8.,close=8.2)
    for i in range(115,120):
        px=9.+(i-115)*.2
        bars[i].update(open=px,close=px+.2,low=px-.1,high=px+.3,vol=180.)
    r=payload(bars,f)["rows"][0]
    assert r["state"] == "cluster_observed"
    assert r["rebound_peak_date"] == bars[112]["trade_date"]
    assert r["cluster_start"] == bars[115]["trade_date"]
    assert r["cluster_end"] == DAY and r["cluster_age_bars"] == 0


@pytest.mark.parametrize("key,value,expected",[("vol",float("nan"),"partial"),
                                             ("vol",float("inf"),"partial"),
                                             ("amount",float("nan"),"complete"),
                                             ("amount",float("inf"),"complete")])
def test_nonfinite_source_never_breaks_parent_snapshot(tmp_path,key,value,expected):
    bars,f=sample();bars[-1][key]=value
    p=payload(bars,f)
    assert p["status"] == expected
    if key=="vol": assert p["rows"][0]["state"] == "uncomputed"
    s=dict(date=DAY,launch_pullbacks=[],research_cards=[],
           research_coverage=dict(eligible=0,collected=0,not_collected=[]),volume_cluster=p)
    parent=R.build(s,"original")
    (tmp_path/f"{DAY}.md").write_text("original")
    R.write(tmp_path,DAY,parent)
    read=R.load(tmp_path,DAY)
    assert read["status"] == "complete" and read["volume_cluster"]["status"] == expected
    assert "NaN" not in (tmp_path/f"{DAY}.review.json").read_text()


def test_unsafe_archived_evidence_is_isolated():
    p=payload();p["evidence"]["quotes"][CODE]["bars"][-1]["vol"]=float("nan")
    checked=V.validate(p,DAY)
    assert checked["status"] == "source_failed" and "evidence" not in checked
    json.dumps(checked,allow_nan=False)


@pytest.mark.parametrize("full_available", [True, False])
def test_core_scan_still_sees_only_its_short_window(full_available):
    from types import SimpleNamespace
    from services.trend_leader import scanner

    bars, _ = sample()
    start = bars[-35]["trade_date"]
    calls = []

    class Registry:
        def call(self, capability, code, since, until):
            calls.append((capability, code, since, until))
            ok = since == start or full_available
            rows = [row for row in bars if row["trade_date"] >= since] if ok else []
            return SimpleNamespace(data=rows, success=ok, source="tushare:daily")

    history = {}
    result = scanner._bars(Registry(), CODE, start, DAY, cluster_cache=history)
    assert result == bars[-35:]
    assert calls[0][2] < bars[0]["trade_date"]
    if full_available:
        assert len(calls) == 1 and history[CODE]["bars"] == bars
    else:
        assert len(calls) == 2 and calls[1][2] == start
        assert "error" in history[CODE] and "bars" not in history[CODE]
