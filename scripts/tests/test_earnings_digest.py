"""L2: earnings_digest 服务层测试（normalize / collector / gap_check / renderer / service）。

全程 mock 隔离外网：fake payload + fake 持仓/关注 + fake 行情。
"""
from __future__ import annotations

import json

import pytest

from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.earnings_digest import collector, gap_check, normalize, renderer
from services.earnings_digest.service import run_daily_digest


@pytest.fixture(autouse=True)
def _isolated_report_dir(tmp_path, monkeypatch):
    """把 .pushed 标记 / MD 落盘目录指向临时目录——否则 service 测试会读到真实
    data/reports/earnings-digest/ 下的已推标记，造成跨测试/跨真实运行污染。"""
    monkeypatch.setenv("EARNINGS_DIGEST_REPORT_DIR", str(tmp_path / "reports"))


# ---------- fixtures ----------

def _fc(ts_code="000017.SZ", ann_date="20260611", end_date="20260630",
        type_="预增", np_min=3600.0, np_max=5400.0, p_min=50.0, p_max=80.0,
        update_flag="0", **over):
    row = {
        "ts_code": ts_code, "code": ts_code, "ann_date": ann_date, "end_date": end_date,
        "type": type_, "p_change_min": p_min, "p_change_max": p_max,
        "net_profit_min": np_min, "net_profit_max": np_max,
        "last_parent_net": 2000.0, "first_ann_date": ann_date,
        "summary": "预计:净利润3600-5400", "change_reason": "主业增长",
        "update_flag": update_flag,
    }
    row.update(over)
    return row


def _ex(ts_code="002651.SZ", ann_date="20260611", end_date="20260630",
        n_income=57_545_000.0, yoy=63.0, **over):
    row = {
        "ts_code": ts_code, "code": ts_code, "ann_date": ann_date, "end_date": end_date,
        "revenue": 7.26e8, "n_income": n_income, "yoy_dedu_np": yoy,
        "diluted_eps": 0.06, "diluted_roe": 2.08, "perf_summary": "", "is_audit": 0,
        "update_flag": "0",
    }
    row.update(over)
    return row


def _quote(ts_code, open_px, pre_close, high=None, low=None):
    return {"ts_code": ts_code, "open": open_px, "pre_close": pre_close,
            "high": high if high is not None else max(open_px, pre_close) * 1.01,
            "low": low if low is not None else min(open_px, pre_close) * 0.99,
            "close": open_px}


# ---------- normalize ----------

def test_normalize_forecast_picks_latest_version_and_marks_revision():
    rows = [
        _fc(update_flag="0", type_="预增", ann_date="20260610"),
        _fc(update_flag="0", type_="预减", ann_date="20260612",
            first_ann_date="20260610", np_min=-2000.0, np_max=-1000.0,
            p_min=-60.0, p_max=-40.0),
    ]
    items = normalize.normalize_forecast(rows)
    assert len(items) == 1  # 同 ts_code+end_date 收敛为当前版本
    item = items[0]
    assert item["type"] == "预减"  # ann_date 最新的版本胜出（预增改预减=信号反转）
    assert item["is_revision"] is True
    assert item["p_change_mid"] == -50.0


def test_normalize_forecast_update_flag_dedup_same_ann_date():
    rows = [_fc(update_flag="0"), _fc(update_flag="1", np_min=4000.0, np_max=4000.0)]
    items = normalize.normalize_forecast(rows)
    assert len(items) == 1
    assert items[0]["net_profit_mid_wan"] == 4000.0  # update_flag=1（修正后）胜出


def test_normalize_forecast_growth_trend_needs_earlier_period():
    rows = [
        _fc(end_date="20260331", p_min=10.0, p_max=20.0, ann_date="20260410"),
        _fc(end_date="20260630", p_min=50.0, p_max=80.0, ann_date="20260611"),
    ]
    items = normalize.normalize_forecast(rows)
    by_end = {i["end_date"]: i for i in items}
    assert by_end["20260630"]["growth_trend"] == "加速"  # 65 > 15
    assert by_end["20260331"]["growth_trend"] is None  # 无更早基准


def test_normalize_express_units_and_vs_forecast():
    fc_items = normalize.normalize_forecast([_fc(ts_code="002651.SZ", np_min=5000.0, np_max=6000.0)])
    ex_items = normalize.normalize_express([_ex(n_income=57_545_000.0)], fc_items)
    item = ex_items[0]
    assert item["n_income_wan"] == pytest.approx(5754.5)  # 元 → 万元
    assert item["vs_forecast"] == "落在预告区间上沿"  # 5754.5 ∈ [5000,6000] 上半段


@pytest.mark.parametrize("n_income_yuan,expected", [
    (70_000_000.0, "超出预告上界"),     # 7000万 > 6000万
    (30_000_000.0, "跌破预告下界"),     # 3000万 < 5000万
    (51_000_000.0, "落在预告区间下沿"),  # 5100万 ∈ 下半段
])
def test_vs_forecast_positions(n_income_yuan, expected):
    fc_items = normalize.normalize_forecast([_fc(ts_code="002651.SZ", np_min=5000.0, np_max=6000.0)])
    ex_items = normalize.normalize_express([_ex(n_income=n_income_yuan)], fc_items)
    assert ex_items[0]["vs_forecast"] == expected


def test_normalize_express_without_forecast_has_no_label():
    ex_items = normalize.normalize_express([_ex()], [])
    assert ex_items[0]["vs_forecast"] is None


# ---------- collector：水位线 ----------

def _db(tmp_path):
    db_path = tmp_path / "t.db"
    conn = get_connection(db_path)
    migrate(conn)
    return conn, str(db_path)


def _insert_payload(conn, biz_date, rows, interface="earnings_forecast"):
    """按日 payload 快照（status 按 IngestService 语义：有行 success，无行 empty）。"""
    payload = {"rows": rows}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES (?, 't', 'post_extended', ?, ?, ?, ?, ?, 'h', ?, ?, '{}', '{}')""",
        (interface, biz_date, biz_date, f"raw_{interface}", f"k:{interface}:{biz_date}",
         json.dumps(payload, ensure_ascii=False), len(rows),
         "success" if rows else "empty"),
    )


def test_watermark_is_max_archived_ann_date_excluding_current(tmp_path):
    conn, _ = _db(tmp_path)
    _insert_payload(conn, "2026-06-14", [_fc(ann_date="20260613"), _fc(ts_code="000002.SZ", ann_date="20260614")])
    _insert_payload(conn, "2026-06-15", [_fc(ts_code="000003.SZ", ann_date="20260615")])  # 本次
    wm = collector.get_push_watermark(conn, "earnings_forecast", "2026-06-15")
    assert wm["max_ann_date"] == "20260614"  # 上一份 payload 的最大公告日；排除本次（biz_date < T）
    assert len(wm["seen_keys"]) == 1  # 仅最大公告日（0614）上的业务键


def test_watermark_first_run_returns_none(tmp_path):
    conn, _ = _db(tmp_path)
    assert collector.get_push_watermark(conn, "earnings_forecast", "2026-06-12") is None


def test_watermark_empty_payload_does_not_advance(tmp_path):
    """codex 回归：镜像滞后日 empty payload 不推进水位线——迟到公告次日仍能推出。"""
    conn, _ = _db(tmp_path)
    _insert_payload(conn, "2026-06-12", [_fc(ann_date="20260612")])  # 周五 success
    _insert_payload(conn, "2026-06-14", [])                          # 周日滞后空跑（empty）
    wm = collector.get_push_watermark(conn, "earnings_forecast", "2026-06-15")
    assert wm["max_ann_date"] == "20260612"  # 停在周五内容最大公告日
    rows = [
        _fc(ts_code="000001.SZ", ann_date="20260613"),  # 周六（迟到补出）
        _fc(ts_code="000002.SZ", ann_date="20260614"),  # 周日（迟到补出）
    ]
    assert len(collector.filter_new_since_watermark(rows, wm)) == 2


def test_watermark_premature_same_day_run_does_not_swallow_evening_announcements(tmp_path):
    """门1 回归（凌晨补跑陷阱）：当日早间 run 的 payload 只含历史公告——水位线按
    内容最大公告日推进，当晚才发布的公告不会被「run 日期=当日」吞掉。"""
    conn, _ = _db(tmp_path)
    _insert_payload(conn, "2026-06-12", [_fc(ann_date="20260612")])             # 周五 22:00 正常跑
    _insert_payload(conn, "2026-06-13", [_fc(ann_date="20260612", update_flag="1")])  # 周六凌晨补跑（只有周五公告）
    wm = collector.get_push_watermark(conn, "earnings_forecast", "2026-06-14")  # 周日跑
    assert wm["max_ann_date"] == "20260612"  # 按内容推进（run 日期粒度会错推到 0613）
    saturday_rows = [_fc(ts_code="000001.SZ", ann_date="20260613")]  # 周六晚公告
    assert len(collector.filter_new_since_watermark(saturday_rows, wm)) == 1  # 不被吞


def test_watermark_seen_keys_aggregate_across_snapshots(tmp_path):
    """codex 回归（阶段4 v2）：镜像非累积快照——同公告日的行分散在两份前序快照里，
    seen_keys 必须跨快照聚合，两行都不得被当作新行重推。"""
    conn, _ = _db(tmp_path)
    row_a = _fc(ts_code="000001.SZ", ann_date="20260612")
    row_b = _fc(ts_code="000002.SZ", ann_date="20260612")
    _insert_payload(conn, "2026-06-12", [row_a])          # 首跑快照只有 A
    _insert_payload(conn, "2026-06-13", [row_b])          # 次跑快照只有 B（非累积，A 消失）
    wm = collector.get_push_watermark(conn, "earnings_forecast", "2026-06-14")
    assert wm["max_ann_date"] == "20260612"
    assert len(wm["seen_keys"]) == 2                      # A、B 均已见
    assert collector.filter_new_since_watermark([row_a, row_b], wm) == []


def test_filter_same_ann_date_late_arrival_passes():
    """codex 回归（阶段4）：与水位线同公告日、但业务键未见过的迟到行照常放行。

    镜像分批吐出同一天公告：首跑存档了 0612 的 A 票，次跑窗口里多出 0612 的 B 票
    （迟到）与 A 票的修正行——标量「严格大于」会吞掉两者，键感知截断放行。
    """
    seen_row = _fc(ts_code="000001.SZ", ann_date="20260612")
    wm = {"max_ann_date": "20260612",
          "seen_keys": {collector._business_key(seen_row)}}
    rows = [
        seen_row,                                                        # 已推过 → 滤
        _fc(ts_code="000002.SZ", ann_date="20260612"),                   # 同日迟到新票 → 放行
        _fc(ts_code="000001.SZ", ann_date="20260612", update_flag="1"),  # 同日修正行 → 放行
    ]
    new = collector.filter_new_since_watermark(rows, wm)
    assert {(r["ts_code"], r["update_flag"]) for r in new} == {("000002.SZ", "0"), ("000001.SZ", "1")}


def test_filter_sunday_to_monday_no_repush():
    """周日晚推过的周末公告，周一不重复推；周一新公告照推。"""
    rows = [
        _fc(ts_code="000001.SZ", ann_date="20260613"),  # 周六
        _fc(ts_code="000002.SZ", ann_date="20260614"),  # 周日
        _fc(ts_code="000003.SZ", ann_date="20260615"),  # 周一
    ]
    wm = {"max_ann_date": "20260614",
          "seen_keys": {collector._business_key(_fc(ts_code="000002.SZ", ann_date="20260614"))}}
    new = collector.filter_new_since_watermark(rows, wm)
    assert [r["ts_code"] for r in new] == ["000003.SZ"]


def test_filter_first_run_pushes_full_window():
    rows = [_fc(ann_date="20260610"), _fc(ts_code="000002.SZ", ann_date="20260612")]
    assert len(collector.filter_new_since_watermark(rows, None)) == 2


def test_read_payload_rows_between_unions_daily_snapshots(tmp_path):
    """长假/周末场景：跨多天按日 payload union 去重。"""
    conn, _ = _db(tmp_path)
    def _store(biz_date, rows):
        payload = {"rows": rows}
        conn.execute(
            """INSERT INTO raw_interface_payloads
               (interface_name, provider, stage, biz_date, target_date, raw_table,
                dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
               VALUES ('earnings_forecast', 't', 'post_extended', ?, ?, 'raw_earnings_forecast',
                       ?, ?, 'h', ?, 'success', '{}', '{}')""",
            (biz_date, biz_date, f"k:{biz_date}", json.dumps(payload, ensure_ascii=False), len(rows)),
        )
    # 周五窗口 [周三..周五]、周日窗口 [周五..周日]——周五行在两份快照里重复
    _store("2026-06-12", [_fc(ts_code="000001.SZ", ann_date="20260612")])
    _store("2026-06-14", [_fc(ts_code="000001.SZ", ann_date="20260612"),
                          _fc(ts_code="000002.SZ", ann_date="20260614")])
    rows = collector.read_payload_rows_between(conn, "earnings_forecast", "2026-06-12", "2026-06-15")
    assert len(rows) == 2  # union 去重


# ---------- gap_check：市场投票 2×2 ----------

def _gap_case(type_, open_px, pre_close):
    candidates = [_fc(ts_code="600000.SH", ann_date="20260611", type_=type_)]
    today = [_quote("600000.SH", open_px, pre_close)]
    return gap_check.check_gaps(
        candidates, today, [],
        prev_trade_date="2026-06-11", target_date="2026-06-12",
    )


@pytest.mark.parametrize("type_,open_px,pre_close,expected_label", [
    ("预增", 10.5, 10.0, "✅超预期确认"),
    ("预增", 9.5, 10.0, "⚠️利好不及预期"),
    ("预减", 10.5, 10.0, "💡利空出尽"),
    ("首亏", 9.5, 10.0, "❌暴雷确认"),
])
def test_gap_vote_quadrants(type_, open_px, pre_close, expected_label):
    hits = _gap_case(type_, open_px, pre_close)
    assert len(hits) == 1
    assert hits[0]["vote_label"] == expected_label


def test_gap_below_threshold_not_hit():
    hits = _gap_case("预增", 10.1, 10.0)  # +1% < 2%
    assert hits == []


def test_gap_exact_threshold_hits():
    hits = _gap_case("预增", 10.2, 10.0)  # 恰好 +2.0% —— >= 阈值即命中
    assert len(hits) == 1
    assert hits[0]["gap_pct"] == 2.0


def test_gap_dirty_negative_price_skipped():
    """脏数据（负价/零价）跳过，不产生荒唐缺口。"""
    candidates = [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")]
    today = [{"ts_code": "600000.SH", "open": 2.5, "pre_close": -5.0,
              "high": 2.6, "low": 2.4, "close": 2.5}]
    hits = gap_check.check_gaps(
        candidates, today, [],
        prev_trade_date="2026-06-11", target_date="2026-06-12",
    )
    assert hits == []


def test_gap_candidate_window_is_next_trade_day_semantics():
    """周一验证：候选=周五晚+周末公告（ann_date ∈ [周五, 周一)），周一公告不进。"""
    candidates = [
        _fc(ts_code="600001.SH", ann_date="20260612", type_="预增"),  # 周五 ✓
        _fc(ts_code="600002.SH", ann_date="20260614", type_="预增"),  # 周日 ✓
        _fc(ts_code="600003.SH", ann_date="20260615", type_="预增"),  # 周一（次日=周二）✗
        _fc(ts_code="600004.SH", ann_date="20260611", type_="预增"),  # 周四（已验证过）✗
    ]
    today = [_quote(f"60000{i}.SH", 10.5, 10.0) for i in range(1, 5)]
    hits = gap_check.check_gaps(
        candidates, today, [],
        prev_trade_date="2026-06-12", target_date="2026-06-15",
    )
    assert {h["ts_code"] for h in hits} == {"600001.SH", "600002.SH"}


def test_gap_suspended_stock_skipped():
    candidates = [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")]
    hits = gap_check.check_gaps(
        candidates, [], [],  # 当日无该票行情（停牌）
        prev_trade_date="2026-06-11", target_date="2026-06-12",
    )
    assert hits == []


def test_gap_strict_and_one_word_board():
    candidates = [
        _fc(ts_code="600001.SH", ann_date="20260611", type_="预增"),
        _fc(ts_code="600002.SH", ann_date="20260611", type_="预增"),
    ]
    today = [
        _quote("600001.SH", 11.0, 10.0, high=11.5, low=10.8),   # 严格缺口：今低10.8 > 昨高10.2
        _quote("600002.SH", 11.0, 10.0, high=11.0, low=11.0),   # 一字板
    ]
    prev = [_quote("600001.SH", 10.0, 10.0, high=10.2, low=9.8)]
    hits = gap_check.check_gaps(
        candidates, today, prev,
        prev_trade_date="2026-06-11", target_date="2026-06-12",
    )
    by_code = {h["ts_code"]: h for h in hits}
    assert by_code["600001.SH"]["strict_gap"] is True
    assert by_code["600002.SH"]["one_word_board"] is True


# ---------- renderer ----------

def _codes(holdings=(), watchlist=()):
    return {"holdings": set(holdings), "watchlist": set(watchlist)}


def test_render_empty_day_returns_none():
    assert renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=[], position_codes=_codes(),
    ) is None


def test_render_hit_section_and_threshold_filter():
    items = normalize.normalize_forecast([
        _fc(ts_code="000017.SZ", np_min=3600.0, np_max=5400.0),       # 中值4500万 < 5000万 不进Top
        _fc(ts_code="600519.SH", np_min=6_000_000.0, np_max=7_000_000.0,  # 中值65亿 进Top
            p_min=60.0, p_max=80.0),
    ])
    result = renderer.render_digest(
        target_date="2026-06-12", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes(holdings={"000017"}),
    )
    assert result is not None
    _, md = result
    assert "① 持仓/关注命中" in md and "〔持仓〕" in md and "000017.SZ" in md
    assert "⑤ 预增 Top" in md
    assert md.index("600519.SH") > md.index("⑤")  # Top 榜含大票
    top_section = md[md.index("⑤"):]
    assert "000017.SZ" not in top_section  # 低基数小票被阈值挡在 Top 榜外


def test_render_industry_top5_and_counts():
    items = normalize.normalize_forecast([
        _fc(ts_code="000001.SZ"), _fc(ts_code="000002.SZ", type_="扭亏"),
    ])
    industry_map = {"000001.SZ": {"sw_l1": "银行"}, "000002.SZ": {"sw_l1": "房地产"}}
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes(), industry_map=industry_map,
    )
    assert "③ 预告集中行业 Top5" in md and "银行 1家" in md
    assert "④ 新增公告计数" in md and "📈预增 1" in md and "🔄扭亏 1" in md


def test_render_gap_section_with_double_flag():
    hits = gap_check.check_gaps(
        [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")],
        [_quote("600000.SH", 10.5, 10.0)], [],
        prev_trade_date="2026-06-11", target_date="2026-06-12",
    )
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=hits, position_codes=_codes(watchlist={"600000"}),
    )
    assert "② 预告次日缺口验证" in md
    assert "✅超预期确认" in md and "〔关注〕" in md  # 缺口+关注双标


def test_render_gap_section_truncated_with_footer():
    """缺口段超显示上限 → 截断 + 尾注计数（不静默）。"""
    hits = [
        {"ts_code": f"60{i:04d}.SH", "type": "预增", "vote_label": "✅超预期确认",
         "gap_pct": 5.0, "strict_gap": False, "one_word_board": False}
        for i in range(renderer.GAP_DISPLAY_CAP + 5)
    ]
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=hits, position_codes=_codes(),
    )
    assert md.count("✅超预期确认") == renderer.GAP_DISPLAY_CAP
    assert "另有 5 条缺口命中" in md


def test_render_express_section_with_unaudited_note():
    ex_items = normalize.normalize_express([_ex(ts_code="002651.SZ")], [])
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=ex_items,
        gap_hits=[], position_codes=_codes(holdings={"002651"}),
    )
    assert "快报" in md and "（未审计）" in md and "归母同比 +63.0%" in md


# ---------- service 编排（fake registry + 真 SQLite） ----------

class _FakeRegistry:
    """按方法名返回预置结果；日期相关方法（行情/交易日）在类内分支，参数化构造。"""

    def __init__(self, results: dict[str, DataResult], *,
                 quotes_by_date: dict[str, list] | None = None,
                 trade_days: set[str] | None = None):
        self._results = results
        self._quotes_by_date = quotes_by_date or {}
        self._trade_days = trade_days or set()
        self.providers = [self]

    def supports(self, method_name: str) -> bool:
        return method_name in self._results

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        if method_name == "is_trade_day":
            return DataResult(data=args[0] in self._trade_days, source="fake")
        if method_name == "get_market_daily_quotes":
            return DataResult(data=self._quotes_by_date.get(args[0], []), source="fake")
        return self._results.get(
            method_name, DataResult(data=None, source="fake", error="unsupported"))


def _service_registry(fc_rows, ex_rows, *, quotes=None, trade_days=None):
    return _FakeRegistry(
        {
            "get_earnings_forecast": DataResult(data=fc_rows, source="tushare:forecast_vip"),
            "get_earnings_express": DataResult(data=ex_rows, source="tushare:express_vip"),
            "get_stock_sw_industry_map": DataResult(data={}, source="fake"),
        },
        quotes_by_date=quotes,
        trade_days=trade_days,
    )


def test_service_end_to_end_archive_filter_render(tmp_path):
    conn, db_path = _db(tmp_path)
    # seed 持仓：000017 命中 → ① 段展开（非命中小票按设计只进计数段）
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('000017', '深中华A', 'active')"
    )
    conn.commit()
    conn.close()
    registry = _service_registry(
        [_fc(ts_code="000017.SZ", ann_date="20260612")],
        [],
        quotes={"2026-06-12": []},
        trade_days={"2026-06-11"},
    )
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path, input_by="test")
    assert result.has_content
    assert "000017.SZ" in result.markdown and "〔持仓〕" in result.markdown
    assert result.stats["forecast_new"] == 1
    # 权威取数已落库
    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_interface_payloads WHERE interface_name='earnings_forecast'"
    ).fetchone()["c"]
    conn.close()
    assert count == 1


def test_service_second_run_same_content_no_repush(tmp_path):
    """连续两天跑：第二天无新公告 → 水位线过滤后空 → 不推送。"""
    _, db_path = _db(tmp_path)
    rows = [_fc(ts_code="000017.SZ", ann_date="20260611")]
    registry = _service_registry(rows, [], trade_days={"2026-06-10", "2026-06-11"})
    first = run_daily_digest(registry, "2026-06-11", db_path=db_path)
    assert first.has_content
    second = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert not second.has_content  # ann_date=0611 ≤ 水位线 0611，不重推


def test_service_express_vs_forecast_uses_archived_history(tmp_path):
    """口径二① 集成：快报对标的预告区间来自历史存档（非仅本批新增）。

    模拟真实时序：预告数周前已落库（不在本批新增窗口），快报今日到达——
    vs_forecast 仍须命中区间标签。
    """
    conn, db_path = _db(tmp_path)
    # 历史预告（5月中旬落库，net 5000~6000 万）
    payload = {"rows": [_fc(ts_code="002651.SZ", ann_date="20260515",
                            np_min=5000.0, np_max=6000.0)]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-05-15', '2026-05-15',
                   'raw_earnings_forecast', 'k:0515', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('002651', 'X', 'active')"
    )
    conn.commit()
    conn.close()
    registry = _service_registry(
        [],  # 今日无新预告
        [_ex(ts_code="002651.SZ", ann_date="20260612", n_income=57_545_000.0)],
        trade_days={"2026-06-11"},
    )
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.has_content
    assert "落在预告区间上沿" in result.markdown  # 5754.5 ∈ [5000,6000] 上半段


def test_service_industry_map_failure_degrades_gracefully(tmp_path):
    """行业映射接口失败 → 跳过 ③ 段但照常推送。"""
    _, db_path = _db(tmp_path)
    registry = _service_registry(
        [_fc(ts_code="000017.SZ", ann_date="20260612")], [],
        trade_days={"2026-06-11"},
    )
    registry._results["get_stock_sw_industry_map"] = DataResult(
        data=None, source="fake", error="mirror down")
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.has_content
    assert "③" not in result.markdown  # 行业段缺席
    assert "④ 新增公告计数" in result.markdown  # 其余段照常


def test_service_quote_failure_on_trading_day_is_visible(tmp_path):
    """codex 回归：交易日行情故障 ≠ 非交易日——缺口候选存在时必须可见警示。"""
    conn, db_path = _db(tmp_path)
    # 昨日落库的预告 payload（构成今日缺口候选）
    payload = {"rows": [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-06-11', '2026-06-11',
                   'raw_earnings_forecast', 'k:0611', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()

    class _QuoteFailRegistry(_FakeRegistry):
        def call(self, method_name, *args, **kwargs):
            if method_name == "get_market_daily_quotes":
                return DataResult(data=None, source="fake", error="mirror down")
            return super().call(method_name, *args, **kwargs)

    registry = _QuoteFailRegistry(
        {
            "get_earnings_forecast": DataResult(data=[], source="tushare:forecast_vip"),
            "get_earnings_express": DataResult(data=[], source="tushare:express_vip"),
            "get_stock_sw_industry_map": DataResult(data={}, source="fake"),
        },
        trade_days={"2026-06-11"},
    )
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.stats["gap_error"] is not None
    assert result.has_content  # 故障不静默装作干净空日
    assert "行情获取失败" in result.markdown


def test_service_empty_quotes_on_trading_day_is_visible(tmp_path):
    """codex 回归 v2：交易日行情「成功返回空」（镜像滞后不报错）同样须亮警示。"""
    conn, db_path = _db(tmp_path)
    payload = {"rows": [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-06-11', '2026-06-11',
                   'raw_earnings_forecast', 'k:0611', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()
    # target_date 是交易日，但行情成功返回空（quotes_by_date 无该日条目 → []）
    registry = _service_registry([], [], trade_days={"2026-06-11", "2026-06-12"})
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.stats["gap_error"] is not None
    assert "行情返回为空" in result.markdown


def test_service_trade_day_check_failure_defaults_to_visible(tmp_path):
    """codex 回归 v3：交易日鉴别自身故障 → 默认可见警示（只有确证非交易日才静默）。"""
    conn, db_path = _db(tmp_path)
    payload = {"rows": [_fc(ts_code="600000.SH", ann_date="20260611", type_="预增")]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-06-11', '2026-06-11',
                   'raw_earnings_forecast', 'k:0611', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()

    class _CalendarDownRegistry(_FakeRegistry):
        def call(self, method_name, *args, **kwargs):
            if method_name == "is_trade_day" and args[0] == "2026-06-12":
                return DataResult(data=None, source="fake", error="calendar down")
            return super().call(method_name, *args, **kwargs)

    registry = _CalendarDownRegistry(
        {
            "get_earnings_forecast": DataResult(data=[], source="t"),
            "get_earnings_express": DataResult(data=[], source="t"),
            "get_stock_sw_industry_map": DataResult(data={}, source="fake"),
        },
        trade_days={"2026-06-11"},  # prev_trade 查找仍可用
    )
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.stats["gap_error"] is not None
    assert "交易日鉴别失败" in result.markdown


def test_service_stale_archive_not_pushed_as_fresh(tmp_path):
    """门1 回归（误推真实事故 2026-06-13）：当日空窗时，数月前的陈旧存档不得被
    当作"本批"整批推送——本批读取限定近窗快照。"""
    conn, db_path = _db(tmp_path)
    # 数月前的历史回填存档（biz_date 远超近窗）
    _insert_payload(conn, "2026-01-30", [_fc(ann_date="20260130")])
    conn.commit()
    conn.close()
    registry = _service_registry([], [], trade_days={"2026-06-11"})
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert not result.has_content  # 陈旧存档不进本批
    assert result.stats["forecast_new"] == 0


def test_service_empty_day_no_content(tmp_path):
    _, db_path = _db(tmp_path)
    registry = _service_registry([], [], trade_days={"2026-06-11"})
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert not result.has_content
    assert result.stats == {"forecast_new": 0, "express_new": 0, "gap_hits": 0,
                            "gap_error": None, "gap_note": None, "consensus_labeled": 0,
                            "prev_trade_date": "2026-06-11"}


def test_service_consensus_label_on_hit_stock(tmp_path):
    """口径三集成：命中票有券商覆盖+可折算 H1 占比 → 渲染附「vs一致预期 [判断]」。"""
    conn, db_path = _db(tmp_path)
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('600600.SH', 'X', 'active')"
    )
    conn.commit()
    conn.close()

    class _ConsensusRegistry(_FakeRegistry):
        def call(self, method_name, *args, **kwargs):
            if method_name == "get_analyst_forecasts":
                return DataResult(data=[{"quarter": "2026Q4", "np": 10000.0}], source="t")
            if method_name == "get_income_history":
                return DataResult(data=[
                    {"end_date": "20250630", "n_income_attr_p": 5e7, "update_flag": "1"},
                    {"end_date": "20251231", "n_income_attr_p": 1e8, "update_flag": "1"},
                    {"end_date": "20240630", "n_income_attr_p": 5e7, "update_flag": "1"},
                    {"end_date": "20241231", "n_income_attr_p": 1e8, "update_flag": "1"},
                ], source="t")
            return super().call(method_name, *args, **kwargs)

    fc = _fc(ts_code="600600.SH", ann_date="20260612", end_date="20260630",
             np_min=6000.0, np_max=7000.0)  # 中值 6500 vs 隐含 H1 5000 → 超预期
    registry = _ConsensusRegistry(
        {
            "get_earnings_forecast": DataResult(data=[fc], source="t"),
            "get_earnings_express": DataResult(data=[], source="t"),
            "get_stock_sw_industry_map": DataResult(data={}, source="fake"),
        },
        trade_days={"2026-06-11"},
    )
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.has_content
    assert result.stats["consensus_labeled"] == 1
    assert "vs一致预期: 超预期" in result.markdown and "[判断·H1占比折算]" in result.markdown


def test_service_consensus_disabled_skips_labels(tmp_path):
    conn, db_path = _db(tmp_path)
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('600600.SH', 'X', 'active')"
    )
    conn.commit()
    conn.close()
    fc = _fc(ts_code="600600.SH", ann_date="20260612", end_date="20260630")
    registry = _service_registry([fc], [], trade_days={"2026-06-11"})
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path, enable_consensus=False)
    assert result.stats["consensus_labeled"] == 0
    assert "vs一致预期" not in result.markdown


# ---------- #1 已推标记：同日重跑幂等兜底 ----------

def test_marker_roundtrip(tmp_path):
    rd = tmp_path / "reports"
    collector.record_pushed("2026-06-12", {"a", "b"}, {"g1"}, rd)
    loaded = collector.load_pushed("2026-06-12", rd)
    assert loaded["announcements"] == {"a", "b"} and loaded["gaps"] == {"g1"}


def test_marker_missing_returns_empty(tmp_path):
    assert collector.load_pushed("2026-06-12", tmp_path / "nope") == {
        "announcements": set(), "gaps": set(), "warnings": set()}


def test_marker_record_is_union(tmp_path):
    rd = tmp_path / "reports"
    collector.record_pushed("2026-06-12", {"a"}, set(), rd)
    collector.record_pushed("2026-06-12", {"b"}, {"g"}, rd)  # 第二次并入既有
    loaded = collector.load_pushed("2026-06-12", rd)
    assert loaded["announcements"] == {"a", "b"} and loaded["gaps"] == {"g"}


def test_marker_corrupt_file_treated_as_unpushed(tmp_path):
    rd = tmp_path / "reports"
    rd.mkdir()
    (rd / ".pushed-2026-06-12.json").write_text("{bad json", encoding="utf-8")
    assert collector.load_pushed("2026-06-12", rd) == {
        "announcements": set(), "gaps": set(), "warnings": set()}


def test_filter_unpushed_drops_recorded_rows():
    rows = [_fc(ts_code="000001.SZ", ann_date="20260612"),
            _fc(ts_code="000002.SZ", ann_date="20260612")]
    pushed = {collector.announcement_marker_key("earnings_forecast", rows[0])}
    kept = collector.filter_unpushed(rows, "earnings_forecast", pushed)
    assert [r["ts_code"] for r in kept] == ["000002.SZ"]


def test_filter_unpushed_empty_keys_noop():
    rows = [_fc(ts_code="000001.SZ")]
    assert collector.filter_unpushed(rows, "earnings_forecast", set()) == rows


def test_filter_unpushed_gaps_drops_recorded():
    hits = [{"ts_code": "600000.SH", "end_date": "20260630", "ann_date": "20260611"},
            {"ts_code": "600001.SH", "end_date": "20260630", "ann_date": "20260611"}]
    pushed = {collector.gap_marker_key(hits[0])}
    kept = collector.filter_unpushed_gaps(hits, pushed)
    assert [h["ts_code"] for h in kept] == ["600001.SH"]


def test_service_same_target_date_rerun_no_repush(tmp_path):
    """codex 回归：同一 target_date 重跑——首次内容键落标记后重跑过滤为空，不重推
    （水位线设计上排除 target_date，本日幂等靠 .pushed 标记兜底）。"""
    _, db_path = _db(tmp_path)
    rows = [_fc(ts_code="000017.SZ", ann_date="20260612")]
    registry = _service_registry(rows, [], trade_days={"2026-06-11"})
    first = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert first.has_content and first.pushed_announcement_keys
    # 模拟 CLI 推送成功后落标记
    collector.record_pushed("2026-06-12", first.pushed_announcement_keys,
                            first.pushed_gap_keys, renderer.resolve_report_dir())
    second = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert not second.has_content  # 已推内容被标记过滤 → 空 → 不重推


def test_service_same_date_rerun_pushes_only_delta(tmp_path):
    """同日重跑但有新公告到达 → 只推新增，旧的（已落标记）不重推。"""
    conn, db_path = _db(tmp_path)
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('000999', 'X', 'active')"
    )
    conn.commit()
    conn.close()
    registry = _service_registry([_fc(ts_code="000017.SZ", ann_date="20260612")], [],
                                 trade_days={"2026-06-11"})
    first = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    collector.record_pushed("2026-06-12", first.pushed_announcement_keys,
                            first.pushed_gap_keys, renderer.resolve_report_dir())
    # 第二次：同 target_date 新增一只持仓票
    registry._results["get_earnings_forecast"] = DataResult(
        data=[_fc(ts_code="000017.SZ", ann_date="20260612"),
              _fc(ts_code="000999.SZ", ann_date="20260612")], source="fake")
    second = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert second.has_content
    assert "000999.SZ" in second.markdown and "〔持仓〕" in second.markdown
    assert "000017.SZ" not in second.markdown  # 旧票已推不再出现


def test_service_gap_error_same_day_rerun_not_repushed(tmp_path):
    """codex 回归：行情持续故障 + 有候选 + 无新公告 → 首跑推纯 gap_error 告警，
    同日重跑不重推（gap_error 单独会触发推送，故须纳入 warnings 标记去重）。"""
    conn, db_path = _db(tmp_path)
    # 预置「昨日已存档（已推）」的预告：使其成为 gap 候选但不再是今日 forecast_items 新增
    payload = {"rows": [_fc(ts_code="600000.SH", ann_date="20260612", end_date="20260630")]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-06-12', '2026-06-12',
                   'raw_earnings_forecast', 'k:0612', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()
    # 今日无新预告；06-15 为交易日 + 行情返回空 → 触发 gap_error（候选存在）
    registry = _service_registry([], [], quotes={"2026-06-15": []},
                                 trade_days={"2026-06-12", "2026-06-15"})
    first = run_daily_digest(registry, "2026-06-15", db_path=db_path)
    assert first.has_content and first.stats["gap_error"]  # 纯 gap_error 告警触发推送
    assert first.pushed_warning_keys
    collector.record_pushed(
        "2026-06-15", first.pushed_announcement_keys, first.pushed_gap_keys,
        renderer.resolve_report_dir(), warning_keys=first.pushed_warning_keys)
    second = run_daily_digest(registry, "2026-06-15", db_path=db_path)
    assert not second.has_content  # gap_error 已推 → 同日重跑去重 → 空，不刷钉钉


def test_service_gap_error_still_shows_in_later_digest_with_new_content(tmp_path):
    """codex round2 回归：gap_error 已推后，同日晚些到达新公告 + 行情仍故障 →
    新公告速报仍须带「缺口验证缺席」提示（已推标记只抑制单独触发推送，不抹可见性）。"""
    conn, db_path = _db(tmp_path)
    # 0612 已存档预告：作 gap 候选（落在 [prev_trade, target) 窗内）
    payload = {"rows": [_fc(ts_code="600000.SH", ann_date="20260612", end_date="20260630")]}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES ('earnings_forecast', 't', 'post_extended', '2026-06-12', '2026-06-12',
                   'raw_earnings_forecast', 'k:0612', ?, 'h', 1, 'success', '{}', '{}')""",
        (json.dumps(payload, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()
    # 预置 warnings 标记：模拟早些时候已推过该 target 的 gap_error 告警
    collector.record_pushed(
        "2026-06-15", set(), set(), renderer.resolve_report_dir(),
        warning_keys={collector.gap_error_marker_key("2026-06-15")})
    # 今日新到一只预告（ann=06-15）+ 行情仍故障（空 + 交易日）
    registry = _service_registry(
        [_fc(ts_code="000017.SZ", ann_date="20260615", end_date="20260630")], [],
        quotes={"2026-06-15": []}, trade_days={"2026-06-12", "2026-06-15"})
    result = run_daily_digest(registry, "2026-06-15", db_path=db_path)
    assert result.has_content  # 新公告触发推送
    assert "缺口验证本期缺席" in result.markdown  # 故障提示仍在（未被已推标记抹掉）
    assert result.stats["gap_error"]  # stats 仍保留故障信息，未静默变 None


def test_service_no_eligible_candidates_no_false_gap_error(tmp_path):
    """codex round3 回归：当期只有 target 当天新公告（无应今日验证的上一交易日/周末公告），
    行情故障也不应误报「缺口验证本期缺席」（候选判定须用 [prev_trade, target) 窗口）。"""
    _, db_path = _db(tmp_path)
    # 仅今日新公告（ann=06-15，不在 [06-12, 06-15) 验证窗内）；无任何前日候选
    registry = _service_registry(
        [_fc(ts_code="000017.SZ", ann_date="20260615", end_date="20260630")], [],
        quotes={"2026-06-15": []}, trade_days={"2026-06-12", "2026-06-15"})
    result = run_daily_digest(registry, "2026-06-15", db_path=db_path)
    assert result.has_content  # 新公告照常推
    assert "缺口验证本期缺席" not in result.markdown  # 无应验证候选 → 不误报故障
    assert result.stats["gap_error"] is None
    assert not result.pushed_warning_keys  # 不污染 warnings 标记


# ---------- #3 行情完整性提示 ----------

def test_render_gap_note_caveat():
    hits = _gap_case("预增", 10.5, 10.0)
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=hits, position_codes=_codes(),
        gap_note="行情仅 1200 行（疑似截断/部分返回），缺口命中可能不完整",
    )
    assert "疑似截断" in md and "### ② 预告次日缺口验证" in md


def test_render_gap_note_without_hits_still_warns():
    """code-review 回归：行情截断到零命中，但 digest 有其它内容 → ② 仍现截断警示
    （否则截断只进日志不进推送，#3 半失效）。"""
    items = normalize.normalize_forecast([_fc(ts_code="000017.SZ", ann_date="20260612")])
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes(),
        gap_note="行情仅 800 行（疑似截断/部分返回），缺口命中可能不完整",
    )
    assert "疑似截断" in md and "无满足阈值的缺口命中" in md


def test_render_lone_gap_note_does_not_force_push():
    """gap_note 不进 None 判定：无任何其它内容时仍返回 None（同日重跑孤立警示不重推）。"""
    assert renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=[], position_codes=_codes(),
        gap_note="行情仅 800 行（疑似截断/部分返回），缺口命中可能不完整",
    ) is None


# ---------- 股票名字（复用申万成分 map 的 name 注入） ----------

def test_render_forecast_line_includes_name_when_present():
    items = normalize.normalize_forecast([_fc(ts_code="000017.SZ", ann_date="20260612")])
    items[0]["name"] = "深中华A"  # service 注入
    _, md = renderer.render_digest(
        target_date="2026-06-12", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes(holdings={"000017"}))
    assert "000017.SZ 深中华A" in md  # 代码 + 名称


def test_render_code_only_when_name_missing():
    items = normalize.normalize_forecast([_fc(ts_code="000017.SZ", ann_date="20260612")])
    _, md = renderer.render_digest(  # 无 name 字段 → 优雅退化只显代码
        target_date="2026-06-12", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes(holdings={"000017"}))
    assert "000017.SZ" in md


def test_service_injects_names_from_industry_map(tmp_path):
    """集成：service 复用 get_stock_sw_industry_map 的 name 注入各行（数据源不含名字）。"""
    conn, db_path = _db(tmp_path)
    conn.execute(
        "INSERT INTO holdings (stock_code, stock_name, status) VALUES ('000017', 'X', 'active')"
    )
    conn.commit()
    conn.close()
    registry = _service_registry(
        [_fc(ts_code="000017.SZ", ann_date="20260612")], [], trade_days={"2026-06-11"})
    registry._results["get_stock_sw_industry_map"] = DataResult(
        data={"000017.SZ": {"name": "深中华A", "sw_l1": "汽车"}}, source="fake")
    result = run_daily_digest(registry, "2026-06-12", db_path=db_path)
    assert result.has_content
    assert "000017.SZ 深中华A" in result.markdown


# ---------- 标题「公告日」窗（target 截至日 vs 公告实际发布日不混淆） ----------

def test_render_title_includes_announcement_window_range():
    items = normalize.normalize_forecast([
        _fc(ts_code="000017.SZ", ann_date="20260613"),
        _fc(ts_code="000066.SZ", ann_date="20260615"),
    ])
    title, md = renderer.render_digest(
        target_date="2026-06-15", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes())
    assert "（公告日 06-13~06-15）" in title and f"## {title}" in md


def test_render_title_single_day_window():
    items = normalize.normalize_forecast([_fc(ts_code="000017.SZ", ann_date="20260615")])
    title, _ = renderer.render_digest(
        target_date="2026-06-15", forecast_items=items, express_items=[],
        gap_hits=[], position_codes=_codes())
    assert "（公告日 06-15）" in title


def test_render_title_no_window_when_only_gaps():
    """仅缺口命中（旧公告价格跟踪）无本期新公告 → 标题不加公告日窗。"""
    hits = _gap_case("预增", 10.5, 10.0)
    title, _ = renderer.render_digest(
        target_date="2026-06-12", forecast_items=[], express_items=[],
        gap_hits=hits, position_codes=_codes())
    assert title == "业绩预告/快报速报 2026-06-12"
