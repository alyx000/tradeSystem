"""交易认知服务：trading_cognitions / cognition_instances / periodic_reviews 写入与查询。

对应《交易认知进化系统-v2》方案 §5、§七；Phase 1b 手动闭环版。
本模块只负责 CRUD + 聚合查询，Phase 2+ 将追加：
- 种子认知库匹配 / 框架优先提取策略
- 多老师共识度计算 / 高共识标记
- LLM 驱动的共识 / 分歧摘要
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type, datetime
from functools import lru_cache
from pathlib import Path
import json
import re
from typing import Any
from uuid import uuid4

from db.connection import get_db
from db.migrate import migrate

try:  # yaml 是项目已有依赖，失败时降级为硬编码分类
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml 不可用是极端情况
    yaml = None  # type: ignore


# ──────────────────────────────────────────────────────────────
# 常量与枚举（与 schema CHECK 约束一致）
# ──────────────────────────────────────────────────────────────
_ALLOWED_EVIDENCE_LEVELS = frozenset({"observation", "hypothesis", "principle"})
_ALLOWED_COGNITION_STATUS = frozenset({"candidate", "active", "deprecated", "merged"})
_ALLOWED_INSTANCE_OUTCOMES = frozenset(
    {"pending", "validated", "invalidated", "partial", "not_applicable"}
)
_VALIDATE_OUTCOMES = frozenset({"validated", "invalidated", "partial", "not_applicable"})
_ALLOWED_PERIOD_TYPES = frozenset({"weekly", "monthly", "quarterly", "yearly"})
_ALLOWED_REVIEW_SCOPES = frozenset({"calendar_period", "event_window", "regime_window"})

# 分类的硬编码 fallback，YAML 可加载时以 YAML 为准
_FALLBACK_CATEGORIES = frozenset(
    {
        "signal", "sentiment", "structure", "cycle", "position",
        "sizing", "synthesis", "fundamental", "macro", "valuation", "execution",
    }
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 允许 '<table>:<YYYY-MM-DD>' 或 '<table>:<sub>:<YYYY-MM-DD>'（如 market_fact_snapshots:index:2026-04-15）
_FACT_SOURCE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(?::[a-zA-Z0-9_]+)*:\d{4}-\d{2}-\d{2}$")

# outcome_fact_source 允许的表白名单 —— 方案 §5 约定的事实源
_FACT_SOURCE_TABLES = frozenset({"daily_market", "market_fact_snapshots", "fact_entities"})

# 事实源表到「日期字段」的映射；schema.py 中 daily_market 用 date，
# market_fact_snapshots / fact_entities 用 biz_date
_FACT_SOURCE_DATE_COLUMN: dict[str, str] = {
    "daily_market": "date",
    "market_fact_snapshots": "biz_date",
    "fact_entities": "biz_date",
}

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TAXONOMY_PATH = _BASE_DIR / "config" / "cognition_taxonomy.yaml"


# ──────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, Any]:
    if yaml is None or not _TAXONOMY_PATH.exists():
        return {}
    try:
        with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _allowed_categories() -> frozenset[str]:
    data = _load_taxonomy()
    cats = data.get("categories") if isinstance(data, dict) else None
    if isinstance(cats, dict) and cats:
        return frozenset(cats.keys())
    return _FALLBACK_CATEGORIES


def _validate_iso_date(label: str, value: str) -> str:
    s = (value or "").strip()
    if not _ISO_DATE_RE.match(s):
        raise ValueError(f"{label} 须为 YYYY-MM-DD，收到: {value!r}")
    try:
        date_type.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"{label} 不是合法日历日期: {value!r}") from exc
    return s


def _ensure_valid_json_str(value: Any, field_name: str) -> str | None:
    """把 JSON 入参统一为字符串。

    入参允许 dict / list / 字符串 / None；字符串必须是合法 JSON，否则 ValueError。
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            json.loads(s)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是合法 JSON: {exc.msg}") from exc
        return s
    raise ValueError(
        f"{field_name} 需 JSON 字符串 / dict / list / None，收到 {type(value).__name__}"
    )


def _normalize_tags(value: Any) -> str | None:
    """tags 统一存 JSON 数组字符串。

    - None / 空 → None
    - list[str] → JSON 字符串
    - 合法 JSON 数组字符串 → 原样返回（校验后）
    - 单字符串 → 包裹成一元数组
    """
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps([str(x) for x in value], ensure_ascii=False)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return json.dumps([str(x) for x in parsed], ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        return json.dumps([s], ensure_ascii=False)
    raise ValueError(f"tags 需 list 或 JSON 数组字符串，收到 {type(value).__name__}")


def _parse_fact_source(value: str | None) -> tuple[str, str, str]:
    """解析 outcome_fact_source 为 (原串, table, date)；同时做格式 + 白名单校验。

    支持两种形态：
    - '<table>:<YYYY-MM-DD>'（如 'daily_market:2026-04-15'）
    - '<table>:<subject>:<YYYY-MM-DD>'（如 'market_fact_snapshots:index:2026-04-15'）
    """
    s = (value or "").strip() if value is not None else ""
    if not s:
        raise ValueError(
            "outcome_fact_source 必填（格式 '<table>:<YYYY-MM-DD>' "
            "或 '<table>:<subject>:<YYYY-MM-DD>'）"
        )
    if not _FACT_SOURCE_RE.match(s):
        raise ValueError(
            "outcome_fact_source 格式非法，需形如 '<table>:<YYYY-MM-DD>' "
            f"或 '<table>:<subject>:<YYYY-MM-DD>'，收到: {value!r}"
        )
    parts = s.split(":")
    table = parts[0]
    date_str = parts[-1]
    if table not in _FACT_SOURCE_TABLES:
        raise ValueError(
            f"outcome_fact_source 表不在白名单: {table}（允许: {sorted(_FACT_SOURCE_TABLES)}）"
        )
    return s, table, date_str


def _validate_fact_source(value: str | None) -> str:
    """outcome_fact_source 必填；做格式 + 白名单校验，返回规范化后的字符串。"""
    s, _table, _date = _parse_fact_source(value)
    return s


def _assert_fact_source_record_exists(conn, fact_source: str) -> None:
    """在同一连接内校验事实源记录存在；不存在时抛 ValueError。

    查询字段对应 schema.py 当前定义：
    - daily_market.date（主键）
    - market_fact_snapshots.biz_date
    - fact_entities.biz_date
    """
    _s, table, date_str = _parse_fact_source(fact_source)
    col = _FACT_SOURCE_DATE_COLUMN.get(table)
    if col is None:  # pragma: no cover - 白名单已在 _parse 校验过
        raise ValueError(f"outcome_fact_source 表不在白名单: {table}")
    sql = f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1"
    row = conn.execute(sql, (date_str,)).fetchone()
    if row is None:
        raise ValueError(
            f"outcome_fact_source 未在 {table} 中找到 {date_str} 记录"
        )


def _sql_like_literal(keyword: str) -> str:
    """关键词字面化：对 % _ ! 做转义，与 ESCAPE '!' 配合。"""
    return keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate_input_by(value: str | None) -> str:
    """写入入口 input_by 非空校验，与方案 §七 一致。"""
    s = (value or "").strip() if value is not None else ""
    if not s:
        raise ValueError("input_by 必填（建议 cursor / claude / web / manual）")
    return s


# ──────────────────────────────────────────────────────────────
# 服务实现
# ──────────────────────────────────────────────────────────────
@dataclass
class CognitionService:
    """认知层标准入口。所有写入走本服务，禁止绕过直写 SQL。"""

    db_path: str | None = None

    # ============================================================
    # 一、trading_cognitions 管理
    # ============================================================
    def add_cognition(
        self,
        *,
        category: str,
        title: str,
        description: str,
        sub_category: str | None = None,
        pattern: str | None = None,
        time_horizon: str | None = None,
        action_template: str | None = None,
        position_template: str | None = None,
        conditions_json: Any = None,
        exceptions_json: Any = None,
        invalidation_conditions_json: Any = None,
        evidence_level: str = "observation",
        conflict_group: str | None = None,
        first_source_note_id: int | None = None,
        first_observed_date: str | None = None,
        tags: Any = None,
        status: str = "candidate",
        input_by: str = "manual",
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        cat = (category or "").strip()
        if not cat:
            raise ValueError("category 必填")
        allowed = _allowed_categories()
        if cat not in allowed:
            raise ValueError(
                f"category 非法，需为 {sorted(allowed)} 之一；收到: {category!r}"
            )
        t = (title or "").strip()
        if not t:
            raise ValueError("title 必填")
        desc = (description or "").strip()
        if not desc:
            raise ValueError("description 必填")
        if evidence_level not in _ALLOWED_EVIDENCE_LEVELS:
            raise ValueError(
                f"evidence_level 非法，需为 {sorted(_ALLOWED_EVIDENCE_LEVELS)} 之一；"
                f"收到: {evidence_level!r}"
            )
        if status not in _ALLOWED_COGNITION_STATUS:
            raise ValueError(
                f"status 非法，需为 {sorted(_ALLOWED_COGNITION_STATUS)} 之一；收到: {status!r}"
            )
        if first_observed_date is not None:
            first_observed_date = _validate_iso_date("first_observed_date", first_observed_date)

        conditions_s = _ensure_valid_json_str(conditions_json, "conditions_json")
        exceptions_s = _ensure_valid_json_str(exceptions_json, "exceptions_json")
        invalidation_s = _ensure_valid_json_str(
            invalidation_conditions_json, "invalidation_conditions_json"
        )
        tags_s = _normalize_tags(tags)

        cognition_id = f"cog_{uuid4().hex[:8]}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO trading_cognitions
                (cognition_id, category, sub_category, title, description, pattern,
                 time_horizon, action_template, position_template,
                 conditions_json, exceptions_json, invalidation_conditions_json,
                 evidence_level, conflict_group, first_source_note_id, first_observed_date,
                 tags, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cognition_id,
                    cat,
                    (sub_category or None),
                    t,
                    desc,
                    pattern,
                    time_horizon,
                    action_template,
                    position_template,
                    conditions_s,
                    exceptions_s,
                    invalidation_s,
                    evidence_level,
                    conflict_group,
                    first_source_note_id,
                    first_observed_date,
                    tags_s,
                    status,
                ),
            )
        return self.get_cognition(cognition_id)

    def list_cognitions(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        sub_category: str | None = None,
        evidence_level: str | None = None,
        conflict_group: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if status:
            if status not in _ALLOWED_COGNITION_STATUS:
                raise ValueError(
                    f"status 非法，需为 {sorted(_ALLOWED_COGNITION_STATUS)} 之一；"
                    f"收到: {status!r}"
                )
            where.append("status = ?")
            params.append(status)
        if category:
            where.append("category = ?")
            params.append(category)
        if sub_category:
            where.append("sub_category = ?")
            params.append(sub_category)
        if evidence_level:
            if evidence_level not in _ALLOWED_EVIDENCE_LEVELS:
                raise ValueError(
                    f"evidence_level 非法，需为 {sorted(_ALLOWED_EVIDENCE_LEVELS)} 之一；"
                    f"收到: {evidence_level!r}"
                )
            where.append("evidence_level = ?")
            params.append(evidence_level)
        if conflict_group:
            where.append("conflict_group = ?")
            params.append(conflict_group)
        kw = (keyword or "").strip()
        if kw:
            like_pat = f"%{_sql_like_literal(kw)}%"
            where.append(
                "(title LIKE ? ESCAPE '!' OR description LIKE ? ESCAPE '!' "
                "OR IFNULL(pattern,'') LIKE ? ESCAPE '!')"
            )
            params.extend([like_pat, like_pat, like_pat])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT * FROM trading_cognitions"
            + where_sql
            + " ORDER BY created_at DESC, cognition_id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_cognition(self, cognition_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT * FROM trading_cognitions WHERE cognition_id = ?",
                (cognition_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"cognition not found: {cognition_id}")
            data = dict(row)
            agg = conn.execute(
                """
                SELECT outcome, COUNT(*) AS n
                FROM cognition_instances
                WHERE cognition_id = ?
                GROUP BY outcome
                """,
                (cognition_id,),
            ).fetchall()
        by_outcome: dict[str, int] = {}
        total = 0
        for r in agg:
            by_outcome[r["outcome"]] = int(r["n"])
            total += int(r["n"])
        data["instances_stats"] = {"total": total, "by_outcome": by_outcome}
        return data

    def refine_cognition(
        self,
        cognition_id: str,
        *,
        input_by: str,
        description: str | None = None,
        pattern: str | None = None,
        conditions_json: Any = None,
        action_template: str | None = None,
        position_template: str | None = None,
        exceptions_json: Any = None,
        invalidation_conditions_json: Any = None,
        evidence_level: str | None = None,
        tags: Any = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        self.get_cognition(cognition_id)  # 不存在则 KeyError

        updates: dict[str, Any] = {}
        if description is not None:
            if not description.strip():
                raise ValueError("description 不能置为空串")
            updates["description"] = description.strip()
        if pattern is not None:
            updates["pattern"] = pattern
        if conditions_json is not None:
            updates["conditions_json"] = _ensure_valid_json_str(conditions_json, "conditions_json")
        if action_template is not None:
            updates["action_template"] = action_template
        if position_template is not None:
            updates["position_template"] = position_template
        if exceptions_json is not None:
            updates["exceptions_json"] = _ensure_valid_json_str(exceptions_json, "exceptions_json")
        if invalidation_conditions_json is not None:
            updates["invalidation_conditions_json"] = _ensure_valid_json_str(
                invalidation_conditions_json, "invalidation_conditions_json"
            )
        if evidence_level is not None:
            if evidence_level not in _ALLOWED_EVIDENCE_LEVELS:
                raise ValueError(
                    f"evidence_level 非法，需为 {sorted(_ALLOWED_EVIDENCE_LEVELS)} 之一；"
                    f"收到: {evidence_level!r}"
                )
            updates["evidence_level"] = evidence_level
        if tags is not None:
            updates["tags"] = _normalize_tags(tags)
        if status is not None:
            if status == "merged":
                raise ValueError(
                    "status=merged 必须通过 merge 流程，不能用 refine"
                )
            if status not in _ALLOWED_COGNITION_STATUS:
                raise ValueError(
                    f"status 非法，需为 {sorted(_ALLOWED_COGNITION_STATUS)} 之一；"
                    f"收到: {status!r}"
                )
            updates["status"] = status

        if not updates:
            return self.get_cognition(cognition_id)

        with get_db(self.db_path) as conn:
            migrate(conn)
            sets = ", ".join(f"{col} = ?" for col in updates.keys())
            params = list(updates.values())
            conn.execute(
                f"UPDATE trading_cognitions SET {sets}, "
                "version = version + 1, updated_at = datetime('now') "
                "WHERE cognition_id = ?",
                params + [cognition_id],
            )
        return self.get_cognition(cognition_id)

    def deprecate_cognition(
        self,
        cognition_id: str,
        *,
        reason: str,
        input_by: str,
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        r = (reason or "").strip()
        if not r:
            raise ValueError("reason 必填")
        current = self.get_cognition(cognition_id)
        raw_tags = current.get("tags")
        tag_list: list[str] = []
        if raw_tags:
            try:
                parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
            except json.JSONDecodeError:
                # 旧 tags 不是合法 JSON，保留原字符串到显式占位，避免静默丢失
                tag_list.append(f"__original_tags__:{raw_tags}")
            else:
                if isinstance(parsed, list):
                    tag_list = [str(x) for x in parsed]
                else:
                    # dict / 数字 / null 等非列表结构也保留原 JSON 表示
                    try:
                        original_repr = json.dumps(parsed, ensure_ascii=False)
                    except (TypeError, ValueError):
                        original_repr = str(raw_tags)
                    tag_list.append(f"__original_tags__:{original_repr}")
        tag_list.append(f"deprecated_reason:{r}")
        tags_s = json.dumps(tag_list, ensure_ascii=False)

        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                "UPDATE trading_cognitions SET status = 'deprecated', tags = ?, "
                "version = version + 1, updated_at = datetime('now') "
                "WHERE cognition_id = ?",
                (tags_s, cognition_id),
            )
        return self.get_cognition(cognition_id)

    # ============================================================
    # 二、cognition_instances 管理
    # ============================================================
    def add_instance(
        self,
        *,
        cognition_id: str,
        observed_date: str,
        source_type: str,
        source_note_id: int | None = None,
        teacher_id: int | None = None,
        teacher_name_snapshot: str | None = None,
        source_plan_review_id: str | None = None,
        source_daily_review_date: str | None = None,
        trade_id: int | None = None,
        context_summary: str | None = None,
        regime_tags_json: Any = None,
        time_horizon: str | None = None,
        action_bias: str | None = None,
        position_cap: float | None = None,
        avoid_action: str | None = None,
        market_regime: str | None = None,
        cross_market_anchor: str | None = None,
        consensus_key: str | None = None,
        parameters_json: Any = None,
        teacher_original_text: str | None = None,
        outcome: str = "pending",
        outcome_detail: str | None = None,
        outcome_fact_source: str | None = None,
        outcome_fact_refs_json: Any = None,
        outcome_date: str | None = None,
        lesson: str | None = None,
        input_by: str = "manual",
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        cid = (cognition_id or "").strip()
        if not cid:
            raise ValueError("cognition_id 必填")
        obs_date = _validate_iso_date("observed_date", observed_date)
        st = (source_type or "").strip()
        if not st:
            raise ValueError("source_type 必填")
        if outcome not in _ALLOWED_INSTANCE_OUTCOMES:
            raise ValueError(
                f"outcome 非法，需为 {sorted(_ALLOWED_INSTANCE_OUTCOMES)} 之一；"
                f"收到: {outcome!r}"
            )
        if outcome in {"validated", "invalidated"} and not (outcome_fact_source or "").strip():
            raise ValueError(
                "outcome 为 validated/invalidated 时必须同时带 outcome_fact_source"
            )
        if outcome_fact_source:
            outcome_fact_source = _validate_fact_source(outcome_fact_source)
        if outcome_date is not None:
            outcome_date = _validate_iso_date("outcome_date", outcome_date)
        if source_daily_review_date is not None:
            source_daily_review_date = _validate_iso_date(
                "source_daily_review_date", source_daily_review_date
            )

        regime_s = _ensure_valid_json_str(regime_tags_json, "regime_tags_json")
        params_s = _ensure_valid_json_str(parameters_json, "parameters_json")
        refs_s = _ensure_valid_json_str(outcome_fact_refs_json, "outcome_fact_refs_json")

        instance_id = f"inst_{uuid4().hex[:8]}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            parent = conn.execute(
                "SELECT cognition_id FROM trading_cognitions WHERE cognition_id = ?",
                (cid,),
            ).fetchone()
            if parent is None:
                raise ValueError(f"cognition not found: {cognition_id}")

            # NULL 漏洞兜底：UNIQUE 对 NULL 失效，需手工 existence check
            if source_note_id is None:
                existing = conn.execute(
                    "SELECT instance_id FROM cognition_instances "
                    "WHERE cognition_id = ? AND observed_date = ? "
                    "AND source_type = ? AND source_note_id IS NULL",
                    (cid, obs_date, st),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT instance_id FROM cognition_instances "
                    "WHERE cognition_id = ? AND observed_date = ? "
                    "AND source_type = ? AND source_note_id = ?",
                    (cid, obs_date, st, int(source_note_id)),
                ).fetchone()
            if existing is not None:
                raise ValueError(
                    f"instance_exists: {existing['instance_id']}（"
                    f"cognition_id={cid}, observed_date={obs_date}, "
                    f"source_type={st}, source_note_id={source_note_id}）"
                )

            conn.execute(
                """
                INSERT INTO cognition_instances
                (instance_id, cognition_id, observed_date, source_type, source_note_id,
                 teacher_id, teacher_name_snapshot, source_plan_review_id,
                 source_daily_review_date, trade_id, context_summary, regime_tags_json,
                 time_horizon, action_bias, position_cap, avoid_action, market_regime,
                 cross_market_anchor, consensus_key, parameters_json, teacher_original_text,
                 outcome, outcome_detail, outcome_fact_source, outcome_fact_refs_json,
                 outcome_date, lesson)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    cid,
                    obs_date,
                    st,
                    source_note_id,
                    teacher_id,
                    teacher_name_snapshot,
                    source_plan_review_id,
                    source_daily_review_date,
                    trade_id,
                    context_summary,
                    regime_s,
                    time_horizon,
                    action_bias,
                    position_cap,
                    avoid_action,
                    market_regime,
                    cross_market_anchor,
                    consensus_key,
                    params_s,
                    teacher_original_text,
                    outcome,
                    outcome_detail,
                    outcome_fact_source,
                    refs_s,
                    outcome_date,
                    lesson,
                ),
            )
        return self._get_instance(instance_id)

    def batch_add_instances(
        self,
        items: list[dict[str, Any]],
        *,
        input_by: str = "manual",
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        if not isinstance(items, list):
            raise ValueError("items 必须是 list[dict]")
        created: list[str] = []
        failed: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                failed.append({"item": item, "reason": "item 不是 dict"})
                continue
            kwargs = dict(item)
            kwargs.setdefault("input_by", input_by)
            try:
                inst = self.add_instance(**kwargs)
                created.append(inst["instance_id"])
            except (ValueError, KeyError) as exc:
                failed.append({"item": item, "reason": str(exc)})
        return {"created": created, "failed": failed, "total": len(items)}

    def list_pending_instances(
        self,
        *,
        observed_date: str | None = None,
        check_ready: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """列出 outcome=pending 的实例。

        - observed_date：精确过滤某日
        - check_ready=True：仅返回 observed_date < today 的，表示「盘后可验证」
        """
        limit = max(1, min(int(limit), 2000))
        where = ["outcome = 'pending'"]
        params: list[Any] = []
        if observed_date is not None:
            od = _validate_iso_date("observed_date", observed_date)
            where.append("observed_date = ?")
            params.append(od)
        if check_ready:
            where.append("observed_date < date('now', 'localtime')")
        sql = (
            "SELECT * FROM cognition_instances WHERE "
            + " AND ".join(where)
            + " ORDER BY observed_date ASC, created_at ASC LIMIT ?"
        )
        params.append(limit)
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def validate_instance(
        self,
        instance_id: str,
        *,
        outcome: str,
        outcome_fact_source: str,
        outcome_detail: str | None = None,
        outcome_fact_refs_json: Any = None,
        outcome_date: str | None = None,
        lesson: str | None = None,
        input_by: str = "manual",
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        if outcome not in _VALIDATE_OUTCOMES:
            raise ValueError(
                f"outcome 非法，需为 {sorted(_VALIDATE_OUTCOMES)} 之一；收到: {outcome!r}"
            )
        fact_source = _validate_fact_source(outcome_fact_source)
        refs_s = _ensure_valid_json_str(outcome_fact_refs_json, "outcome_fact_refs_json")
        od = _validate_iso_date("outcome_date", outcome_date) if outcome_date else _today_iso()

        instance = self._get_instance(instance_id)

        with get_db(self.db_path) as conn:
            migrate(conn)
            # 查表校验事实源记录存在；失败则抛 ValueError，由 get_db 自动 rollback，
            # 保持 outcome 为 pending（即异常路径下不更新）
            _assert_fact_source_record_exists(conn, fact_source)
            conn.execute(
                """
                UPDATE cognition_instances
                SET outcome = ?, outcome_detail = ?, outcome_fact_source = ?,
                    outcome_fact_refs_json = ?, outcome_date = ?, lesson = ?
                WHERE instance_id = ?
                """,
                (
                    outcome,
                    outcome_detail,
                    fact_source,
                    refs_s,
                    od,
                    lesson,
                    instance_id,
                ),
            )

        updated = self._get_instance(instance_id)
        parent = self.get_cognition(instance["cognition_id"])
        return {
            "instance": updated,
            "cognition": {
                "cognition_id": parent["cognition_id"],
                "confidence": parent["confidence"],
                "validated_count": parent["validated_count"],
                "invalidated_count": parent["invalidated_count"],
                "instance_count": parent["instance_count"],
            },
        }

    def list_instances(
        self,
        *,
        cognition_id: str | None = None,
        outcome: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        teacher_id: int | None = None,
        source_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 2000))
        offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if cognition_id:
            where.append("cognition_id = ?")
            params.append(cognition_id)
        if outcome:
            if outcome not in _ALLOWED_INSTANCE_OUTCOMES:
                raise ValueError(
                    f"outcome 非法，需为 {sorted(_ALLOWED_INSTANCE_OUTCOMES)} 之一；"
                    f"收到: {outcome!r}"
                )
            where.append("outcome = ?")
            params.append(outcome)
        if date_from:
            where.append("observed_date >= ?")
            params.append(_validate_iso_date("date_from", date_from))
        if date_to:
            where.append("observed_date <= ?")
            params.append(_validate_iso_date("date_to", date_to))
        if teacher_id is not None:
            try:
                tid = int(teacher_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"teacher_id 需为整数，收到: {teacher_id!r}") from exc
            where.append("teacher_id = ?")
            params.append(tid)
        if source_type:
            st = str(source_type).strip()
            if not st:
                raise ValueError("source_type 不能为空串")
            # 既有 schema 未对 source_type 设白名单，此处保持与 add_instance 一致：
            # 只做非空校验，接受任意字符串。
            where.append("source_type = ?")
            params.append(st)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT * FROM cognition_instances"
            + where_sql
            + " ORDER BY observed_date DESC, created_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ============================================================
    # 三、periodic_reviews 管理
    # ============================================================
    def generate_review(
        self,
        *,
        period_type: str,
        period_start: str,
        period_end: str,
        review_scope: str = "calendar_period",
        regime_label: str | None = None,
        input_by: str = "manual",
    ) -> dict[str, Any]:
        """Phase 1b：只做聚合查询，不做 LLM 文本生成。

        聚合字段：
        - active_cognitions_json：期内 distinct cognition_id 列表
        - validation_stats_json：{"total": N, "by_outcome": {...}}
        - teacher_participation_json：{"by_teacher": [{teacher_id, count}], "teachers": [names]}
        """
        _validate_input_by(input_by)
        if period_type not in _ALLOWED_PERIOD_TYPES:
            raise ValueError(
                f"period_type 非法，需为 {sorted(_ALLOWED_PERIOD_TYPES)} 之一；"
                f"收到: {period_type!r}"
            )
        if review_scope not in _ALLOWED_REVIEW_SCOPES:
            raise ValueError(
                f"review_scope 非法，需为 {sorted(_ALLOWED_REVIEW_SCOPES)} 之一；"
                f"收到: {review_scope!r}"
            )
        ps = _validate_iso_date("period_start", period_start)
        pe = _validate_iso_date("period_end", period_end)
        if ps > pe:
            raise ValueError(f"period_start({ps}) 不能晚于 period_end({pe})")

        review_id = f"rev_{uuid4().hex[:8]}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            dup = conn.execute(
                """
                SELECT review_id FROM periodic_reviews
                WHERE period_type = ? AND period_start = ? AND period_end = ?
                """,
                (period_type, ps, pe),
            ).fetchone()
            if dup is not None:
                raise ValueError(
                    f"同 period_type+period_start+period_end 已存在 review: "
                    f"{dup['review_id']}"
                )

            # 期内实例聚合
            instance_rows = conn.execute(
                """
                SELECT cognition_id, outcome, teacher_id, teacher_name_snapshot
                FROM cognition_instances
                WHERE observed_date >= ? AND observed_date <= ?
                """,
                (ps, pe),
            ).fetchall()

            active_cognitions: list[str] = []
            seen_cog: set[str] = set()
            outcome_bucket: dict[str, int] = {}
            teacher_counts: dict[int, int] = {}
            teacher_names: dict[int, str] = {}
            anon_teacher_names: set[str] = set()
            total = 0
            for r in instance_rows:
                cog_id = r["cognition_id"]
                if cog_id and cog_id not in seen_cog:
                    seen_cog.add(cog_id)
                    active_cognitions.append(cog_id)
                outcome_bucket[r["outcome"]] = outcome_bucket.get(r["outcome"], 0) + 1
                total += 1
                tid = r["teacher_id"]
                if tid is not None:
                    teacher_counts[int(tid)] = teacher_counts.get(int(tid), 0) + 1
                    if r["teacher_name_snapshot"]:
                        teacher_names[int(tid)] = str(r["teacher_name_snapshot"])
                elif r["teacher_name_snapshot"]:
                    anon_teacher_names.add(str(r["teacher_name_snapshot"]))

            by_teacher = [
                {"teacher_id": tid, "count": cnt, "name": teacher_names.get(tid)}
                for tid, cnt in sorted(teacher_counts.items(), key=lambda x: (-x[1], x[0]))
            ]
            all_names = sorted({*teacher_names.values(), *anon_teacher_names})

            validation_stats = {"total": total, "by_outcome": outcome_bucket}
            teacher_participation = {"by_teacher": by_teacher, "teachers": all_names}

            conn.execute(
                """
                INSERT INTO periodic_reviews
                (review_id, period_type, review_scope, regime_label,
                 period_start, period_end,
                 active_cognitions_json, validation_stats_json, teacher_participation_json,
                 status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
                """,
                (
                    review_id,
                    period_type,
                    review_scope,
                    regime_label,
                    ps,
                    pe,
                    json.dumps(active_cognitions, ensure_ascii=False),
                    json.dumps(validation_stats, ensure_ascii=False),
                    json.dumps(teacher_participation, ensure_ascii=False),
                ),
            )
        return self.get_review(review_id)

    def get_review(self, review_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT * FROM periodic_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"review not found: {review_id}")
        return dict(row)

    def list_reviews(
        self,
        *,
        period_type: str | None = None,
        review_scope: str | None = None,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出周期复盘，支持按 period_type / review_scope / status / period_start 区间过滤。

        - `from_date / to_date`：区间过滤，逻辑为 `period_start >= from AND period_end <= to`。
        - 非法枚举或非法日期格式抛 `ValueError`。
        - 排序：`period_start DESC, generated_at DESC`。
        """
        limit = max(1, min(int(limit), 2000))
        offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if period_type:
            if period_type not in _ALLOWED_PERIOD_TYPES:
                raise ValueError(
                    f"period_type 非法，需为 {sorted(_ALLOWED_PERIOD_TYPES)} 之一；"
                    f"收到: {period_type!r}"
                )
            where.append("period_type = ?")
            params.append(period_type)
        if review_scope:
            if review_scope not in _ALLOWED_REVIEW_SCOPES:
                raise ValueError(
                    f"review_scope 非法，需为 {sorted(_ALLOWED_REVIEW_SCOPES)} 之一；"
                    f"收到: {review_scope!r}"
                )
            where.append("review_scope = ?")
            params.append(review_scope)
        if status:
            if status not in {"draft", "confirmed"}:
                raise ValueError(
                    f"status 非法，需为 ['draft', 'confirmed'] 之一；收到: {status!r}"
                )
            where.append("status = ?")
            params.append(status)
        if from_date:
            where.append("period_start >= ?")
            params.append(_validate_iso_date("from_date", from_date))
        if to_date:
            where.append("period_end <= ?")
            params.append(_validate_iso_date("to_date", to_date))

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            "SELECT * FROM periodic_reviews"
            + where_sql
            + " ORDER BY period_start DESC, generated_at DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def confirm_review(
        self,
        review_id: str,
        *,
        input_by: str,
        user_reflection: str | None = None,
        action_items_json: Any = None,
        key_lessons_json: Any = None,
        performance_notes: str | None = None,
    ) -> dict[str, Any]:
        _validate_input_by(input_by)
        current = self.get_review(review_id)
        if current.get("status") != "draft":
            raise ValueError(
                f"review 状态为 {current.get('status')}，仅 draft 可确认"
            )

        action_items_s = _ensure_valid_json_str(action_items_json, "action_items_json")
        key_lessons_s = _ensure_valid_json_str(key_lessons_json, "key_lessons_json")

        updates: list[tuple[str, Any]] = []
        if user_reflection is not None:
            updates.append(("user_reflection", user_reflection))
        if action_items_s is not None:
            updates.append(("action_items_json", action_items_s))
        if key_lessons_s is not None:
            updates.append(("key_lessons_json", key_lessons_s))
        if performance_notes is not None:
            updates.append(("performance_notes", performance_notes))

        with get_db(self.db_path) as conn:
            migrate(conn)
            sets = ["status = 'confirmed'", "confirmed_at = datetime('now')"]
            params: list[Any] = []
            for col, val in updates:
                sets.append(f"{col} = ?")
                params.append(val)
            sql = (
                "UPDATE periodic_reviews SET " + ", ".join(sets) + " WHERE review_id = ?"
            )
            conn.execute(sql, params + [review_id])
        return self.get_review(review_id)

    # ============================================================
    # 内部辅助
    # ============================================================
    def _get_instance(self, instance_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT * FROM cognition_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"instance not found: {instance_id}")
        return dict(row)


def _today_iso() -> str:
    return date_type.today().isoformat()
