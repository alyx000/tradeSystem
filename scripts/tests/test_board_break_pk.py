"""scripts/tests/test_board_break_pk.py"""
import itertools
import pytest
from services.board_break import constants as C, pk


def _cards(n):
    return [{"code": f"60000{i}", "name": f"票{i}"} for i in range(n)]


def _scored(cards):
    return [{"code": c["code"], "total": 10.0 - i} for i, c in enumerate(cards)]


class TestParseVerdict:
    def test_valid(self):
        assert pk.parse_verdict('{"winner": "A", "reason": "主线+业绩更强"}')["winner"] == "A"

    def test_wrapped_json_extracted(self):
        assert pk.parse_verdict('好的。{"winner": "B", "reason": "x"} 以上')["winner"] == "B"

    @pytest.mark.parametrize("bad", ['{"winner": "C", "reason": "x"}', '{"reason": "x"}',
                                     '{"winner": "A", "reason": 5}', "不是JSON"])
    def test_invalid(self, bad):
        assert pk.parse_verdict(bad) is None


class TestRunPk:
    def test_round_robin_pairs_and_ranks(self):
        cards, wins_a = _cards(4), '{"winner": "A", "reason": "r"}'
        result = pk.run_pk(cards, _scored(cards), lambda p, pl: wins_a)
        assert result["total"] == 6            # C(4,2)
        assert result["status"] == "ok"
        # A 位=字典序小者 → 600000 全胜
        assert result["wins"]["600000"] == 3 and result["ranks"]["600000"] == 1

    def test_nontimeout_failure_retry_once_then_invalid(self):
        calls = []
        def runner(p, pl):
            calls.append(1)
            return None
        result = pk.run_pk(_cards(2), _scored(_cards(2)), runner)
        assert len(calls) == 2 and result["invalid"] == 1   # 重试 1 次后计无效场

    def test_invalid_ratio_melts(self):
        result = pk.run_pk(_cards(6), _scored(_cards(6)), lambda p, pl: "垃圾输出")
        assert result["status"] == "melted" and result["ranks"] is None

    def test_budget_melts(self):
        t = {"now": 0.0}
        def clock():
            t["now"] += 700.0     # 每场耗时 700s → 第二场前超 1200s 预算
            return t["now"]
        result = pk.run_pk(_cards(4), _scored(_cards(4)),
                           lambda p, pl: '{"winner": "A", "reason": "r"}', clock=clock)
        assert result["status"] == "melted"

    def test_pool_truncated_to_top12(self):
        cards = _cards(15)
        result = pk.run_pk(cards, _scored(cards), lambda p, pl: '{"winner": "A", "reason": "r"}')
        assert result["total"] == 66 and len(result["excluded"]) == 3

    def test_tie_broken_by_score_then_code(self):
        # 构造 2 票循环互胜不可能（单场），用 3 票让 wins 并列后按加权分破平
        cards = _cards(3)
        seq = iter(['{"winner": "A", "reason": "r"}', '{"winner": "B", "reason": "r"}',
                    '{"winner": "B", "reason": "r"}'])
        result = pk.run_pk(cards, _scored(cards), lambda p, pl: next(seq))
        assert list(result["ranks"].values()) == sorted(result["ranks"].values())

    def test_timeout_not_retried(self):
        calls = []
        def runner(p, pl):
            calls.append(1)
            runner.last_diagnostics = {"reason": "timeout"}
            return None
        result = pk.run_pk(_cards(2), _scored(_cards(2)), runner)
        assert len(calls) == 1 and result["invalid"] == 1  # 超时直接计无效场，不重试

    def test_valid_ratio_below_70_no_ranks(self):
        # 有效场 < PK_VALID_RATIO_MIN → ranks=None（与全无效熔断是不同路径：部分有效仍不渲染）
        seq = {"n": 0}
        def runner(p, pl):
            seq["n"] += 1
            runner.last_diagnostics = {"reason": "timeout"}
            return '{"winner": "A", "reason": "r"}' if seq["n"] <= 4 else None
        result = pk.run_pk(_cards(5), _scored(_cards(5)), runner)  # 10 场中 6 场无效 → 40% 有效
        assert result["ranks"] is None

    def test_budget_melt_reports_attempted_not_theoretical(self):
        """预算熔断中途退出：attempted=实际已打场次，valid_ratio 按 attempted 计（审查 Important1）。"""
        t = {"now": 0.0}
        def clock():
            t["now"] += 700.0
            return t["now"]
        result = pk.run_pk(_cards(4), _scored(_cards(4)),
                           lambda p, pl: '{"winner": "A", "reason": "r"}', clock=clock)
        assert result["status"] == "melted"
        assert result["attempted"] < result["total"]  # 只打了部分场次
        assert result["attempted"] == len([m for m in result["matches"]])

    def test_explicit_valid_ratio_min_check(self, monkeypatch):
        """PK_VALID_RATIO_MIN 独立判据：与无效场比上限脱钩时仍能触发熔断（审查 Important2）。"""
        from services.board_break import constants as C
        monkeypatch.setattr(C, "PK_INVALID_RATIO_MAX", 0.99)  # 解除第一判据
        monkeypatch.setattr(C, "PK_VALID_RATIO_MIN", 0.95)    # 收紧第二判据
        seq = {"n": 0}
        def runner(p, pl):
            seq["n"] += 1
            runner.last_diagnostics = {"reason": "timeout"}
            return '{"winner": "A", "reason": "r"}' if seq["n"] > 1 else None
        # 10 场中 1 场超时无效 → 有效率 0.9 < 0.95 → melted
        result = pk.run_pk(_cards(5), _scored(_cards(5)), runner)
        assert result["status"] == "melted" and result["ranks"] is None

    def test_runner_exception_treated_as_failure(self):
        """runner 抛异常 → 收敛为失败场（重试后无效），不打崩整场（门2 S3 R1）。"""
        calls = {"n": 0}
        def runner(p, pl):
            calls["n"] += 1
            raise RuntimeError("boom")
        result = pk.run_pk(_cards(2), _scored(_cards(2)), runner)
        assert result["invalid"] == 1 and calls["n"] == 2  # 重试 1 次后计无效场

    def test_stale_timeout_diag_does_not_block_retry(self):
        """上一场残留 timeout 诊断 + 本场抛异常 → 仍按可重试失败重试 1 次（门2 S3 R2）。"""
        calls = {"n": 0}
        def runner(p, pl):
            calls["n"] += 1
            raise RuntimeError("transport boom")
        runner.last_diagnostics = {"reason": "timeout"}  # 陈旧残留
        result = pk.run_pk(_cards(2), _scored(_cards(2)), runner)
        assert calls["n"] == 2  # 清空诊断后异常按非超时处理 → 重试
        assert result["invalid"] == 1

    def test_duplicate_and_blank_codes_deduped(self):
        """重复码/空码入池前去重去空（门2 S3 R2）：不得自我对局或胜场膨胀。"""
        cards = _cards(3) + [_cards(3)[0], {"code": "", "name": "空"}]
        result = pk.run_pk(cards, _scored(_cards(3)), lambda p, pl: '{"winner": "A", "reason": "r"}')
        assert result["total"] == 3  # C(3,2)，重复与空码不参与
        assert result["status"] == "ok"

    def test_single_pair_over_budget_melts(self):
        """单场即跨预算 → 场后复查熔断，不得 status=ok（门2 S3 R3）。"""
        t = {"now": 0.0}
        def clock():
            v = t["now"]
            t["now"] += 1500.0  # 每次读钟推进 1500s：首查 0s 放行,场后复查已超 1200s
            return v
        result = pk.run_pk(_cards(2), _scored(_cards(2)),
                           lambda p, pl: '{"winner": "A", "reason": "r"}', clock=clock)
        assert result["status"] == "melted" and result["ranks"] is None

    def test_redline_reason_filtered(self, caplog):
        from services.recommend.formatter import REDLINE_KEYWORDS
        kw = next(iter(REDLINE_KEYWORDS))
        with caplog.at_level("WARNING"):
            result = pk.run_pk(_cards(2), _scored(_cards(2)),
                               lambda p, pl: '{"winner": "A", "reason": "%s"}' % kw)
        assert result["matches"][0]["reason"] == "(理由已按红线过滤)"
        assert kw in caplog.text  # 命中词须落日志，便于事后审计

    def test_empty_reason_fallback(self):
        """过滤/截断后为空串 → 兜底占位符，防渲染层空白行。"""
        assert pk._filter_reason("") == "(无理由)"

    def test_card_map_first_wins_on_duplicate_code(self):
        """同 code 两条内容不同的卡：参赛数据用第一条（对齐 _pool_and_excluded 去重顺序）。"""
        cards = [
            {"code": "600000", "name": "第一条"},
            {"code": "600000", "name": "第二条"},
            {"code": "600001", "name": "票1"},
        ]
        captured_payloads = []

        def runner(prompt, payload):
            captured_payloads.append(payload)
            return '{"winner": "A", "reason": "r"}'

        pk.run_pk(cards, _scored(cards[:1] + cards[2:]), runner)
        assert captured_payloads[0]["A"]["name"] == "第一条"

    def test_payload_includes_recognition_facts_but_not_weighted_score(self):
        """PK 独立读取连板高度和近期涨幅辨识度，不读取方式一的加权分。"""
        cards = [
            {"code": "600000", "name": "高辨识度", "limit_times": 4,
             "pct_chg": -4.55, "gain10": 64.7, "gain10_status": "ok"},
            {"code": "600001", "name": "普通辨识度", "limit_times": 2,
             "pct_chg": -7.00, "gain10": 10.0, "gain10_status": "ok"},
        ]
        captured_payloads = []

        def runner(prompt, payload):
            captured_payloads.append(payload)
            return '{"winner": "A", "reason": "连板高度与近期涨幅辨识度更高"}'

        pk.run_pk(cards, _scored(cards), runner)
        high = captured_payloads[0]["A"]
        assert high["limit_times"] == 4
        assert high["pct_chg"] == -4.55
        assert high["gain10"] == 64.7
        assert "total" not in high and "evidences" not in high


class TestUnifiedPkPolicy:
    def test_every_match_receives_same_direction_and_branch_neutral_policy(self):
        cards = [
            dict(code="601188", name="龙江交通", branch="rebreak", limit_times=1,
                 pattern_evidence=dict(prior_limit_times=3), gain10=31.9, gain10_status="ok", pct_chg=-8.9),
            dict(code="002593", name="日上集团", branch="consecutive_break", limit_times=2,
                 gain10=17.6, gain10_status="ok", pct_chg=-9.18),
            dict(code="603725", name="天安新材", branch="consecutive_break", limit_times=2,
                 gain10=24.0, gain10_status="ok", pct_chg=-5.93),
        ]
        calls = []
        def runner(prompt,payload):
            calls.append((prompt,payload))
            return '{"winner":"B","reason":"本场依据所给当日表现判断，分支不预设强弱。"}'
        result=pk.run_pk(cards,_scored(cards),runner)
        assert len(calls)==3 and len({prompt for prompt,_ in calls})==1
        prompt=calls[0][0]
        assert "涨幅更高表示该维度辨识度更高" in prompt
        assert "不得因为涨幅较小" in prompt and "也不得仅因涨幅较大" in prompt
        assert "反包后再断板不天然判弱" in prompt and "不能仅以昨日1板小于2板" in prompt
        assert "哪项独立证据决定胜负" in prompt
        assert result["policy_version"]==C.PK_POLICY_VERSION
        for _,payload in calls:
            for card in payload.values():
                assert "total" not in card and "evidences" not in card
                if card["code"]=="601188":
                    assert card["limit_times"]==1 and card["pattern_evidence"]["prior_limit_times"]==3

    def test_lower_recognition_can_win_with_independent_evidence_and_full_explanation(self):
        import json
        from services.board_break import renderer
        cards=[dict(code="600000",gain10=31.9,branch="rebreak",pct_chg=-8.9),
               dict(code="600001",gain10=24.0,branch="consecutive_break",pct_chg=-5.93)]
        reason=("A近10日涨31.9%，辨识度高于B的24.0%；B当日跌5.93%，较A跌8.90%更小。"
                "本场重视当日表现而选择B，已承认A辨识度优势；不因低涨幅或反包后再断板标签判胜负。")
        assert 60 < len(reason) <= C.PK_REASON_MAX_CHARS
        result=pk.run_pk(cards,_scored(cards),lambda *_:json.dumps(dict(winner="B",reason=reason),ensure_ascii=False))
        assert result["matches"][0]["reason"]==reason
        assert result["wins"]["600001"]==1
        md="\n".join(renderer._pk_summary_lines(result))
        assert reason in md and C.PK_POLICY_VERSION in md and C.PK_POLICY_SUMMARY in md

    def test_historical_result_does_not_claim_new_policy(self):
        from services.board_break import renderer
        old=dict(status="skipped",attempted=0,total=0,invalid=0,valid_ratio=0)
        assert C.PK_POLICY_VERSION not in "\n".join(renderer._pk_summary_lines(old))


class TestBuildLlmRunner:
    """`build_llm_runner`：mock `subprocess.run`（不真调 LLM CLI）。"""

    def test_success_returns_stdout(self, monkeypatch):
        import subprocess as sp

        class _R:
            stdout = '{"winner": "A", "reason": "x"}'
            stderr = ""
            returncode = 0

        monkeypatch.setattr(sp, "run", lambda *a, **k: _R())
        runner = pk.build_llm_runner()
        assert runner("prompt", {"A": {}, "B": {}}) == '{"winner": "A", "reason": "x"}'
        assert runner.last_diagnostics is None

    def test_timeout_returns_none_with_reason(self, monkeypatch):
        import subprocess as sp

        def _raise(*a, **k):
            raise sp.TimeoutExpired(cmd="agy", timeout=180)

        monkeypatch.setattr(sp, "run", _raise)
        runner = pk.build_llm_runner()
        assert runner("prompt", {}) is None
        assert runner.last_diagnostics["reason"] == "timeout"

    def test_oserror_returns_none(self, monkeypatch):
        import subprocess as sp

        def _raise(*a, **k):
            raise FileNotFoundError("agy 不存在")

        monkeypatch.setattr(sp, "run", _raise)
        runner = pk.build_llm_runner()
        assert runner("prompt", {}) is None
        assert runner.last_diagnostics is not None

    def test_nonzero_returncode_returns_none(self, monkeypatch):
        import subprocess as sp

        class _R:
            stdout = ""
            stderr = "boom"
            returncode = 1

        monkeypatch.setattr(sp, "run", lambda *a, **k: _R())
        runner = pk.build_llm_runner()
        assert runner("prompt", {}) is None
        assert runner.last_diagnostics is not None

    def test_empty_stdout_returns_none_with_reason(self, monkeypatch):
        """returncode==0 但 stdout 为空白 → 视为无效场（同 narrator L1 三级 fallback 语义），
        不得当作真实裁决喂给 parse_verdict（空串会被判 None，但诊断原因须显式标注）。"""
        import subprocess as sp

        class _R:
            stdout = "   "
            stderr = ""
            returncode = 0

        monkeypatch.setattr(sp, "run", lambda *a, **k: _R())
        runner = pk.build_llm_runner()
        assert runner("prompt", {}) is None
        assert runner.last_diagnostics["reason"] == "empty_stdout"

    def test_diagnostics_cleared_at_call_start(self, monkeypatch):
        """上一场诊断不得残留到下一场（防误判超时归属）。"""
        import subprocess as sp

        calls = {"n": 0}

        def _run(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sp.TimeoutExpired(cmd="agy", timeout=180)

            class _R:
                stdout = '{"winner": "B", "reason": "y"}'
                stderr = ""
                returncode = 0
            return _R()

        monkeypatch.setattr(sp, "run", _run)
        runner = pk.build_llm_runner()
        assert runner("p", {}) is None
        assert runner.last_diagnostics["reason"] == "timeout"
        assert runner("p", {}) == '{"winner": "B", "reason": "y"}'
        assert runner.last_diagnostics is None
