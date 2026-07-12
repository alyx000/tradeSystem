from services.tail_scan import pk


def _cards():
    return [{"code": "600001.SH", "name": "A", "pct_chg": 9.0, "in_main_sector": True},
            {"code": "600002.SH", "name": "B", "pct_chg": 8.0, "in_main_sector": False}]


def _scored():
    return [{"code": "600001.SH", "total": 5.0}, {"code": "600002.SH", "total": 2.0}]


def test_run_pk_ok_ranks_by_wins():
    # runner 恒判 A 胜
    def runner(prompt, payload):
        return '{"winner": "A", "reason": "逻辑更硬"}'
    res = pk.run_pk(_cards(), _scored(), runner)
    assert res["status"] == "ok"
    winner = min(res["ranks"], key=res["ranks"].get)
    assert winner == "600001.SH"


def test_run_pk_skipped_when_pool_lt_2():
    res = pk.run_pk(_cards()[:1], _scored()[:1], lambda p, pl: '{"winner":"A","reason":"x"}')
    assert res["status"] == "skipped"


def test_reason_redline_filtered():
    def runner(prompt, payload):
        return '{"winner": "A", "reason": "建议买入并加仓"}'  # 含红线词
    res = pk.run_pk(_cards(), _scored(), runner)
    reasons = [m["reason"] for m in res["matches"] if m["state"] == "valid"]
    assert all("买入" not in r for r in reasons)
