"""scripts/tests/test_board_break_scorer.py"""
import os

import pytest
from services.board_break import constants as C, scanner, scorer


class TestClassify:
    @pytest.mark.parametrize("title,cat", [
        ("关于股东减持计划的公告", "reduce"),
        ("关于股东终止减持计划的公告", "negate"),       # 否定优先于减持
        ("关于控股股东增持股份的公告", "increase"),
        ("关于回购注销部分股份的公告", "negate"),       # 否定优先于增持
        ("2026年度向特定对象发行股票预案", "placement"),
        ("2026年半年度业绩预告", "earnings"),
        ("关于中标某项目的公告", "good"),
        ("关于中标候选人公示的公告", "neutral"),        # 排除词
        ("关于收到警示函的公告", "bad"),
        ("日常关联交易公告", "neutral"),
    ])
    def test_priority(self, title, cat):
        assert scorer.classify_announcement(title) == cat


def _card(**over):
    base = {"code": "600002", "name": "x", "limit_times": 2, "pct_chg": 3.0, "close": 10.0,
            "industry": "计算机", "in_main_sector": True, "main_sector_status": "ok",
            "ann_status": "ok", "ann_events": {"increase": [], "placement": [], "reduce": [], "good": [], "bad": []},
            "holder_source": "announcement",
            "earnings_status": "no_event", "earnings_type": None,
            "gain10": 10.0, "gain10_status": "ok",
            "dif": 0.5, "dif_status": "ok",
            "position_value": 0.5, "position_state": "full"}
    base.update(over)
    return base


class TestHolderEventTitle:
    def test_readable_title(self):
        """增减持事件标题人可读：方向翻译+万股化（真跑校准修）。"""
        row = {"holder_name": "深圳兆伟", "in_de": "DE", "change_vol": 6375000}
        assert scorer._holder_event_title(row, "reduce") == "深圳兆伟 减持 637.5万股"

    def test_missing_vol(self):
        assert "股数未知" in scorer._holder_event_title({"holder_name": "某", "in_de": "IN"}, "increase")


class TestScore:
    def test_main_sector_scored_with_evidence(self):
        r = scorer.score_candidate(_card())
        ev = {e["dimension"]: e for e in r["evidences"]}
        assert ev["main_sector"]["score"] == C.W_MAIN_SECTOR
        assert "计算机" in ev["main_sector"]["detail"]

    def test_main_sector_not_in_top5(self):
        card = _card(in_main_sector=False)
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["main_sector"]["score"] == 0.0
        assert ev["main_sector"]["status"] == "ok"

    def test_main_sector_missing(self):
        card = _card(main_sector_status="missing")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["main_sector"]["score"] == 0.0
        assert ev["main_sector"]["status"] == "missing"

    @pytest.mark.parametrize("pos,expect", [
        (0.29, C.W_REDUCE_LOW), (0.30, C.W_REDUCE_LOW), (0.31, 0.0),
        (0.69, 0.0), (0.70, C.W_REDUCE_HIGH), (0.71, C.W_REDUCE_HIGH)])
    def test_reduce_polarity_by_position(self, pos, expect):
        card = _card(position_value=pos,
                     ann_events={"increase": [], "placement": [], "good": [], "bad": [],
                                 "reduce": [{"date": "0628", "title": "减持公告"}]})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["reduce"]["score"] == expect

    def test_reduce_degraded_position_still_scored_with_sample_note(self):
        # D 修复：degraded（120-249根）不应被静默当 full 打分丢样本量信息，
        # 但打分口径与 full 一致（同三档阈值），detail 追加样本不足提示
        card = _card(position_value=0.8, position_state="degraded", position_bar_count=180,
                     ann_events={"increase": [], "placement": [], "good": [], "bad": [],
                                 "reduce": [{"date": "2026-06-28", "title": "减持公告"}]})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["reduce"]["score"] == C.W_REDUCE_HIGH
        assert "样本不足250" in ev["reduce"]["detail"]

    def test_position_missing_reduce_neutral(self):
        card = _card(position_state="missing", position_value=None,
                     ann_events={"increase": [], "placement": [], "good": [], "bad": [],
                                 "reduce": [{"date": "0628", "title": "减持公告"}]})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["reduce"]["score"] == 0.0 and "位置缺失" in ev["reduce"]["detail"]

    def test_reduce_no_event(self):
        ev = {e["dimension"]: e for e in scorer.score_candidate(_card())["evidences"]}
        assert ev["reduce"]["score"] == 0.0
        assert ev["reduce"]["status"] == "no_event"

    @pytest.mark.parametrize("g,expect", [(24.99, 0.0), (25.0, C.W_GAIN_MID), (40.0, C.W_GAIN_HIGH)])
    def test_gain_bands(self, g, expect):
        ev = {e["dimension"]: e for e in scorer.score_candidate(_card(gain10=g))["evidences"]}
        assert ev["gain10"]["score"] == expect

    def test_gain_missing(self):
        card = _card(gain10=None, gain10_status="missing")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["gain10"]["score"] == 0.0 and ev["gain10"]["status"] == "missing"

    def test_macd_below_axis_zero_but_ok(self):
        card = _card(dif=-0.3, dif_status="ok")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["macd"]["score"] == 0.0
        assert ev["macd"]["status"] == "ok"

    def test_macd_above_axis(self):
        card = _card(dif=0.3, dif_status="ok")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["macd"]["score"] == C.W_MACD_UP

    def test_same_category_counted_once(self):
        card = _card(ann_events={"increase": [], "placement": [], "good": [], "bad": [],
                                 "reduce": [{"date": "0626", "title": "a"}, {"date": "0627", "title": "b"}]},
                     position_value=0.8, position_state="full")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["reduce"]["score"] == C.W_REDUCE_HIGH  # 两条减持仍只 -2

    def test_increase_event_scored(self):
        card = _card(ann_events={"increase": [{"date": "0620", "title": "增持公告"}],
                                 "placement": [], "reduce": [], "good": [], "bad": []})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["increase"]["score"] == C.W_INCREASE
        assert ev["increase"]["status"] == "ok"

    def test_placement_event_scored(self):
        card = _card(ann_events={"increase": [], "reduce": [], "good": [], "bad": [],
                                 "placement": [{"date": "0615", "title": "定增预案"}]})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["placement"]["score"] == C.W_PLACEMENT

    def test_announce_good_and_bad_additive(self):
        card = _card(ann_events={"increase": [], "reduce": [], "placement": [],
                                 "good": [{"date": "0610", "title": "中标合同"}],
                                 "bad": [{"date": "0611", "title": "收到警示函"}]})
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["announce"]["score"] == pytest.approx(C.W_ANN_GOOD + C.W_ANN_BAD)

    def test_earnings_good(self):
        # earnings_direction 由 build_fact_card._earnings_direction 唯一推导来源，
        # 直接构造 fixture 的测试须显式给出（score_candidate 不再重复推导，避免两处口径漂移）
        card = _card(earnings_type="预增", earnings_status="ok", earnings_direction="good")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["earnings"]["score"] == C.W_EARN_GOOD

    def test_earnings_bad(self):
        card = _card(earnings_type="预减", earnings_status="ok", earnings_direction="bad")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["earnings"]["score"] == C.W_EARN_BAD

    def test_earnings_no_event(self):
        ev = {e["dimension"]: e for e in scorer.score_candidate(_card())["evidences"]}
        assert ev["earnings"]["score"] == 0.0 and ev["earnings"]["status"] == "no_event"

    def test_earnings_neutral_type_none_detail_direction_unknown(self):
        # neutral（已选中报告期记录但方向未知/type 空）不得被 no_event 分支吞掉：
        # status 须为 neutral，detail 须显式标「方向未知」，与"无披露"语义区分
        card = _card(earnings_status="neutral", earnings_type=None, earnings_direction=None)
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["earnings"]["status"] == "neutral"
        assert "方向未知" in ev["earnings"]["detail"]

    def test_gain10_defensive_double_check_status_ok_but_value_none(self):
        # 防御性双查：score_candidate 是纯函数容错契约，不假设输入必经 build_fact_card
        # （如未来回放/反序列化路径喂入不一致卡片）；status="ok" 但 value=None 须按
        # missing 降级，而非对 None 直接算术抛 TypeError 拖垮整批打分
        card = _card(gain10=None, gain10_status="ok")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["gain10"]["score"] == 0.0
        assert ev["gain10"]["status"] == "missing"

    def test_macd_defensive_double_check_status_ok_but_value_none(self):
        card = _card(dif=None, dif_status="ok")
        ev = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert ev["macd"]["score"] == 0.0
        assert ev["macd"]["status"] == "missing"

    def test_adj_factor_failed_three_dims_missing(self):
        # D12 前提：复权因子失败 → gain10/MACD/position 三维度整体 missing，不得用未复权价硬算
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": [{"trade_date": "20260704", "close": 10.0,
                                                      "low": 9.8, "high": 10.2}] * 130},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=None)  # adj_factors=None 模拟因子源失败
        assert card["gain10_status"] == "missing"
        assert card["dif_status"] == "missing"
        assert card["position_state"] == "missing"

    def test_macd_last_bar_not_T_missing(self):
        bars = [{"trade_date": f"202601{i:02d}", "close": 10.0, "low": 9.8, "high": 10.2}
                for i in range(1, 29)] * 5  # 末根 trade_date != T
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=[{"trade_date": b["trade_date"], "adj_factor": 1.0} for b in bars])
        assert card["dif_status"] == "missing"

    def test_zero_score_semantics_distinguishable(self):
        card = _card(ann_status="source_failed", dif=None, dif_status="missing")
        evs = {e["dimension"]: e for e in scorer.score_candidate(card)["evidences"]}
        assert evs["announce"]["status"] == "source_failed"
        assert evs["macd"]["status"] == "missing"
        assert all(e["detail"] for e in scorer.score_candidate(card)["evidences"])  # detail 永不为空


class _FakeResult:
    def __init__(self, data=None, error=""):
        self.data = data
        self.error = error

    @property
    def success(self):
        return self.error == ""


class TestNormDate:
    """`_norm_date` falsy-0 修复：`value or ""` 会把数字 0（合法但假值）误当空串。"""

    def test_zero_not_treated_as_empty(self):
        assert scorer._norm_date(0) == "0"

    def test_none_returns_empty(self):
        assert scorer._norm_date(None) == ""

    def test_compact_date_still_normalized(self):
        assert scorer._norm_date("20260628") == "2026-06-28"


class TestBuildFactCard:
    def _bars_ok(self, n=130, close=10.0, date="2026-07-04"):
        # n-1 根历史 + 末根=T 日，全部同值（免打分方向干扰）
        out = [{"trade_date": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
                "close": close, "low": close * 0.98, "high": close * 1.02} for i in range(n - 1)]
        out.append({"trade_date": date, "close": close, "low": close * 0.98, "high": close * 1.02})
        return out

    def _factors_for(self, bars):
        return [{"trade_date": b["trade_date"], "adj_factor": 1.0} for b in bars]

    def test_main_sector_membership_true(self):
        bars = self._bars_ok()
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors={"计算机", "电子"}, ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["in_main_sector"] is True
        assert card["main_sector_status"] == "ok"

    def test_ref_price_transparently_passed_through(self):
        """门1 硬 bug 修复：6% 参考位（scanner 层算好的 ref_price）须原样透传进事实卡，
        否则渲染层候选表该列会恒显 "—"（scanner 算了但 scorer 没接住）。"""
        bars = self._bars_ok()
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "ref_price": 10.6, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["ref_price"] == 10.6

    def test_ann_result_classifies_events(self):
        bars = self._bars_ok()
        ann_result = _FakeResult(data=[
            {"title": "关于股东减持计划的公告", "ann_date": "20260628", "url": ""},
            {"title": "关于中标某项目的公告", "ann_date": "20260610", "url": ""},
        ])
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=ann_result, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["ann_status"] == "ok"
        assert len(card["ann_events"]["reduce"]) == 1
        assert len(card["ann_events"]["good"]) == 1
        assert card["holder_source"] == "announcement"  # holder_result 不可用回退公告分类

    def test_holder_result_overrides_announcement(self):
        bars = self._bars_ok()
        ann_result = _FakeResult(data=[])
        holder_result = _FakeResult(data=[
            {"ann_date": "20260628", "holder_name": "张三", "holder_type": "高管",
             "in_de": "DE", "change_vol": 100000},
            {"ann_date": "20260601", "holder_name": "李四", "holder_type": "股东",
             "in_de": "IN", "change_vol": 50000},
        ])
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=ann_result, holder_result=holder_result,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["holder_source"] == "stk_holdertrade"
        assert len(card["ann_events"]["reduce"]) == 1
        assert len(card["ann_events"]["increase"]) == 1

    def test_earnings_rows_none_is_source_failed(self):
        bars = self._bars_ok()
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=None, adj_factors=self._factors_for(bars))
        assert card["earnings_status"] == "source_failed"

    def test_earnings_rows_forecast_positive(self):
        bars = self._bars_ok()
        earnings_rows = [{"ts_code": "600002.SH", "end_date": "20260630", "ann_date": "20260620",
                          "type": "预增", "kind": "forecast"}]
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=earnings_rows, adj_factors=self._factors_for(bars))
        assert card["earnings_status"] == "ok"
        assert card["earnings_type"] == "预增"
        assert card["earnings_direction"] == "good"

    def test_earnings_express_priority_over_forecast_same_period(self):
        bars = self._bars_ok()
        earnings_rows = [
            {"ts_code": "600002.SH", "end_date": "20260630", "ann_date": "20260610",
             "type": "预增", "kind": "forecast"},
            {"ts_code": "600002.SH", "end_date": "20260630", "ann_date": "20260625",
             "yoy_dedu_np": -12.5, "kind": "express"},
        ]
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=earnings_rows, adj_factors=self._factors_for(bars))
        # 同报告期 express 优先于 forecast，express yoy 为负 → 方向 bad（即便 forecast 预告是"预增"）
        assert card["earnings_direction"] == "bad"

    def test_event_dates_normalized_and_formatted_mmdd_in_detail(self):
        # D13：tushare holder_trade（YYYYMMDD）与 akshare 公告（YYYY-MM-DD）两种日期格式
        # 均须在 build_fact_card 归一为 YYYY-MM-DD，score_candidate 依据明细按「MM-DD《标题》」拼接
        bars = self._bars_ok()
        ann_result = _FakeResult(data=[
            {"title": "关于中标某项目的公告", "ann_date": "2026-06-10", "url": ""},  # akshare 格式
        ])
        holder_result = _FakeResult(data=[
            {"ann_date": "20260615", "holder_name": "张三", "holder_type": "股东",
             "in_de": "IN", "change_vol": 50000},   # tushare 格式：增持
            {"ann_date": "20260628", "holder_name": "李四", "holder_type": "股东",
             "in_de": "DE", "change_vol": 20000},   # tushare 格式：减持
        ])
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=ann_result, holder_result=holder_result,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["ann_events"]["increase"][0]["date"] == "2026-06-15"
        assert card["ann_events"]["reduce"][0]["date"] == "2026-06-28"

        scored = scorer.score_candidate(card)
        ev = {e["dimension"]: e for e in scored["evidences"]}
        assert "06-15" in ev["increase"]["detail"]
        assert "06-28" in ev["reduce"]["detail"]
        assert "06-10" in ev["announce"]["detail"]  # good 事件来自 ann_result（akshare 格式日期）

    def test_ann_titles_capped_5_and_40_chars(self):
        bars = self._bars_ok()
        long_title = "关于" + "中" * 60 + "标某重大项目中标的公告"
        ann_result = _FakeResult(data=[{"title": long_title, "ann_date": "20260610", "url": ""}] * 8)
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=ann_result, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert len(card["ann_titles"]) == C.FACT_CARD_ANN_MAX
        assert all(len(t) <= C.FACT_CARD_ANN_CHARS for t in card["ann_titles"])


    def test_bias_fields_present_with_sufficient_bars(self):
        """长周期乖离展示项：样本充足（130根同值）→ bias60/bias120 = 0%。"""
        bars = self._bars_ok()
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["bias60"] == pytest.approx(0.0)
        assert card["bias120"] == pytest.approx(0.0)
        # 成品文案在 scorer 层拼好（渲染层原样使用契约）
        assert card["bias_detail"] == (
            "vs 60日线(≈13周) +0.0% / vs 120日线(≈24周) +0.0%；展示项不计分")

    def test_bias_none_when_qfq_fails(self):
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": [], "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=None)
        assert card["bias60"] is None and card["bias120"] is None
        assert card["bias_detail"] == "缺失（前复权失败/样本不足/末根非T日）；展示项不计分"

    def test_bias_partial_long_window_missing(self):
        """次新股真实可达路径：60<=样本<120 → 短窗口有值、长窗口标"样本不足"（角B-2 补测）。"""
        bars = self._bars_ok(n=80)
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["bias60"] == pytest.approx(0.0)
        assert card["bias120"] is None
        assert "vs 120日线(≈24周) —（样本不足）" in card["bias_detail"]

    def test_bias_none_when_last_bar_not_t(self):
        """末根非 T 日 → 与 dif 同口径整体缺失（现价口径不可信，不硬算）。"""
        bars = self._bars_ok()
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机",
             "close": 10.0, "pct_chg": 3.0, "bars": bars, "date": "2026-07-05"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=[], adj_factors=self._factors_for(bars))
        assert card["bias60"] is None and card["bias120"] is None


class TestScoreAll:
    def test_sorted_desc_with_rank(self):
        cards = [_card(code="600001", gain10=0.0), _card(code="600002", gain10=45.0)]
        ranked = scorer.score_all(cards)
        assert ranked[0]["code"] == "600001"  # 无涨幅过高扣分，总分更高
        assert ranked[0]["rank_score"] == 1
        assert ranked[1]["rank_score"] == 2
        assert ranked[0]["total"] >= ranked[1]["total"]


class _OrchestrationFakeResult:
    """build_fact_cards 编排层测试专用 DataResult-like fake（与 TestBuildFactCard 上方
    的 _FakeResult 语义一致，独立命名避免跨测试类误用）。"""
    def __init__(self, data=None, error=""):
        self.data = data
        self.error = error

    @property
    def success(self):
        return self.error == ""


class _RecordingRegistry:
    """记录每次 call 的方法名+参数供窗口/env 断言用；除显式覆盖的业绩两接口外，
    其余方法一律返回空结果（不影响主线板块/复权因子相关断言）。"""

    def __init__(self, *, forecast=None, express=None):
        self.calls: list[tuple[str, tuple]] = []
        self._forecast = forecast if forecast is not None else _OrchestrationFakeResult(data=[])
        self._express = express if express is not None else _OrchestrationFakeResult(data=[])

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == "get_earnings_forecast":
            return self._forecast
        if name == "get_earnings_express":
            return self._express
        return _OrchestrationFakeResult(data=[])


def _orch_bars(date="2026-07-04", n=130, close=10.0):
    out = [{"trade_date": f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}",
            "close": close, "low": close * 0.98, "high": close * 1.02} for i in range(n - 1)]
    out.append({"trade_date": date, "close": close, "low": close * 0.98, "high": close * 1.02})
    return out


def _orch_result(**over):
    base = {
        "date": "2026-07-04",
        "candidates": [{"code": "600002", "name": "x", "industry": "计算机", "bars": _orch_bars()}],
        "main_sectors": ["计算机"],
        "main_sector_degraded": False,
    }
    base.update(over)
    return base


class TestMainSectorDegradedWiring:
    """口径调和：main_sector_degraded=True（当日集中度快照缺失、scanner 已回退最近一日）
    时，回退集合**照常参与打分**——对齐 trend_leader 既有口径「回退值照用+显式标注」，
    而非像此前那样把 main_sectors 整体清空为 None、白白丢弃一份可用的回退结论。
    只有回退集合本身也是空的（scanner 也找不到任何历史快照）才真正判该维度缺失。
    卡片额外透传 main_sector_degraded 字段，供渲染层加脚注。"""

    def test_degraded_flag_scores_normally_with_detail_annotation(self):
        registry = _RecordingRegistry()
        result = _orch_result(main_sector_degraded=True)  # 回退数据带 main_sectors=["计算机"]
        cards = scorer.build_fact_cards(conn=None, registry=registry, result=result)
        assert cards[0]["main_sector_status"] == "ok"
        assert cards[0]["main_sector_degraded"] is True
        ev = {e["dimension"]: e for e in scorer.score_candidate(cards[0])["evidences"]}
        assert ev["main_sector"]["status"] == "ok"
        assert "当日集中度缺失" in ev["main_sector"]["detail"]

    def test_degraded_flag_with_empty_fallback_set_marks_missing(self):
        # scanner 回退后仍是空集合（连历史快照都没有）→ 无值可用，才真正判缺失
        registry = _RecordingRegistry()
        result = _orch_result(main_sector_degraded=True, main_sectors=[])
        cards = scorer.build_fact_cards(conn=None, registry=registry, result=result)
        assert cards[0]["main_sector_status"] == "missing"
        ev = {e["dimension"]: e for e in scorer.score_candidate(cards[0])["evidences"]}
        assert ev["main_sector"]["status"] == "missing"

    def test_non_degraded_flag_uses_main_sectors_normally(self):
        registry = _RecordingRegistry()
        result = _orch_result(main_sector_degraded=False)
        cards = scorer.build_fact_cards(conn=None, registry=registry, result=result)
        assert cards[0]["main_sector_status"] == "ok"
        assert cards[0]["in_main_sector"] is True
        assert cards[0]["main_sector_degraded"] is False
        ev = {e["dimension"]: e for e in scorer.score_candidate(cards[0])["evidences"]}
        assert ev["main_sector"]["status"] == "ok"
        assert "当日集中度缺失" not in ev["main_sector"]["detail"]


class TestFetchEarningsRows:
    """_fetch_earnings_rows 的 env 覆盖/还原 + 双源失败契约（参考 test_trend_leader_scanner.py
    的 FakeRegistry 模式，自建轻量 fake 记录/还原 EARNINGS_LOOKBACK_DAYS）。"""

    def test_env_overridden_during_call_no_prior_value(self, monkeypatch):
        monkeypatch.delenv("EARNINGS_LOOKBACK_DAYS", raising=False)
        seen = {}

        class R:
            def call(self, name, date):
                seen[name] = os.environ.get("EARNINGS_LOOKBACK_DAYS")
                return _OrchestrationFakeResult(data=[])

        scorer._fetch_earnings_rows(R(), "2026-07-04")
        assert seen["get_earnings_forecast"] == str(C.EARNINGS_WINDOW_DAYS)
        assert seen["get_earnings_express"] == str(C.EARNINGS_WINDOW_DAYS)
        assert "EARNINGS_LOOKBACK_DAYS" not in os.environ  # 原无值 → 还原为"无"

    def test_env_overridden_during_call_restores_prior_value(self, monkeypatch):
        monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "3")
        seen = {}

        class R:
            def call(self, name, date):
                seen[name] = os.environ.get("EARNINGS_LOOKBACK_DAYS")
                return _OrchestrationFakeResult(data=[])

        scorer._fetch_earnings_rows(R(), "2026-07-04")
        assert seen["get_earnings_forecast"] == str(C.EARNINGS_WINDOW_DAYS)
        assert os.environ["EARNINGS_LOOKBACK_DAYS"] == "3"  # 原有值 → 还原为原值

    def test_env_restored_even_if_call_raises(self, monkeypatch):
        monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "3")

        class R:
            def call(self, name, date):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            scorer._fetch_earnings_rows(R(), "2026-07-04")
        assert os.environ["EARNINGS_LOOKBACK_DAYS"] == "3"  # finally 兜底还原，不因异常泄漏

    def test_both_forecast_and_express_failed_returns_none(self):
        class R:
            def call(self, name, date):
                return _OrchestrationFakeResult(error="boom")

        assert scorer._fetch_earnings_rows(R(), "2026-07-04") is None

    def test_earnings_rows_none_propagates_to_source_failed_dimension(self):
        """两路取数失败 → earnings_rows is None → build_fact_card 业绩维度 status=="source_failed"。"""
        card = scorer.build_fact_card(
            {"code": "600002", "name": "x", "industry": "计算机", "close": 10.0, "pct_chg": 3.0,
             "bars": _orch_bars(), "date": "2026-07-04"},
            main_sectors=set(), ann_result=None, holder_result=None,
            earnings_rows=None, adj_factors=None)
        assert card["earnings_status"] == "source_failed"


class TestBuildFactCardsWindowParams:
    """逐候选调用 get_stock_announcements/get_holder_trade/get_stock_adj_factor_range
    时窗口起点参数须分别对齐 ANNOUNCE_WINDOW_DAYS(30) 与 LOOKBACK_NATURAL_DAYS(400)。"""

    def test_per_candidate_calls_use_expected_window_starts(self):
        registry = _RecordingRegistry()
        result = _orch_result()
        scorer.build_fact_cards(conn=None, registry=registry, result=result)

        expected_ann_start = scanner._window_start("2026-07-04", C.ANNOUNCE_WINDOW_DAYS)
        expected_adj_start = scanner._window_start("2026-07-04", C.LOOKBACK_NATURAL_DAYS)
        calls = {name: args for name, args in registry.calls}
        assert calls["get_stock_announcements"] == ("600002", expected_ann_start, "2026-07-04")
        assert calls["get_holder_trade"] == ("600002", expected_ann_start, "2026-07-04")
        assert calls["get_stock_adj_factor_range"] == ("600002", expected_adj_start, "2026-07-04")
