"""L5: /api/review/{date}/prefill 新增字段 `cognitions_by_step` 测试。

验证：
1. 8 个 step key 全部存在
2. 按 `_STEP_CATEGORY_MAP` 过滤 category
3. 只返回 `status='active'` 的认知
4. 空库下所有 step 返回 `[]`（非 null，也不缺 key）
5. 二次排序：confidence DESC → instance_count DESC tie-break
6. 每步 top 5 截断
7. JSON 字段（tags）反序列化为 list；conflict_group 无值透传 null

对应实施计划 `.plan` 文件「API 层 7 个测试用例清单」。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_EXPECTED_STEP_KEYS = {
    "step1_market",
    "step2_sectors",
    "step3_emotion",
    "step4_style",
    "step5_leaders",
    "step6_nodes",
    "step7_positions",
    "step8_plan",
}

# 业务日期选周五，避免 trade_calendar 空表时被判定为周末影响其它 prefill 分支
_TEST_DATE = "2026-04-17"


def _svc(db_path: str):
    from services.cognition_service import CognitionService
    return CognitionService(db_path=db_path)


def _raw_update(db_path: str, cognition_id: str, **fields) -> None:
    """绕过 CognitionService 直接写 DB 字段（用于测触发器维护的字段：
    confidence / instance_count / updated_at）。
    """
    if not fields:
        return
    conn = sqlite3.connect(db_path)
    try:
        sets = ", ".join(f"{col} = ?" for col in fields)
        conn.execute(
            f"UPDATE trading_cognitions SET {sets} WHERE cognition_id = ?",
            list(fields.values()) + [cognition_id],
        )
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────
# 用例 1：字段存在性（含 8 个 step key）
# ──────────────────────────────────────────────────────────────
def test_prefill_exposes_cognitions_by_step_with_all_eight_keys(api_client):
    client, _ = api_client
    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    assert r.status_code == 200
    body = r.json()
    assert "cognitions_by_step" in body, "prefill 响应须包含 cognitions_by_step 字段"
    assert set(body["cognitions_by_step"].keys()) == _EXPECTED_STEP_KEYS


# ──────────────────────────────────────────────────────────────
# 用例 2：按 category 过滤（_STEP_CATEGORY_MAP 生效）
# ──────────────────────────────────────────────────────────────
def test_prefill_cognitions_filter_by_step_category_map(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="structure",
        title="S1",
        description="D",
        status="active",
        input_by="pytest",
    )
    svc.add_cognition(
        category="sentiment",
        title="E1",
        description="D",
        status="active",
        input_by="pytest",
    )

    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    assert r.status_code == 200
    groups = r.json()["cognitions_by_step"]

    # step1_market（主 structure / 次 macro, cycle）→ 命中 S1
    assert {c["title"] for c in groups["step1_market"]} == {"S1"}
    # step3_emotion（只有 sentiment）→ 命中 E1
    assert {c["title"] for c in groups["step3_emotion"]} == {"E1"}
    # step5_leaders（只有 execution）→ 两条都不命中
    assert groups["step5_leaders"] == []


# ──────────────────────────────────────────────────────────────
# 用例 3：只返回 active 认知（candidate / deprecated 被过滤）
# ──────────────────────────────────────────────────────────────
def test_prefill_cognitions_only_returns_active_status(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="structure",
        title="ACT",
        description="D",
        status="active",
        input_by="pytest",
    )
    svc.add_cognition(
        category="structure",
        title="CAND",
        description="D",
        status="candidate",
        input_by="pytest",
    )
    # deprecated 路径必须走 service（deprecate 会改 tags）
    cog = svc.add_cognition(
        category="structure",
        title="DEP",
        description="D",
        status="candidate",
        input_by="pytest",
    )
    svc.deprecate_cognition(cog["cognition_id"], reason="old", input_by="pytest")

    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    titles = {c["title"] for c in r.json()["cognitions_by_step"]["step1_market"]}
    assert titles == {"ACT"}


# ──────────────────────────────────────────────────────────────
# 用例 4：空库 → 8 个 step key 全部为 []
# ──────────────────────────────────────────────────────────────
def test_prefill_empty_db_returns_all_empty_arrays(api_client):
    client, _ = api_client
    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    cog_map = r.json()["cognitions_by_step"]
    for step_key in _EXPECTED_STEP_KEYS:
        assert cog_map[step_key] == [], f"{step_key} 应为空数组，实得 {cog_map[step_key]!r}"


# ──────────────────────────────────────────────────────────────
# 用例 5：二次排序（confidence DESC → instance_count DESC tie-break）
# ──────────────────────────────────────────────────────────────
def test_prefill_cognitions_secondary_sort_by_confidence_and_instance_count(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    c_high = svc.add_cognition(
        category="structure", title="HIGH", description="D",
        status="active", input_by="pytest",
    )
    c_mid = svc.add_cognition(
        category="structure", title="MID", description="D",
        status="active", input_by="pytest",
    )
    c_low = svc.add_cognition(
        category="structure", title="LOW", description="D",
        status="active", input_by="pytest",
    )
    c_tie_hi = svc.add_cognition(
        category="structure", title="TIE_HI", description="D",
        status="active", input_by="pytest",
    )
    c_tie_lo = svc.add_cognition(
        category="structure", title="TIE_LO", description="D",
        status="active", input_by="pytest",
    )

    _raw_update(db_path, c_high["cognition_id"], confidence=0.8, instance_count=1)
    _raw_update(db_path, c_mid["cognition_id"], confidence=0.6, instance_count=1)
    _raw_update(db_path, c_low["cognition_id"], confidence=0.4, instance_count=1)
    _raw_update(db_path, c_tie_hi["cognition_id"], confidence=0.5, instance_count=10)
    _raw_update(db_path, c_tie_lo["cognition_id"], confidence=0.5, instance_count=2)

    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    titles = [c["title"] for c in r.json()["cognitions_by_step"]["step1_market"]]
    # 期望：0.8 → 0.6 → 0.5(inst=10) → 0.5(inst=2) → 0.4
    assert titles == ["HIGH", "MID", "TIE_HI", "TIE_LO", "LOW"]


# ──────────────────────────────────────────────────────────────
# 用例 6：top 5 截断
# ──────────────────────────────────────────────────────────────
def test_prefill_cognitions_top5_truncation(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    for idx in range(7):
        svc.add_cognition(
            category="sentiment",
            title=f"T{idx}",
            description="D",
            status="active",
            input_by="pytest",
        )
    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    step3 = r.json()["cognitions_by_step"]["step3_emotion"]
    assert len(step3) == 5, f"step3_emotion 应被截断至 5 条，实得 {len(step3)} 条"


# ──────────────────────────────────────────────────────────────
# 用例 7：JSON 字段解析（tags → list[str]）+ conflict_group 透传 null
# ──────────────────────────────────────────────────────────────
def test_prefill_cognition_json_fields_parsed(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="structure",
        title="T",
        description="D",
        status="active",
        tags=["分化", "量价"],
        input_by="pytest",
    )
    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    cog = r.json()["cognitions_by_step"]["step1_market"][0]
    assert isinstance(cog["tags"], list), "tags 须解析为 list[str]"
    assert cog["tags"] == ["分化", "量价"]
    # conflict_group 未赋值时应透传为 null（JSON null == Python None）
    assert cog["conflict_group"] is None
    # 白名单字段存在性校验（关键几条）
    for field in (
        "cognition_id",
        "title",
        "category",
        "evidence_level",
        "confidence",
        "instance_count",
        "validated_count",
        "invalidated_count",
        "pattern",
        "sub_category",
    ):
        assert field in cog, f"白名单字段 {field} 缺失"
    # 非白名单字段不应返回
    for banned in ("description", "created_at", "updated_at", "status"):
        assert banned not in cog, f"字段 {banned} 不应出现在 prefill 响应"


# ──────────────────────────────────────────────────────────────
# 用例 8：`fundamental` / `valuation` 映射独占断言
#   - fundamental 只映射到 step7_positions
#   - valuation  只映射到 step8_plan
#   - 两者互不越界（防止后续改动误扩 _STEP_CATEGORY_MAP）
# ──────────────────────────────────────────────────────────────
def test_prefill_fundamental_and_valuation_exclusive_mapping(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    svc.add_cognition(
        category="fundamental",
        title="F1",
        description="D",
        status="active",
        input_by="pytest",
    )
    svc.add_cognition(
        category="valuation",
        title="V1",
        description="D",
        status="active",
        input_by="pytest",
    )
    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    groups = r.json()["cognitions_by_step"]

    # 定位 fundamental 出现的步骤集合
    steps_with_fundamental = {
        key for key, items in groups.items()
        if any(c["category"] == "fundamental" for c in items)
    }
    steps_with_valuation = {
        key for key, items in groups.items()
        if any(c["category"] == "valuation" for c in items)
    }

    assert steps_with_fundamental == {"step7_positions"}, (
        f"fundamental 必须仅映射 step7_positions，实得 {steps_with_fundamental}"
    )
    assert steps_with_valuation == {"step8_plan"}, (
        f"valuation 必须仅映射 step8_plan，实得 {steps_with_valuation}"
    )


# ──────────────────────────────────────────────────────────────
# 用例 9：tags 为非法 JSON 字符串时优雅降级（置 null，不拖垮 prefill）
#   覆盖 _parse_cognition_json_fields 的 try/except 路径。
# ──────────────────────────────────────────────────────────────
def test_prefill_malformed_tags_json_degrades_to_null(api_client):
    client, db_path = api_client
    svc = _svc(db_path)
    cog = svc.add_cognition(
        category="structure",
        title="BAD_JSON",
        description="D",
        status="active",
        tags=["ok"],
        input_by="pytest",
    )
    # 绕过 service 往 DB 写入一段非法 JSON；模拟历史脏数据
    _raw_update(db_path, cog["cognition_id"], tags="{not-valid-json}")

    r = client.get(f"/api/review/{_TEST_DATE}/prefill")
    assert r.status_code == 200, "prefill 不应被坏 JSON 拖垮"
    step1 = r.json()["cognitions_by_step"]["step1_market"]
    bad = next(c for c in step1 if c["title"] == "BAD_JSON")
    assert bad["tags"] is None, "非法 JSON 的 tags 应降级为 null"
