"""L5: /api/cognition/* 只读 API 测试。

验证：
- list / show 正常返回 + JSON 字段自动反序列化
- 参数过滤（status / category / conflict_group / cognition_id / outcome / teacher_id / period_type）
- 非法枚举 / 非法日期 → 422
- 未找到资源 → 404
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _svc(db_path: str):
    from services.cognition_service import CognitionService
    return CognitionService(db_path=db_path)


# ──────────────────────────────────────────────────────────────
# /cognitions
# ──────────────────────────────────────────────────────────────
def test_list_cognitions_empty(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/cognitions")
    assert r.status_code == 200
    body = r.json()
    assert body == {"total": 0, "cognitions": []}


def test_list_cognitions_returns_seeded(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="signal", title="T1", description="D1",
        tags=["a", "b"], input_by="pytest",
    )
    svc.add_cognition(
        category="sentiment", title="T2", description="D2", input_by="pytest",
    )
    r = client.get("/api/cognition/cognitions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {c["title"] for c in body["cognitions"]} == {"T1", "T2"}


def test_list_cognitions_filter_category_and_status(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="signal", title="A", description="D",
        status="candidate", input_by="pytest",
    )
    svc.add_cognition(
        category="signal", title="B", description="D",
        status="active", input_by="pytest",
    )
    svc.add_cognition(
        category="sentiment", title="C", description="D",
        status="candidate", input_by="pytest",
    )
    r = client.get("/api/cognition/cognitions?category=signal&status=candidate")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["cognitions"][0]["title"] == "A"


def test_list_cognitions_filter_conflict_group(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="signal", title="X", description="D",
        conflict_group="reversal_v_follow", input_by="pytest",
    )
    svc.add_cognition(
        category="signal", title="Y", description="D", input_by="pytest",
    )
    r = client.get("/api/cognition/cognitions?conflict_group=reversal_v_follow")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["cognitions"][0]["title"] == "X"


def test_list_cognitions_invalid_status_returns_422(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/cognitions?status=invalid_enum")
    assert r.status_code == 422


def test_get_cognition_parses_json_fields(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    cog = svc.add_cognition(
        category="signal", title="T", description="D",
        tags=["tag1", "tag2"],
        conditions_json={"k": "v"},
        input_by="pytest",
    )
    r = client.get(f"/api/cognition/cognitions/{cog['cognition_id']}")
    assert r.status_code == 200
    body = r.json()
    assert "cognition" in body, "单条端点须返回 {cognition: ...} 包装，对齐 CLI 风格"
    cog_body = body["cognition"]
    assert isinstance(cog_body["tags"], list)
    assert cog_body["tags"] == ["tag1", "tag2"]
    assert isinstance(cog_body["conditions_json"], dict)
    assert cog_body["conditions_json"] == {"k": "v"}


def test_get_cognition_not_exist_returns_404(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/cognitions/not_exist")
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────
# /instances
# ──────────────────────────────────────────────────────────────
def test_list_instances_filter_cognition_and_outcome(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    cog = svc.add_cognition(
        category="signal", title="T", description="D", input_by="pytest",
    )
    cog_id = cog["cognition_id"]
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-10",
        source_type="teacher_note", input_by="pytest",
    )
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-11",
        source_type="teacher_note", input_by="pytest",
    )

    r = client.get(
        f"/api/cognition/instances?cognition_id={cog_id}&outcome=pending"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    for row in body["instances"]:
        assert row["cognition_id"] == cog_id
        assert row["outcome"] == "pending"


def test_list_instances_invalid_date_returns_422(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/instances?date_from=abc")
    assert r.status_code == 422


def test_list_instances_filter_teacher_id(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    cog = svc.add_cognition(
        category="signal", title="T", description="D", input_by="pytest",
    )
    cog_id = cog["cognition_id"]
    # 插入 teachers 行以满足 FK
    from db.connection import get_connection
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO teachers (id, name) VALUES (1, 'teacher_a')")
        conn.execute("INSERT INTO teachers (id, name) VALUES (2, 'teacher_b')")
        conn.commit()
    finally:
        conn.close()
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-10",
        source_type="teacher_note", teacher_id=1, input_by="pytest",
    )
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-11",
        source_type="teacher_note", teacher_id=2,
        input_by="pytest",
    )

    r = client.get("/api/cognition/instances?teacher_id=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["instances"][0]["teacher_id"] == 1


def test_list_instances_filter_source_type(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    cog = svc.add_cognition(
        category="signal", title="T", description="D", input_by="pytest",
    )
    cog_id = cog["cognition_id"]
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-10",
        source_type="teacher_note", input_by="pytest",
    )
    svc.add_instance(
        cognition_id=cog_id, observed_date="2026-04-11",
        source_type="plan_review", input_by="pytest",
    )
    r = client.get("/api/cognition/instances?source_type=plan_review")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["instances"][0]["source_type"] == "plan_review"


# ──────────────────────────────────────────────────────────────
# /reviews
# ──────────────────────────────────────────────────────────────
def test_list_reviews_filter_period_type(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.generate_review(
        period_type="weekly", period_start="2026-04-07", period_end="2026-04-11",
        input_by="pytest",
    )
    svc.generate_review(
        period_type="monthly", period_start="2026-04-01", period_end="2026-04-30",
        input_by="pytest",
    )
    r = client.get("/api/cognition/reviews?period_type=weekly")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["reviews"][0]["period_type"] == "weekly"


def test_get_review_parses_validation_stats(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    rev = svc.generate_review(
        period_type="weekly", period_start="2026-04-07", period_end="2026-04-11",
        input_by="pytest",
    )
    r = client.get(f"/api/cognition/reviews/{rev['review_id']}")
    assert r.status_code == 200
    body = r.json()
    assert "review" in body, "单条端点须返回 {review: ...} 包装，对齐 CLI 风格"
    rev_body = body["review"]
    assert isinstance(rev_body["validation_stats_json"], dict)
    assert "total" in rev_body["validation_stats_json"]
    assert "by_outcome" in rev_body["validation_stats_json"]


def test_get_review_not_exist_returns_404(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/reviews/not_exist")
    assert r.status_code == 404


def test_list_reviews_invalid_period_type_returns_422(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/reviews?period_type=bogus")
    assert r.status_code == 422


def test_list_reviews_invalid_date_returns_422(api_client):
    client, _ = api_client
    r = client.get("/api/cognition/reviews?date_from=not-a-date")
    assert r.status_code == 422
