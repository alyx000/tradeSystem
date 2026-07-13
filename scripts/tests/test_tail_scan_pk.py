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


def test_play_match_retries_on_none_then_succeeds():
    calls = []

    def runner(prompt, payload):
        calls.append(1)
        if len(calls) == 1:
            return None  # 首次返回 None（非异常，模拟 build_llm_runner 的失败语义）
        return '{"winner": "A", "reason": "x"}'

    runner.last_diagnostics = None
    winner, reason = pk._play_match(_cards()[0], _cards()[1], runner)
    assert len(calls) == 2  # 确认发生了重试
    assert winner == "600001.SH"
    assert reason == "x"


def test_no_retry_on_timeout():
    calls = []

    def runner(prompt, payload):
        calls.append(1)
        runner.last_diagnostics = {"reason": "timeout"}
        return None

    runner.last_diagnostics = None
    winner, reason = pk._play_match(_cards()[0], _cards()[1], runner)
    assert winner is None
    assert reason is None
    assert len(calls) == 1  # 超时不重试


def test_melted_on_last_match_over_budget():
    def runner(prompt, payload):
        return '{"winner": "A", "reason": "x"}'

    # clock() 调用顺序：start=0 → pre-match 预算检查(0)未超 → _play_match 内部不调 clock
    # → post-match 预算复查(200) 超出 → melted
    clock_values = iter([0.0, 0.0, 200.0])

    def clock():
        return next(clock_values)

    cards = _cards()  # 2 支票 → 1 对
    res = pk.run_pk(cards, _scored(), runner, budget_seconds=180.0, clock=clock)
    assert res["status"] == "melted"


def test_reason_action_word_filtered():
    """codex 门2 中：补充仓位动作词(满仓/上车等)也过滤。"""
    def runner(prompt, payload):
        return '{"winner": "A", "reason": "建议满仓上车"}'
    res = pk.run_pk(_cards(), _scored(), runner)
    reasons = [m["reason"] for m in res["matches"] if m["state"] == "valid"]
    assert all("满仓" not in r and "上车" not in r for r in reasons)
