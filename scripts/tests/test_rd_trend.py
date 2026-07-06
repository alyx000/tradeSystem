"""research-digest 行业覆盖趋势（services/research_digest/trend.py）单测。

覆盖：日序列加载（空日鉴别/脏 payload 跳过/行业标注降级）、趋势窗口（有效日取窗、
占比与 Δpp、prior 不足披露、未分类恒尾、脉冲日份额免疫）、回补编排（日历游标/幂等/
失败隔离）、markdown 渲染。
"""
from __future__ import annotations

import datetime
import json

from db.connection import get_connection
from db.migrate import migrate
from services.research_digest import trend
from services.research_digest.collector import UNCLASSIFIED, sw_prefix_index


# ---------- fixtures ----------

PREFIX = sw_prefix_index({
    "600519.SH": {"sw_l1": "食品饮料"},
    "000725.SZ": {"sw_l1": "电子"},
    "601318.SH": {"sw_l1": "非银金融"},
})


def _db(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    migrate(conn)
    return conn


def _insert_payload(conn, biz_date, rows, *, status=None, dedupe=None):
    payload = {"rows": rows}
    conn.execute(
        """INSERT INTO raw_interface_payloads
           (interface_name, provider, stage, biz_date, target_date, raw_table,
            dedupe_key, payload_json, payload_hash, row_count, status, params_json, source_meta_json)
           VALUES (?, 't', 'post_extended', ?, ?, ?, ?, ?, 'h', ?, ?, '{}', '{}')""",
        (trend.INTERFACE_NAME, biz_date, biz_date, f"raw_{trend.INTERFACE_NAME}",
         dedupe or f"k:{trend.INTERFACE_NAME}:{biz_date}", json.dumps(payload, ensure_ascii=False),
         len(rows), status or ("success" if rows else "empty")),
    )
    conn.commit()  # 释放写事务：真实 CLI conn 只读；测试里未提交事务会锁住 IngestService 的第二连接


def _rows(*codes):
    return [{"stock_code": c, "stock_name": "x", "institution": "机构"} for c in codes]


# ---------- load_daily_industry_series ----------

def test_series_labels_industry_and_counts(tmp_path):
    conn = _db(tmp_path)
    _insert_payload(conn, "2026-07-01", _rows("600519", "600519", "000725"))
    series = trend.load_daily_industry_series(conn, PREFIX, days=30)
    assert series == {"2026-07-01": {"食品饮料": 2, "电子": 1}}


def test_series_skips_empty_day_and_unknown_code_falls_to_unclassified(tmp_path):
    conn = _db(tmp_path)
    _insert_payload(conn, "2026-06-30", _rows("600519"))
    _insert_payload(conn, "2026-07-01", [])          # 合法真空日 status='empty'
    _insert_payload(conn, "2026-07-02", _rows("999999"))  # 无申万成分 → 未分类
    series = trend.load_daily_industry_series(conn, PREFIX, days=30)
    assert "2026-07-01" not in series               # 空日不进有效日
    assert series["2026-07-02"] == {UNCLASSIFIED: 1}


def test_series_skips_corrupt_payload_without_breaking_others(tmp_path):
    conn = _db(tmp_path)
    _insert_payload(conn, "2026-06-30", _rows("600519"))
    conn.execute(
        "UPDATE raw_interface_payloads SET payload_json = 'not-json' WHERE biz_date = '2026-06-30'")
    _insert_payload(conn, "2026-07-01", _rows("000725"))
    series = trend.load_daily_industry_series(conn, PREFIX, days=30)
    assert list(series) == ["2026-07-01"]


def test_series_respects_days_limit_latest_first(tmp_path):
    conn = _db(tmp_path)
    for d in ("2026-06-29", "2026-06-30", "2026-07-01"):
        _insert_payload(conn, d, _rows("600519"))
    series = trend.load_daily_industry_series(conn, PREFIX, days=2)
    assert list(series) == ["2026-06-30", "2026-07-01"]  # LIMIT 取最近，输出仍升序


def test_series_same_day_multirow_takes_latest_and_limit_counts_days(tmp_path):
    # 同一 biz_date 两行（不同 dedupe_key）→ 取 MAX(id) 最新一份；limit 语义=天数不被同日多行吃掉
    conn = _db(tmp_path)
    _insert_payload(conn, "2026-06-30", _rows("600519"))
    conn.execute("UPDATE raw_interface_payloads SET dedupe_key = 'k:other:2026-07-01-a' WHERE biz_date='2026-06-30'")
    conn.execute("UPDATE raw_interface_payloads SET biz_date = '2026-07-01' WHERE biz_date='2026-06-30'")
    _insert_payload(conn, "2026-07-01", _rows("000725", "000725"))   # 同日更新的一行（id 更大）
    _insert_payload(conn, "2026-06-30", _rows("600519"))
    series = trend.load_daily_industry_series(conn, PREFIX, days=2)
    assert list(series) == ["2026-06-30", "2026-07-01"]              # 2 天配额=2 个不同日
    assert series["2026-07-01"] == {"电子": 2}                        # 同日取最新行


def test_series_corrupt_success_day_warns(tmp_path, caplog):
    # status=success 且 row_count>0 但 payload 损坏 = 脏数据剔窗，必须留 warning（区别于合法真空日）
    conn = _db(tmp_path)
    _insert_payload(conn, "2026-06-30", _rows("600519"))
    conn.execute(
        "UPDATE raw_interface_payloads SET payload_json = 'not-json' WHERE biz_date = '2026-06-30'")
    with caplog.at_level("WARNING"):
        series = trend.load_daily_industry_series(conn, PREFIX, days=30)
    assert series == {}
    assert any("payload 损坏" in r.message for r in caplog.records)


# ---------- build_industry_trend ----------

def test_trend_windows_take_valid_days_only(tmp_path):
    # 6 个有效日, recent_n=2 → recent=最后2个, prior=再前2个
    s = {f"2026-06-0{i}": {"电子": 1} for i in range(1, 7)}
    t = trend.build_industry_trend(s, recent_n=2)
    assert t["recent_days"] == ["2026-06-05", "2026-06-06"]
    assert t["prior_days"] == ["2026-06-03", "2026-06-04"]


def test_trend_share_and_delta_pp():
    s = {
        "2026-06-01": {"电子": 2, "食品饮料": 2},   # prior: 电子 50%
        "2026-06-02": {"电子": 6, "食品饮料": 2},   # recent: 电子 75%
    }
    t = trend.build_industry_trend(s, recent_n=1)
    elec = next(it for it in t["items"] if it["industry"] == "电子")
    assert elec["recent_share"] == 75.0
    assert elec["prior_share"] == 50.0
    assert elec["delta_pp"] == 25.0


def test_trend_share_immune_to_pulse_day():
    # 脉冲日总篇数×10，行业结构不变 → 占比 Δpp 应为 0（绝对篇数会误报升温）
    s = {
        "2026-06-01": {"电子": 6, "食品饮料": 4},
        "2026-06-02": {"电子": 60, "食品饮料": 40},
    }
    t = trend.build_industry_trend(s, recent_n=1)
    assert all(it["delta_pp"] == 0.0 for it in t["items"])


def test_trend_prior_insufficient_delta_none():
    t = trend.build_industry_trend({"2026-06-02": {"电子": 3}}, recent_n=5)
    assert t["prior_days"] == [] and t["prior_total"] == 0
    assert t["items"][0]["delta_pp"] is None       # 无对照不伪造 0 变化


def test_trend_unclassified_sorts_last():
    t = trend.build_industry_trend({"2026-06-02": {UNCLASSIFIED: 9, "电子": 1}}, recent_n=1)
    assert [it["industry"] for it in t["items"]] == ["电子", UNCLASSIFIED]


def test_trend_empty_series():
    t = trend.build_industry_trend({}, recent_n=5)
    assert t["recent_days"] == [] and t["items"] == []


# ---------- backfill_missing_payloads ----------

class _FakeRegistry:
    """ensure_trade_calendar 内 registry.call 会被调用；返回失败让其走「以库内日历继续」分支。"""

    def call(self, *_args, **_kwargs):
        class _R:
            success = False
            data = None
        return _R()


class _FakeIngest:
    calls: list[str] = []
    fail_dates: set[str] = set()      # 抛异常（进程内意外错误路径）
    status_fail_dates: set[str] = set()  # 返回 status='failed'（provider 失败不抛异常的真实路径）

    def __init__(self, db_path=None, registry=None):
        pass

    def execute_interface(self, name, target_date, **_kw):
        assert name == trend.INTERFACE_NAME
        if target_date in self.fail_dates:
            raise RuntimeError("boom")
        _FakeIngest.calls.append(target_date)
        if target_date in self.status_fail_dates:
            return {"status": "failed"}
        return {"status": "success"}


def _seed_calendar(conn, dates):
    for d in dates:
        conn.execute("INSERT OR REPLACE INTO trade_calendar(date, is_open) VALUES (?, 1)", (d,))
    conn.commit()


def _recent_days(n):
    today = datetime.date.today()
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(n)][::-1]


def _reset_fake_ingest():
    _FakeIngest.calls, _FakeIngest.fail_dates, _FakeIngest.status_fail_dates = [], set(), set()


def test_backfill_idempotent_and_cursor(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    days = _recent_days(4)          # 4 个「交易日」（测试日历全开）
    _seed_calendar(conn, days)
    _insert_payload(conn, days[-1], _rows("600519"))   # 最近一日已有非空 payload → 完成态跳过
    _reset_fake_ingest()
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 3)
    assert stats == {"done": 2, "skipped": 1, "failed": 0, "scanned": 3}
    assert _FakeIngest.calls == [days[-2], days[-3]]   # 游标新→旧，跳过非空完成日


def test_backfill_refetches_empty_day_for_late_arrival(tmp_path, monkeypatch):
    # cninfo 迟到回填：已落 empty 的日不是完成态，backfill 必须重采让 empty→非空可升级
    # （重采安全性由 preserve_nonempty_on_empty=True 保证）
    conn = _db(tmp_path)
    days = _recent_days(2)
    _seed_calendar(conn, days)
    _insert_payload(conn, days[-1], [])                # empty 首写
    _insert_payload(conn, days[-2], _rows("600519"))   # 非空完成态
    _reset_fake_ingest()
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 2)
    assert _FakeIngest.calls == [days[-1]]             # empty 日被重采，非空日跳过
    assert stats == {"done": 1, "skipped": 1, "failed": 0, "scanned": 2}


def test_backfill_exception_isolated(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    days = _recent_days(3)
    _seed_calendar(conn, days)
    _reset_fake_ingest()
    _FakeIngest.fail_dates = {days[-2]}
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 3)
    assert stats["failed"] == 1 and stats["done"] == 2   # 单日异常不中断其余


def test_backfill_status_failed_counted_as_failed(tmp_path, monkeypatch):
    # provider/网络失败时 execute_interface 返回 status='failed' 而不抛异常，
    # 必须计入 failed 而非 done（否则「回补完成/失败 0」骗人）
    conn = _db(tmp_path)
    days = _recent_days(3)
    _seed_calendar(conn, days)
    _reset_fake_ingest()
    _FakeIngest.status_fail_dates = {days[-1]}
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 3)
    assert stats["failed"] == 1 and stats["done"] == 2


def test_backfill_calendar_gap_stops_early_and_warns(tmp_path, monkeypatch, caplog):
    conn = _db(tmp_path)
    days = _recent_days(2)
    _seed_calendar(conn, days)      # 日历只有 2 天，要 5 天 → scanned=2 且告警留痕
    _reset_fake_ingest()
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    with caplog.at_level("WARNING"):
        stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 5)
    assert stats["scanned"] == 2 and stats["done"] == 2
    assert any("2/5" in r.message for r in caplog.records)   # 截断不静默


def test_backfill_refetches_corrupt_success_day_for_self_heal(tmp_path, monkeypatch):
    # status=success 且 row_count>0 但 payload 损坏：趋势用不了这天，完成态判定必须
    # 与趋势消费同口径（可解析非空行），坏日重采自愈而不是被「已有跳过」永久锁死
    conn = _db(tmp_path)
    days = _recent_days(2)
    _seed_calendar(conn, days)
    _insert_payload(conn, days[-1], _rows("600519"))
    conn.execute(
        "UPDATE raw_interface_payloads SET payload_json = 'not-json' WHERE biz_date = ?", (days[-1],))
    _insert_payload(conn, days[-2], _rows("600519"))
    _reset_fake_ingest()
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 2)
    assert _FakeIngest.calls == [days[-1]]
    assert stats == {"done": 1, "skipped": 1, "failed": 0, "scanned": 2}


def test_backfill_refetches_day_whose_latest_snapshot_is_empty(tmp_path, monkeypatch):
    # 同日多行（不同 dedupe_key）：旧行非空、最新行 empty → 趋势按 MAX(id) 只看到 empty，
    # backfill 不得因「存在过非空行」把该日判为完成态
    conn = _db(tmp_path)
    days = _recent_days(2)
    _seed_calendar(conn, days)
    _insert_payload(conn, days[-1], _rows("600519"))                       # 旧非空
    _insert_payload(conn, days[-1], [], dedupe=f"k:alt:{days[-1]}")        # 新 empty（id 更大）
    _insert_payload(conn, days[-2], _rows("600519"))
    _reset_fake_ingest()
    monkeypatch.setattr("services.ingest_service.IngestService", _FakeIngest)
    stats = trend.backfill_missing_payloads(conn, _FakeRegistry(), 2)
    assert _FakeIngest.calls == [days[-1]]
    assert stats["skipped"] == 1


class _RealPathRegistry:
    """真实 IngestService 集成用：应答 get_research_report_list，其余接口失败降级。"""

    class _P:
        def supports(self, method):
            return method == "get_research_report_list"

    providers = [_P()]

    def call(self, method, *args, **kwargs):
        from providers.base import DataResult
        if method == "get_research_report_list":
            return DataResult(
                data=[{"stock_code": "600519", "stock_name": "x", "institution": "机构"}],
                source="t")
        return DataResult(data=None, source="t", error="unsupported")


def test_backfill_real_ingest_writes_into_same_db(tmp_path):
    # 不 mock IngestService：验证 backfill 用 PRAGMA 解析的 db_path 写进与 conn 同一库，
    # 随后同一 conn 能读到 payload 且趋势把该日算作有效日
    conn = _db(tmp_path)
    days = _recent_days(1)
    _seed_calendar(conn, days)
    stats = trend.backfill_missing_payloads(conn, _RealPathRegistry(), 1)
    assert stats == {"done": 1, "skipped": 0, "failed": 0, "scanned": 1}
    series = trend.load_daily_industry_series(conn, PREFIX, days=5)
    assert series == {days[-1]: {"食品饮料": 1}}


def test_backfill_refetch_supersedes_stale_bad_snapshot(tmp_path):
    # codex 第3轮场景：canonical 行（IngestService dedupe）id 低、坏的 alt 行 id 高。
    # 重采走 ON CONFLICT DO UPDATE 只刷新 canonical 的 inserted_at 不换 id——
    # 快照选择若按 MAX(id) 会永远看到坏行（无限重采不自愈）；按 inserted_at 才能闭环。
    conn = _db(tmp_path)
    days = _recent_days(1)
    _seed_calendar(conn, days)
    reg = _RealPathRegistry()
    assert trend.backfill_missing_payloads(conn, reg, 1)["done"] == 1   # canonical 非空落库
    conn.execute("UPDATE raw_interface_payloads SET inserted_at = datetime('now','-1 hour')")
    conn.commit()
    _insert_payload(conn, days[-1], _rows("600519"), dedupe=f"k:alt:{days[-1]}")  # 高 id alt 行
    conn.execute(
        "UPDATE raw_interface_payloads SET payload_json='not-json', "
        "inserted_at = datetime('now','-1 minute') WHERE dedupe_key = ?",
        (f"k:alt:{days[-1]}",))
    conn.commit()
    assert trend.load_daily_industry_series(conn, PREFIX, days=5) == {}  # 坏行为最新快照→不可用
    stats = trend.backfill_missing_payloads(conn, reg, 1)
    assert stats["done"] == 1                                            # 判不完成→重采
    series = trend.load_daily_industry_series(conn, PREFIX, days=5)
    assert series == {days[-1]: {"食品饮料": 1}}                          # 重采后 canonical 重新成为最新快照


def test_run_trend_report_auto_expands_when_empty_days_eat_quota(tmp_path):
    # 最近 12 天全是合法真空日：默认 days=recent_n×4=20 只装得下 8 个有效日（<2×5），
    # 应自动扩容重读一次，把更早的 10 个有效日全部纳入双窗口
    conn = _db(tmp_path)
    valid_days = [f"2026-05-{d:02d}" for d in range(1, 11)]     # 10 个有效日
    empty_days = [f"2026-06-{d:02d}" for d in range(1, 13)]     # 12 个真空日（更近）
    for d in valid_days:
        _insert_payload(conn, d, _rows("600519"))
    for d in empty_days:
        _insert_payload(conn, d, [])
    result = trend.run_trend_report(conn, _FakeRegistry(), days=None, recent_n=5)
    assert result["recent_days"] == valid_days[-5:]
    assert result["prior_days"] == valid_days[:5]               # 扩容后 prior 窗口完整


# ---------- render_trend_md ----------

def test_render_empty_hint():
    md = trend.render_trend_md(trend.build_industry_trend({}))
    assert "暂无有效采集日" in md and "--backfill" in md


def test_render_table_and_truncation():
    s = {
        "2026-06-01": {f"行业{i}": 1 for i in range(12)},
        "2026-06-02": {f"行业{i}": 1 for i in range(12)},
    }
    md = trend.render_trend_md(trend.build_industry_trend(s, recent_n=1), top_cap=10)
    assert "近1有效日" in md and "| 行业 |" in md
    assert "还有 2 个行业" in md


def test_render_no_prior_window_labelled():
    md = trend.render_trend_md(trend.build_industry_trend({"2026-06-02": {"电子": 3}}, recent_n=5))
    assert "无前窗口对照" in md
    assert "| 电子 | 3 | 100.0 | 0.0 | — |" in md
