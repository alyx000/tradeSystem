"""L2: 交易认知服务测试（方案 §5、§七 / Phase 1b）。

覆盖：
- 认知 CRUD（新增校验、列表过滤、详情聚合、精炼版本号、弃用 tag）
- 实例写入（触发器、NULL 漏洞兜底）、验证（fact_source 校验、confidence 计算）
- 周期复盘（聚合、唯一约束、确认状态流转）
所有用例使用 tmp_path 真 SQLite + migrate()，不依赖外网。
"""
from __future__ import annotations

import json

import pytest

from db.connection import get_connection, get_db
from db.migrate import migrate
from services.cognition_service import CognitionService


# ──────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────
@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "cognition_service.db"
    conn = get_connection(p)
    migrate(conn)
    conn.close()
    return str(p)


@pytest.fixture
def service(db_path):
    return CognitionService(db_path)


def _seed_teacher_notes(db_path: str, note_ids: list[int]) -> None:
    """为需要非空 source_note_id 的用例预先写入 teacher_notes（满足 FK）。"""
    if not note_ids:
        return
    with get_db(db_path) as conn:
        migrate(conn)
        for nid in note_ids:
            conn.execute(
                "INSERT OR IGNORE INTO teacher_notes (id, date, title, input_by) "
                "VALUES (?, ?, ?, ?)",
                (int(nid), "2026-04-01", f"seed-note-{nid}", "pytest"),
            )


def _seed_teachers(db_path: str, teachers: list[tuple[int, str]]) -> None:
    """为 teacher_id FK 预写入 teachers 行。"""
    if not teachers:
        return
    with get_db(db_path) as conn:
        migrate(conn)
        for tid, name in teachers:
            conn.execute(
                "INSERT OR IGNORE INTO teachers (id, name) VALUES (?, ?)",
                (int(tid), name),
            )


def _seed_daily_market(db_path: str, dates: list[str]) -> None:
    """为 outcome_fact_source 查表校验预写入 daily_market.date 行。"""
    if not dates:
        return
    with get_db(db_path) as conn:
        migrate(conn)
        for d in dates:
            conn.execute("INSERT OR IGNORE INTO daily_market (date) VALUES (?)", (d,))


def _seed_market_fact_snapshots(db_path: str, rows: list[dict]) -> None:
    """为 outcome_fact_source 查表校验预写入 market_fact_snapshots 行。"""
    if not rows:
        return
    with get_db(db_path) as conn:
        migrate(conn)
        for r in rows:
            conn.execute(
                "INSERT OR IGNORE INTO market_fact_snapshots "
                "(snapshot_id, biz_date, fact_type, subject_type, subject_code, "
                "subject_name, facts_json, source_interfaces_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["snapshot_id"],
                    r["biz_date"],
                    r.get("fact_type", "index_close"),
                    r.get("subject_type", "index"),
                    r.get("subject_code"),
                    r.get("subject_name"),
                    r.get("facts_json", "{}"),
                    r.get("source_interfaces_json", "[]"),
                ),
            )


def _make_cognition(service: CognitionService, **overrides) -> dict:
    payload = {
        "category": "signal",
        "title": "尾盘加速→次日冲高",
        "description": "盘中跌后自然回升并持续加速至收盘，次日惯性冲高",
        "evidence_level": "hypothesis",
        "input_by": "pytest",
    }
    payload.update(overrides)
    return service.add_cognition(**payload)


def _make_instance(service: CognitionService, cognition_id: str, **overrides) -> dict:
    payload = {
        "cognition_id": cognition_id,
        "observed_date": "2026-04-14",
        "source_type": "teacher_note",
        "input_by": "pytest",
    }
    payload.update(overrides)
    return service.add_instance(**payload)


# ──────────────────────────────────────────────────────────────
# 1. 认知：新增 / 列表 / 详情 / 精炼 / 弃用
# ──────────────────────────────────────────────────────────────
def test_add_cognition_defaults(service):
    """category/title/description 必填；默认 status=candidate、version=1；返回 cognition_id。"""
    cog = _make_cognition(service)
    assert cog["cognition_id"].startswith("cog_")
    assert cog["status"] == "candidate"
    assert cog["version"] == 1
    assert cog["category"] == "signal"
    assert cog["evidence_level"] == "hypothesis"
    assert cog["instance_count"] == 0
    # 查询聚合栏目存在但都是零
    assert cog["instances_stats"] == {"total": 0, "by_outcome": {}}


def test_add_cognition_invalid_evidence_level(service):
    """非法 evidence_level 抛 ValueError。"""
    with pytest.raises(ValueError, match="evidence_level"):
        _make_cognition(service, evidence_level="unknown_level")


def test_add_cognition_invalid_json_field(service):
    """conditions_json 非合法 JSON 抛 ValueError。"""
    with pytest.raises(ValueError, match="conditions_json"):
        _make_cognition(service, conditions_json="{not json")


def test_add_cognition_rejects_unknown_category(service):
    """category 不在 taxonomy 枚举 → ValueError。"""
    with pytest.raises(ValueError, match="category"):
        _make_cognition(service, category="phantom_category")


def test_add_cognition_rejects_empty_input_by(service):
    """写入入口 input_by 非空校验（§七 约定）。"""
    with pytest.raises(ValueError, match="input_by"):
        _make_cognition(service, input_by="")


def test_list_cognitions_filter_by_status(service):
    """status 过滤只返回匹配项。"""
    _make_cognition(service, title="候选A")  # status=candidate（默认）
    _make_cognition(service, title="激活A", status="active")
    candidates = service.list_cognitions(status="candidate")
    active_only = service.list_cognitions(status="active")
    assert all(c["status"] == "candidate" for c in candidates)
    assert len(candidates) == 1 and candidates[0]["title"] == "候选A"
    assert len(active_only) == 1 and active_only[0]["title"] == "激活A"


def test_list_cognitions_keyword_escape(service):
    """关键词含 SQL LIKE 通配符时按字面匹配，不触发通配。"""
    _make_cognition(service, title="percent%sign", description="含百分号")
    _make_cognition(service, title="aaaa", description="通配混淆 bbbb")
    # 纯 % 关键词期望只匹配真正含 % 的那条
    rows = service.list_cognitions(keyword="%")
    assert len(rows) == 1
    assert rows[0]["title"] == "percent%sign"
    # underscore 同理
    _make_cognition(service, title="has_underscore", description="含下划线")
    _make_cognition(service, title="nouscoreXX", description="无")
    rows2 = service.list_cognitions(keyword="_")
    assert all("_" in r["title"] or "_" in (r["description"] or "") for r in rows2)


def test_get_cognition_with_instance_stats(service):
    """get_cognition 返回认知 + 实例 outcome 分桶。"""
    cog = _make_cognition(service)
    _make_instance(service, cog["cognition_id"], observed_date="2026-04-10")
    _make_instance(service, cog["cognition_id"], observed_date="2026-04-11")
    _make_instance(
        service, cog["cognition_id"], observed_date="2026-04-12",
        outcome="validated", outcome_fact_source="daily_market:2026-04-13",
    )
    detail = service.get_cognition(cog["cognition_id"])
    assert detail["instance_count"] == 3
    stats = detail["instances_stats"]
    assert stats["total"] == 3
    assert stats["by_outcome"].get("pending") == 2
    assert stats["by_outcome"].get("validated") == 1


def test_get_cognition_not_found(service):
    """不存在 → KeyError。"""
    with pytest.raises(KeyError):
        service.get_cognition("cog_not_exist")


def test_refine_cognition_increments_version(service):
    """每次精炼 version+=1；仅更新非 None 字段。"""
    cog = _make_cognition(service)
    assert cog["version"] == 1

    refined1 = service.refine_cognition(
        cog["cognition_id"],
        input_by="pytest",
        description="更新后的描述",
        evidence_level="principle",
    )
    assert refined1["version"] == 2
    assert refined1["description"] == "更新后的描述"
    assert refined1["evidence_level"] == "principle"
    # 其他字段保持不变
    assert refined1["title"] == cog["title"]

    refined2 = service.refine_cognition(
        cog["cognition_id"], input_by="pytest", pattern="当X时，Y→Z"
    )
    assert refined2["version"] == 3
    assert refined2["pattern"] == "当X时，Y→Z"


def test_deprecate_cognition(service):
    """deprecate 把 status 置 deprecated 并把原因追加到 tags。"""
    cog = _make_cognition(service, tags=["original_tag"])
    deprecated = service.deprecate_cognition(
        cog["cognition_id"], reason="经多次实例否定", input_by="pytest"
    )
    assert deprecated["status"] == "deprecated"
    tags = json.loads(deprecated["tags"])
    assert "original_tag" in tags
    assert any("经多次实例否定" in t for t in tags if "deprecated_reason" in t)


# ──────────────────────────────────────────────────────────────
# 2. 实例：写入（含触发器）/ NULL 唯一性漏洞 / 验证
# ──────────────────────────────────────────────────────────────
def test_add_instance_triggers_parent_counts(service):
    """写入 2 条 instance 后父表 instance_count=2（触发器维护）。"""
    cog = _make_cognition(service)
    _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    _make_instance(service, cog["cognition_id"], observed_date="2026-04-15")

    parent = service.get_cognition(cog["cognition_id"])
    assert parent["instance_count"] == 2
    assert parent["validated_count"] == 0
    assert parent["invalidated_count"] == 0
    # 有效样本 < 3，confidence 固定 0.5（方案 §4.1 触发器约定）
    assert parent["confidence"] == 0.5


def test_add_instance_records_structured_viewpoint_factor_and_hypothesis(service):
    """老师观点实例保存事实/判断拆分、因子快照与可证伪假设。"""
    cog = _make_cognition(service)
    inst = _make_instance(
        service,
        cog["cognition_id"],
        observed_date="2026-04-14",
        viewpoint_claims_json=[
            {"label": "fact", "text": "两市成交额较昨日放大"},
            {"label": "judgement", "text": "资金回流同一主线低位分支"},
        ],
        factor_snapshot_json={
            "factor": "theme_internal_rotation",
            "direction": "low_branch_absorption",
        },
        hypothesis_json={
            "statement": "高位分支退潮但主线未破坏时，低位分支更容易承接回流",
            "invalidation": ["核心中军同步破位", "主线成交额连续衰减"],
            "validation_window": "next_3_trading_days",
        },
    )

    claims = json.loads(inst["viewpoint_claims_json"])
    assert claims[0] == {"label": "fact", "text": "两市成交额较昨日放大"}
    assert claims[1]["label"] == "judgement"
    factor = json.loads(inst["factor_snapshot_json"])
    assert factor["factor"] == "theme_internal_rotation"
    hypothesis = json.loads(inst["hypothesis_json"])
    assert hypothesis["validation_window"] == "next_3_trading_days"


def test_add_instance_rejects_unknown_viewpoint_claim_label(service):
    """事实/判断拆分必须使用白名单标签，防止把判断伪装成事实。"""
    cog = _make_cognition(service)
    with pytest.raises(ValueError, match="viewpoint_claims_json"):
        _make_instance(
            service,
            cog["cognition_id"],
            observed_date="2026-04-14",
            viewpoint_claims_json=[{"label": "certain_fact", "text": "错误标签"}],
        )


def test_add_instance_unknown_cognition_raises(service):
    """父认知不存在 → ValueError。"""
    with pytest.raises(ValueError, match="cognition not found"):
        service.add_instance(
            cognition_id="cog_missing",
            observed_date="2026-04-14",
            source_type="teacher_note",
            input_by="pytest",
        )


def test_add_instance_duplicate_with_null_note_raises(service):
    """source_note_id=None 时 UNIQUE 对 NULL 失效，service 层应手工 existence check。

    方案 §4.2 明确要求：Schema 侧不依赖 DB 约束兜底，service 写入前先查重。
    """
    cog = _make_cognition(service)
    _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    with pytest.raises(ValueError, match="instance_exists"):
        _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")


def test_add_instance_duplicate_with_note_id_raises(service, db_path):
    """source_note_id 非 NULL 重复写入也须被 service 拦截（早于 DB UNIQUE）。"""
    _seed_teacher_notes(db_path, [42])
    cog = _make_cognition(service)
    _make_instance(
        service, cog["cognition_id"], observed_date="2026-04-14", source_note_id=42
    )
    with pytest.raises(ValueError, match="instance_exists"):
        _make_instance(
            service, cog["cognition_id"], observed_date="2026-04-14", source_note_id=42
        )


def test_add_instance_validated_requires_fact_source(service):
    """add_instance 时若 outcome=validated 必须带 outcome_fact_source。"""
    cog = _make_cognition(service)
    with pytest.raises(ValueError, match="outcome_fact_source"):
        _make_instance(
            service, cog["cognition_id"],
            observed_date="2026-04-14", outcome="validated",
        )


def test_validate_instance_requires_fact_source(service):
    """validate_instance 缺 outcome_fact_source 抛 ValueError。"""
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    with pytest.raises(ValueError, match="outcome_fact_source"):
        service.validate_instance(
            inst["instance_id"],
            outcome="validated",
            outcome_fact_source="",
            input_by="pytest",
        )


def test_validate_instance_rejects_invalid_fact_source_format(service):
    """outcome_fact_source 格式非法（缺日期段）→ ValueError。"""
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    with pytest.raises(ValueError, match="outcome_fact_source"):
        service.validate_instance(
            inst["instance_id"],
            outcome="validated",
            outcome_fact_source="daily_market",  # 缺 :YYYY-MM-DD
            input_by="pytest",
        )


def test_validate_instance_updates_confidence(service, db_path):
    """5 validated + 3 invalidated → parent.confidence=0.625（方案 §4.1）。"""
    # 新查表校验要求事实源日期在 daily_market 中存在
    _seed_daily_market(
        db_path, [f"2026-04-{(i % 28) + 1:02d}" for i in range(8)]
    )
    cog = _make_cognition(service)
    instance_ids: list[str] = []
    for i in range(5):
        inst = _make_instance(
            service, cog["cognition_id"], observed_date=f"2026-03-{i + 1:02d}"
        )
        instance_ids.append(inst["instance_id"])
    for i in range(3):
        inst = _make_instance(
            service, cog["cognition_id"], observed_date=f"2026-03-{10 + i:02d}"
        )
        instance_ids.append(inst["instance_id"])

    # 前 5 条 validated，后 3 条 invalidated
    for i, iid in enumerate(instance_ids):
        outcome = "validated" if i < 5 else "invalidated"
        service.validate_instance(
            iid,
            outcome=outcome,
            outcome_fact_source=f"daily_market:2026-04-{(i % 28) + 1:02d}",
            input_by="pytest",
        )

    parent = service.get_cognition(cog["cognition_id"])
    assert parent["validated_count"] == 5
    assert parent["invalidated_count"] == 3
    assert parent["confidence"] == pytest.approx(5 / 8)


def test_validate_instance_records_feedback_action_and_detail(service, db_path):
    """验证结果能同步生成交易系统反馈动作，供后续复盘反向优化。"""
    _seed_daily_market(db_path, ["2026-04-15"])
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")

    result = service.validate_instance(
        inst["instance_id"],
        outcome="invalidated",
        outcome_fact_source="daily_market:2026-04-15",
        outcome_detail="板块未回流，核心中军同步走弱",
        feedback_action="deprecate",
        feedback_detail_json={
            "reason": "失效条件触发",
            "next_step": "复盘时检查是否弃用或收窄边界",
        },
        input_by="pytest",
    )

    updated = result["instance"]
    assert updated["feedback_action"] == "deprecate"
    feedback = json.loads(updated["feedback_detail_json"])
    assert feedback["reason"] == "失效条件触发"


def test_validate_instance_defaults_feedback_action_by_outcome(service, db_path):
    """调用方不传反馈动作时，service 按 outcome 给出保守默认动作。"""
    _seed_daily_market(db_path, ["2026-04-15"])
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")

    result = service.validate_instance(
        inst["instance_id"],
        outcome="validated",
        outcome_fact_source="daily_market:2026-04-15",
        input_by="pytest",
    )

    assert result["instance"]["feedback_action"] == "keep"


def test_batch_add_instances_partial_failure(service, db_path):
    """批量写入：部分失败不影响其他项；返回 created/failed/total。"""
    _seed_teacher_notes(db_path, [1, 2])
    cog = _make_cognition(service)
    items = [
        {
            "cognition_id": cog["cognition_id"],
            "observed_date": "2026-04-14",
            "source_type": "teacher_note",
            "source_note_id": 1,
        },
        {
            # 缺 observed_date → 失败
            "cognition_id": cog["cognition_id"],
            "source_type": "teacher_note",
            "observed_date": "",
        },
        {
            "cognition_id": cog["cognition_id"],
            "observed_date": "2026-04-15",
            "source_type": "teacher_note",
            "source_note_id": 2,
        },
    ]
    result = service.batch_add_instances(items, input_by="pytest")
    assert result["total"] == 3
    assert len(result["created"]) == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["item"]["observed_date"] == ""


# ──────────────────────────────────────────────────────────────
# 3. 周期复盘
# ──────────────────────────────────────────────────────────────
def test_generate_review_aggregates_stats(service, db_path):
    """期内 5 条 instance 被正确聚合：active_cognitions / outcome / teacher 参与。"""
    _seed_teachers(db_path, [(10, "沈纯"), (20, "小鲍")])
    _seed_teacher_notes(db_path, [1, 2, 3, 4, 5, 6])
    cog_a = _make_cognition(service, title="认知A")
    cog_b = _make_cognition(service, title="认知B", category="sentiment")

    # 期内 5 条实例（3 条 pending / 1 条 validated / 1 条 invalidated）
    _make_instance(
        service, cog_a["cognition_id"], observed_date="2026-04-07",
        source_note_id=1, teacher_id=10, teacher_name_snapshot="沈纯",
    )
    _make_instance(
        service, cog_a["cognition_id"], observed_date="2026-04-08",
        source_note_id=2, teacher_id=10, teacher_name_snapshot="沈纯",
    )
    _make_instance(
        service, cog_b["cognition_id"], observed_date="2026-04-09",
        source_note_id=3, teacher_id=20, teacher_name_snapshot="小鲍",
    )
    _make_instance(
        service, cog_b["cognition_id"], observed_date="2026-04-10",
        source_note_id=4, teacher_id=10, teacher_name_snapshot="沈纯",
        outcome="validated", outcome_fact_source="daily_market:2026-04-10",
    )
    _make_instance(
        service, cog_a["cognition_id"], observed_date="2026-04-11",
        source_note_id=5, teacher_id=20, teacher_name_snapshot="小鲍",
        outcome="invalidated", outcome_fact_source="daily_market:2026-04-12",
    )
    # 期外 1 条不应被计入
    _make_instance(
        service, cog_a["cognition_id"], observed_date="2026-04-20",
        source_note_id=6, teacher_id=10, teacher_name_snapshot="沈纯",
    )

    review = service.generate_review(
        period_type="weekly",
        period_start="2026-04-07",
        period_end="2026-04-11",
        input_by="pytest",
    )
    assert review["review_id"].startswith("rev_")
    assert review["status"] == "draft"

    active = json.loads(review["active_cognitions_json"])
    assert set(active) == {cog_a["cognition_id"], cog_b["cognition_id"]}

    stats = json.loads(review["validation_stats_json"])
    assert stats["total"] == 5
    assert stats["by_outcome"].get("pending") == 3
    assert stats["by_outcome"].get("validated") == 1
    assert stats["by_outcome"].get("invalidated") == 1

    participation = json.loads(review["teacher_participation_json"])
    by_teacher = {entry["teacher_id"]: entry["count"] for entry in participation["by_teacher"]}
    assert by_teacher[10] == 3
    assert by_teacher[20] == 2
    assert set(participation["teachers"]) == {"沈纯", "小鲍"}


def test_generate_review_collects_evolving_view_feedback(service, db_path):
    """周期复盘聚合验证反馈，形成认知反向优化清单。"""
    _seed_daily_market(db_path, ["2026-04-10", "2026-04-11"])
    cog_keep = _make_cognition(service, title="可保留认知")
    cog_refine = _make_cognition(service, title="需精炼认知")
    inst_keep = _make_instance(
        service,
        cog_keep["cognition_id"],
        observed_date="2026-04-08",
        context_summary="老师观点 A",
    )
    inst_refine = _make_instance(
        service,
        cog_refine["cognition_id"],
        observed_date="2026-04-09",
        context_summary="老师观点 B",
    )
    service.validate_instance(
        inst_keep["instance_id"],
        outcome="validated",
        outcome_fact_source="daily_market:2026-04-10",
        lesson="保留该回流框架",
        input_by="pytest",
    )
    service.validate_instance(
        inst_refine["instance_id"],
        outcome="invalidated",
        outcome_fact_source="daily_market:2026-04-11",
        feedback_action="refine",
        feedback_detail_json={"suggestion": "增加核心中军不能破位的边界"},
        lesson="失效来自核心中军走弱",
        input_by="pytest",
    )

    review = service.generate_review(
        period_type="weekly",
        period_start="2026-04-07",
        period_end="2026-04-11",
        input_by="pytest",
    )

    evolving = json.loads(review["evolving_views_json"])
    assert evolving["by_feedback_action"] == {"keep": 1, "refine": 1}
    refine_items = [item for item in evolving["items"] if item["feedback_action"] == "refine"]
    assert refine_items[0]["cognition_id"] == cog_refine["cognition_id"]
    assert refine_items[0]["lesson"] == "失效来自核心中军走弱"


def test_generate_review_duplicate_period_raises(service):
    """同 period_type+start+end 重复抛 ValueError。"""
    service.generate_review(
        period_type="weekly",
        period_start="2026-04-07",
        period_end="2026-04-11",
        input_by="pytest",
    )
    with pytest.raises(ValueError, match="已存在"):
        service.generate_review(
            period_type="weekly",
            period_start="2026-04-07",
            period_end="2026-04-11",
            input_by="pytest",
        )


def test_confirm_review(service):
    """draft → confirmed；confirmed_at 非空；再次 confirm 抛 ValueError。"""
    review = service.generate_review(
        period_type="weekly",
        period_start="2026-04-07",
        period_end="2026-04-11",
        input_by="pytest",
    )
    confirmed = service.confirm_review(
        review["review_id"],
        input_by="pytest",
        user_reflection="本周反思",
        action_items_json=["下周继续观察 M1/M2"],
        key_lessons_json={"principle_first": "优先套框架级认知"},
        performance_notes="纪律良好",
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"]
    assert confirmed["user_reflection"] == "本周反思"
    # 二次确认应被拒绝
    with pytest.raises(ValueError, match="draft"):
        service.confirm_review(
            review["review_id"], input_by="pytest",
        )


# ──────────────────────────────────────────────────────────────
# 4. pending 实例列表
# ──────────────────────────────────────────────────────────────
def test_list_pending_instances_check_ready(service):
    """check_ready=True 仅返回 observed_date<今日 的实例。"""
    cog = _make_cognition(service)
    # 过去日期（盘后可验证）
    past = _make_instance(service, cog["cognition_id"], observed_date="2026-01-10")
    # 未来日期（不应返回）
    future = _make_instance(service, cog["cognition_id"], observed_date="9999-12-31")

    ready = service.list_pending_instances(check_ready=True)
    ready_ids = {r["instance_id"] for r in ready}
    assert past["instance_id"] in ready_ids
    assert future["instance_id"] not in ready_ids

    all_pending = service.list_pending_instances(check_ready=False)
    assert {past["instance_id"], future["instance_id"]} <= {r["instance_id"] for r in all_pending}


# ──────────────────────────────────────────────────────────────
# 5. 事实源查表校验（补缺口）
# ──────────────────────────────────────────────────────────────
def test_validate_instance_fact_source_table_not_in_whitelist(service):
    """outcome_fact_source 的 table 不在白名单 → ValueError 且 outcome 保持 pending。"""
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    with pytest.raises(ValueError, match="白名单"):
        service.validate_instance(
            inst["instance_id"],
            outcome="validated",
            outcome_fact_source="unknown_table:2026-04-15",
            input_by="pytest",
        )
    stored = service._get_instance(inst["instance_id"])
    assert stored["outcome"] == "pending"
    assert stored["outcome_fact_source"] is None
    parent = service.get_cognition(cog["cognition_id"])
    assert parent["validated_count"] == 0


def test_validate_instance_fact_source_record_not_found(service):
    """白名单 table 但指定日期在 DB 不存在 → ValueError 且 outcome 保持 pending。"""
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    with pytest.raises(ValueError, match="未在 daily_market 中找到"):
        service.validate_instance(
            inst["instance_id"],
            outcome="validated",
            outcome_fact_source="daily_market:2026-04-15",
            input_by="pytest",
        )
    stored = service._get_instance(inst["instance_id"])
    assert stored["outcome"] == "pending"
    assert stored["outcome_fact_source"] is None


def test_validate_instance_fact_source_three_segments(service, db_path):
    """三段格式 'market_fact_snapshots:index:2026-04-15' 正常通过查表校验。"""
    _seed_market_fact_snapshots(
        db_path,
        [{
            "snapshot_id": "snap_test_001",
            "biz_date": "2026-04-15",
            "fact_type": "index_close",
            "subject_type": "index",
            "subject_code": "000001.SH",
            "subject_name": "上证指数",
        }],
    )
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    result = service.validate_instance(
        inst["instance_id"],
        outcome="validated",
        outcome_fact_source="market_fact_snapshots:index:2026-04-15",
        input_by="pytest",
    )
    assert result["instance"]["outcome"] == "validated"
    assert (
        result["instance"]["outcome_fact_source"]
        == "market_fact_snapshots:index:2026-04-15"
    )
    assert result["cognition"]["validated_count"] == 1


def test_trigger_outcome_revert_to_pending_recounts_parent(service, db_path):
    """触发器：outcome 从 validated 改回 pending 时 validated_count 正确减回 0。

    Phase 1b 没有 revert CLI，用直连 SQL 触发 UPDATE 以验证触发器行为本身。
    """
    _seed_daily_market(db_path, ["2026-04-15"])
    cog = _make_cognition(service)
    inst = _make_instance(service, cog["cognition_id"], observed_date="2026-04-14")
    service.validate_instance(
        inst["instance_id"],
        outcome="validated",
        outcome_fact_source="daily_market:2026-04-15",
        input_by="pytest",
    )
    parent_before = service.get_cognition(cog["cognition_id"])
    assert parent_before["validated_count"] == 1
    assert parent_before["instance_count"] == 1

    with get_db(db_path) as conn:
        migrate(conn)
        conn.execute(
            "UPDATE cognition_instances SET outcome = 'pending' WHERE instance_id = ?",
            (inst["instance_id"],),
        )

    parent_after = service.get_cognition(cog["cognition_id"])
    assert parent_after["validated_count"] == 0
    assert parent_after["invalidated_count"] == 0
    assert parent_after["instance_count"] == 1


def test_refine_cognition_reject_merged_status(service):
    """service 层兜底：refine 不允许把 status 置为 merged（必须走 merge 流程）。"""
    cog = _make_cognition(service)
    with pytest.raises(ValueError, match="merge 流程"):
        service.refine_cognition(
            cog["cognition_id"],
            input_by="pytest",
            status="merged",
        )
    # 原状态未变
    stored = service.get_cognition(cog["cognition_id"])
    assert stored["status"] == "candidate"
    assert stored["version"] == 1


def test_generate_review_rejects_inverted_range(service):
    """period_start > period_end 抛 ValueError。"""
    with pytest.raises(ValueError, match="不能晚于"):
        service.generate_review(
            period_type="weekly",
            period_start="2026-04-11",
            period_end="2026-04-07",
            input_by="pytest",
        )
