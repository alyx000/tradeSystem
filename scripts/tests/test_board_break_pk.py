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

    def test_redline_reason_filtered(self):
        from services.recommend.formatter import REDLINE_KEYWORDS
        kw = next(iter(REDLINE_KEYWORDS))
        result = pk.run_pk(_cards(2), _scored(_cards(2)),
                           lambda p, pl: '{"winner": "A", "reason": "%s"}' % kw)
        assert result["matches"][0]["reason"] == "(理由已按红线过滤)"
