"""build_trend_payload 单测:供前端集中度趋势图的 API 载荷整形(series / sector_keys / snapshot)。"""
from __future__ import annotations

import sqlite3

from db.schema import init_schema
from services.volume_concentration import repo, service


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _save(conn, date, total, sectors, stocks=None, market_total=None):
    """sectors=[(行业, share_fraction)];share 为占 top_n 的小数。"""
    repo.save_concentration(conn, {
        "date": date, "top_n": 20, "total_amount_billion": total,
        "market_total_billion": market_total,
        "stocks": stocks or [],
        "sector_summary": [{"industry": i, "count": 1, "amount_billion": total * sh,
                            "share_in_top_n": sh, "codes": []} for i, sh in sectors],
        "source": {"industry_coverage": 1.0},
    })


def test_series_and_cr3_and_market_share():
    """series 逐日:cr3(排未分类,round1)/ 占两市% / date_short;sector_keys 排未分类含其他。"""
    conn = _conn()
    _save(conn, "2026-05-28", 4000.0,
          [("半导体", 0.32), ("通信", 0.25), ("电池", 0.17), ("未分类", 0.26)], market_total=40000.0)
    _save(conn, "2026-05-29", 3680.0,
          [("半导体", 0.30), ("通信", 0.25), ("电池", 0.18), ("未分类", 0.27)], market_total=40000.0)

    p = service.build_trend_payload(conn, days=30)

    assert p["requested_days"] == 30
    assert [s["date"] for s in p["series"]] == ["2026-05-28", "2026-05-29"]
    s1 = p["series"][1]
    assert s1["date_short"] == "05-29"
    assert s1["cr3"] == 73.0            # 30+25+18(排未分类)
    assert s1["market_share_pct"] == 9.2   # 3680/40000*100
    assert "未分类" not in p["sector_keys"]
    assert {"半导体", "通信", "电池"}.issubset(set(p["sector_keys"]))
    assert "其他" in p["sector_keys"]       # 有未分类残量 → 其他
    assert set(s1["sectors"].keys()) == set(p["sector_keys"])  # 每日补齐所有键
    assert s1["sectors"]["其他"] == 27.0    # 100-(30+25+18)=27(此处即未分类)
    assert round(sum(s1["sectors"].values()), 1) == 100.0


def test_top8_truncation_lumps_tail_into_other():
    """>8 行业 → 取前8 + 其他(尾部并入);并列按行业名升序稳定。"""
    conn = _conn()
    sectors = [(f"行业{i}", 0.1) for i in range(10)]  # 10 个各 10%,无未分类
    _save(conn, "2026-05-28", 1000.0, sectors, market_total=10000.0)
    _save(conn, "2026-05-29", 1000.0, sectors, market_total=10000.0)

    p = service.build_trend_payload(conn, days=30)

    non_other = [k for k in p["sector_keys"] if k != "其他"]
    assert len(non_other) == 8
    assert non_other == [f"行业{i}" for i in range(8)]   # 并列按行业名升序,锁定前8选取+顺序
    assert "其他" in p["sector_keys"]
    assert p["series"][1]["sectors"]["其他"] == 20.0   # 剩余2个行业=20%


def test_no_other_key_when_full_coverage():
    """≤8 行业且无未分类/无尾部 → 不出「其他」键。"""
    conn = _conn()
    _save(conn, "2026-05-28", 1000.0, [("半导体", 0.6), ("电池", 0.4)], market_total=10000.0)
    _save(conn, "2026-05-29", 1000.0, [("半导体", 0.6), ("电池", 0.4)], market_total=10000.0)

    p = service.build_trend_payload(conn, days=30)

    assert "其他" not in p["sector_keys"]
    assert set(p["sector_keys"]) == {"半导体", "电池"}


def test_other_bucket_clamped_non_negative():
    """某日 top8 占满 100%(浮点累加 overshoot)→ 其他=0.0,非 -0.0/负值(真实库实测兜底)。"""
    conn = _conn()
    _save(conn, "2026-05-28", 1000.0, [("半导体", 0.5), ("未分类", 0.5)], market_total=10000.0)  # 触发其他键
    _save(conn, "2026-05-29", 1000.0, [          # 8 行业占满,浮点和=100.00000000000001
        ("半导体", 0.293), ("通信设备", 0.239), ("元件", 0.20), ("消费电子", 0.106),
        ("电池", 0.045), ("自动化设备", 0.034), ("光伏设备", 0.034), ("光学光电子", 0.049),
    ], market_total=10000.0)

    p = service.build_trend_payload(conn, days=30)

    assert "其他" in p["sector_keys"]
    other = p["series"][1]["sectors"]["其他"]
    assert str(other) == "0.0"   # 非 "-0.0"(负零)、非负值


def test_market_share_none_without_market_total():
    """缺 market_total → 该日 market_share_pct=None(前端 connectNulls 兜底)。"""
    conn = _conn()
    _save(conn, "2026-05-28", 1000.0, [("电池", 1.0)], market_total=None)
    _save(conn, "2026-05-29", 1200.0, [("电池", 1.0)], market_total=None)

    p = service.build_trend_payload(conn, days=30)
    assert p["series"][1]["market_share_pct"] is None


def test_missing_sector_on_some_day_filled_zero():
    """某行业某日缺席 → 该日该键补 0(堆叠对齐,审查中-6)。"""
    conn = _conn()
    _save(conn, "2026-05-28", 1000.0, [("半导体", 0.5), ("电池", 0.5)], market_total=10000.0)
    _save(conn, "2026-05-29", 1000.0, [("半导体", 1.0)], market_total=10000.0)  # 电池缺席

    p = service.build_trend_payload(conn, days=30)

    assert "电池" in p["sector_keys"]
    assert p["series"][0]["sectors"]["电池"] == 50.0
    assert p["series"][1]["sectors"]["电池"] == 0.0   # 补 0


def test_snapshot_rotation_enriched_with_industry_and_change():
    """snapshot.rotation:今日新进带行业+涨跌(镜像钉钉报告),退出仅名称。"""
    conn = _conn()
    _save(conn, "2026-05-28", 1000.0, [("电池", 1.0)],
          stocks=[{"code": "A", "name": "甲", "industry": "电池", "change_pct": 1.0}], market_total=10000.0)
    _save(conn, "2026-05-29", 1000.0, [("通信", 1.0)],
          stocks=[{"code": "B", "name": "乙", "industry": "通信", "change_pct": -3.5}], market_total=10000.0)

    snap = service.build_trend_payload(conn, days=30)["snapshot"]

    assert snap["date"] == "2026-05-29"
    assert snap["rotation"]["new"] == [{"name": "乙", "industry": "通信", "change_pct": -3.5}]
    assert snap["rotation"]["dropped"] == [{"name": "甲"}]


def test_snapshot_retention_streak():
    """snapshot.retention:连续在榜个股(streak≥2)+ 名称/天数。"""
    conn = _conn()
    for d in ["2026-05-27", "2026-05-28", "2026-05-29"]:
        _save(conn, d, 1000.0, [("电池", 1.0)],
              stocks=[{"code": "A", "name": "甲", "industry": "电池", "change_pct": 1.0}], market_total=10000.0)

    snap = service.build_trend_payload(conn, days=30)["snapshot"]
    assert {"name": "甲", "streak": 3} in snap["retention"]


def test_empty_when_no_data():
    """无任何快照 → 空壳(前端据此不渲染面板)。"""
    conn = _conn()
    assert service.build_trend_payload(conn, days=30) == {
        "requested_days": 30, "series": [], "sector_keys": [], "snapshot": None,
    }


def test_snapshot_single_day_no_rotation():
    """仅 1 日:无前一日可比 → rotation 空、retention 空(streak=1 不入),series 仍 1 点。"""
    conn = _conn()
    _save(conn, "2026-05-29", 1000.0, [("电池", 1.0)],
          stocks=[{"code": "A", "name": "甲", "industry": "电池", "change_pct": 1.0}], market_total=10000.0)

    p = service.build_trend_payload(conn, days=30)
    assert len(p["series"]) == 1
    snap = p["snapshot"]
    assert snap["date"] == "2026-05-29"
    assert snap["rotation"] == {"new": [], "dropped": []}
    assert snap["retention"] == []   # streak=1 < 2


def test_snapshot_retention_truncated_at_12():
    """连续在榜个股 >12 → 截断至 12(限制载荷)。"""
    conn = _conn()
    stocks = [{"code": f"S{i}", "name": f"股{i}", "industry": "电池", "change_pct": 0.0} for i in range(15)]
    for d in ["2026-05-28", "2026-05-29"]:   # 15 只各连续 2 日 → streak=2
        _save(conn, d, 1000.0, [("电池", 1.0)], stocks=stocks, market_total=10000.0)

    snap = service.build_trend_payload(conn, days=30)["snapshot"]
    assert len(snap["retention"]) == 12
    assert all(r["streak"] == 2 for r in snap["retention"])


def test_series_cr3_matches_report_formatter():
    """端到端口径锁:series 的 cr3 == 报告 formatter「前3行业 X%」(同排未分类同 round,审查中-4)。"""
    from services.volume_concentration import formatter, trend as trend_mod

    conn = _conn()
    recs = [
        ("2026-05-28", [("半导体", 0.30), ("通信", 0.25), ("电池", 0.10), ("未分类", 0.35)]),
        ("2026-05-29", [("半导体", 0.35), ("通信", 0.20), ("电池", 0.10), ("未分类", 0.35)]),
    ]
    for d, sectors in recs:
        _save(conn, d, 100.0, sectors, market_total=1000.0)

    p = service.build_trend_payload(conn, days=30)
    latest_cr3 = p["series"][-1]["cr3"]

    records = repo.get_recent_concentration(conn, "2026-05-29", 30)
    report = formatter.format_daily_report(records[-1], trend_mod.compute_trend(records))
    assert f"前3行业 {latest_cr3}%" in report   # payload cr3 == 报告 CR3
