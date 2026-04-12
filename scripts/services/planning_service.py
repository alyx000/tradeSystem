"""交易计划服务：Observation -> Draft -> Plan -> Review。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any
from uuid import uuid4

from db import queries as DbQ
from db.connection import get_db
from db.migrate import migrate


def _json_text(value: Any, default: Any) -> str:
    source = default if value is None else value
    return json.dumps(source, ensure_ascii=False)


@dataclass
class PlanningService:
    db_path: str | None = None
    registry: Any | None = None

    def create_observation(
        self,
        *,
        trade_date: str,
        source_type: str,
        title: str | None = None,
        market_facts: Any = None,
        sector_facts: Any = None,
        stock_facts: Any = None,
        judgements: Any = None,
        source_refs: Any = None,
        source_agent: str | None = None,
        created_by: str | None = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        observation_id = f"obs_{uuid4().hex[:12]}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO market_observations
                (observation_id, trade_date, source_type, title, market_facts_json, sector_facts_json,
                 stock_facts_json, judgements_json, source_refs_json, source_agent, created_by, input_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    trade_date,
                    source_type,
                    title,
                    _json_text(market_facts, {}),
                    _json_text(sector_facts, {}),
                    _json_text(stock_facts, {}),
                    _json_text(judgements, []),
                    _json_text(source_refs, []),
                    source_agent,
                    created_by,
                    input_by,
                ),
            )
        return self.get_observation(observation_id)

    def get_observation(self, observation_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT * FROM market_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"observation not found: {observation_id}")
        return dict(row)

    def list_observations(self, *, trade_date: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            if trade_date:
                rows = conn.execute(
                    """
                    SELECT * FROM market_observations
                    WHERE trade_date = ?
                    ORDER BY created_at DESC, observation_id DESC
                    LIMIT ?
                    """,
                    (trade_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM market_observations
                    ORDER BY created_at DESC, observation_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def update_observation(
        self,
        observation_id: str,
        *,
        title: str | None = None,
        market_facts: Any = None,
        sector_facts: Any = None,
        stock_facts: Any = None,
        judgements: Any = None,
        source_refs: Any = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        self.get_observation(observation_id)
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if market_facts is not None:
            updates["market_facts_json"] = _json_text(market_facts, {})
        if sector_facts is not None:
            updates["sector_facts_json"] = _json_text(sector_facts, {})
        if stock_facts is not None:
            updates["stock_facts_json"] = _json_text(stock_facts, {})
        if judgements is not None:
            updates["judgements_json"] = _json_text(judgements, [])
        if source_refs is not None:
            updates["source_refs_json"] = _json_text(source_refs, [])
        if input_by is not None:
            updates["input_by"] = input_by
        self._update_row("market_observations", "observation_id", observation_id, updates)
        return self.get_observation(observation_id)

    def create_draft(
        self,
        *,
        trade_date: str,
        source_observation_ids: list[str],
        title: str | None = None,
        summary: str | None = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        observations = self._load_observations(source_observation_ids)
        missing_observation_ids = [
            observation_id
            for observation_id in self._normalize_observation_ids(source_observation_ids)
            if observation_id not in {observation["observation_id"] for observation in observations}
        ]
        if missing_observation_ids:
            raise KeyError(f"observation not found: {', '.join(missing_observation_ids)}")
        if not observations:
            raise ValueError("至少需要一个 observation 来生成 draft")

        market_view = self._merge_market_view(observations)
        sector_view = self._merge_sector_view(observations)
        stock_focus = self._merge_stock_focus(observations)
        assumptions, ambiguities = self._merge_judgements(observations)

        draft_id = f"draft_{uuid4().hex[:12]}"
        draft_title = title or f"{trade_date} 次日计划草稿"
        draft_summary = summary or self._default_summary(market_view, sector_view)
        watch_items = self._default_watch_items(stock_focus)
        fact_candidates = self._default_fact_candidates(stock_focus)
        judgement_candidates = self._default_judgement_candidates(ambiguities)

        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO trade_drafts
                (draft_id, trade_date, title, summary, market_view_json, sector_view_json,
                 stock_focus_json, style_view_json, assumptions_json, ambiguities_json,
                 missing_fields_json, watch_items_json, fact_check_candidates_json,
                 judgement_check_candidates_json, source_observation_ids_json, status, input_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    trade_date,
                    draft_title,
                    draft_summary,
                    json.dumps(market_view, ensure_ascii=False),
                    json.dumps(sector_view, ensure_ascii=False),
                    json.dumps(stock_focus, ensure_ascii=False),
                    json.dumps({"focus_style": "趋势"}, ensure_ascii=False),
                    json.dumps(assumptions, ensure_ascii=False),
                    json.dumps(ambiguities, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    json.dumps(watch_items, ensure_ascii=False),
                    json.dumps(fact_candidates, ensure_ascii=False),
                    json.dumps(judgement_candidates, ensure_ascii=False),
                    json.dumps(source_observation_ids, ensure_ascii=False),
                    "ready_for_confirm",
                    input_by,
                ),
            )
        return self.get_draft(draft_id)

    def draft_from_review(
        self,
        *,
        review_date: str,
        trade_date: str | None = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            review = DbQ.get_daily_review(conn, review_date)
        if review is None:
            raise KeyError(f"review not found: {review_date}")

        target_trade_date = trade_date or self._guess_next_trade_date(review_date)
        step1_market = self._json_value(review.get("step1_market"), {})
        step2_sectors = self._json_value(review.get("step2_sectors"), {})

        market_facts = {
            "bias": self._review_market_bias(step1_market),
            "review_date": review_date,
        }
        sector_facts = {
            "main_themes": self._review_main_themes(step2_sectors),
        }
        stock_facts = self._review_stock_focus(step2_sectors)
        judgements = self._review_judgements(step1_market, step2_sectors)
        selection_summary = str(step2_sectors.get("selection_summary") or "").strip()

        observation = self.create_observation(
            trade_date=target_trade_date,
            source_type="review",
            title=f"{review_date} 复盘转次日草稿",
            market_facts=market_facts,
            sector_facts=sector_facts,
            stock_facts=stock_facts,
            judgements=judgements,
            source_refs=[
                {
                    "source_type": "review",
                    "review_date": review_date,
                    "selection_summary": selection_summary,
                }
            ],
            source_agent="review_workbench",
            created_by=input_by,
            input_by=input_by,
        )
        draft = self.create_draft(
            trade_date=target_trade_date,
            source_observation_ids=[observation["observation_id"]],
            title=f"{target_trade_date} 次日计划草稿",
            summary=selection_summary or None,
            input_by=input_by,
        )
        draft = self.update_draft(
            draft["draft_id"],
            summary=selection_summary or draft.get("summary"),
            fact_check_candidates=self._review_fact_candidates(step2_sectors),
            judgement_check_candidates=self._review_judgement_candidates(step2_sectors),
            input_by=input_by,
        )
        return {
            "review_date": review_date,
            "trade_date": target_trade_date,
            "observation": observation,
            "draft": draft,
        }

    def get_draft(self, draft_id: str | None = None, trade_date: str | None = None) -> dict[str, Any] | None:
        with get_db(self.db_path) as conn:
            migrate(conn)
            if draft_id:
                row = conn.execute(
                    "SELECT * FROM trade_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
            elif trade_date:
                row = conn.execute(
                    """
                    SELECT * FROM trade_drafts
                    WHERE trade_date = ?
                    ORDER BY created_at DESC, draft_id DESC
                    LIMIT 1
                    """,
                    (trade_date,),
                ).fetchone()
            else:
                row = None
        return dict(row) if row else None

    def list_drafts(self, *, trade_date: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            if trade_date:
                rows = conn.execute(
                    """
                    SELECT * FROM trade_drafts
                    WHERE trade_date = ?
                    ORDER BY created_at DESC, draft_id DESC
                    LIMIT ?
                    """,
                    (trade_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM trade_drafts
                    ORDER BY created_at DESC, draft_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def update_draft(
        self,
        draft_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        market_view: Any = None,
        sector_view: Any = None,
        stock_focus: Any = None,
        style_view: Any = None,
        assumptions: Any = None,
        ambiguities: Any = None,
        missing_fields: Any = None,
        watch_items: Any = None,
        fact_check_candidates: Any = None,
        judgement_check_candidates: Any = None,
        status: str | None = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        self.get_draft(draft_id=draft_id) or (_ for _ in ()).throw(KeyError(f"draft not found: {draft_id}"))
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if summary is not None:
            updates["summary"] = summary
        if market_view is not None:
            updates["market_view_json"] = _json_text(market_view, {})
        if sector_view is not None:
            updates["sector_view_json"] = _json_text(sector_view, {})
        if stock_focus is not None:
            updates["stock_focus_json"] = _json_text(stock_focus, [])
        if style_view is not None:
            updates["style_view_json"] = _json_text(style_view, {})
        if assumptions is not None:
            updates["assumptions_json"] = _json_text(assumptions, [])
        if ambiguities is not None:
            updates["ambiguities_json"] = _json_text(ambiguities, [])
        if missing_fields is not None:
            updates["missing_fields_json"] = _json_text(missing_fields, [])
        if watch_items is not None:
            updates["watch_items_json"] = _json_text(watch_items, [])
        if fact_check_candidates is not None:
            updates["fact_check_candidates_json"] = _json_text(fact_check_candidates, [])
        if judgement_check_candidates is not None:
            updates["judgement_check_candidates_json"] = _json_text(judgement_check_candidates, [])
        if status is not None:
            updates["status"] = status
        if input_by is not None:
            updates["input_by"] = input_by
        self._update_row("trade_drafts", "draft_id", draft_id, updates)
        return self.get_draft(draft_id=draft_id)  # type: ignore[return-value]

    def confirm_plan(
        self,
        *,
        draft_id: str,
        trade_date: str,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        draft = self.get_draft(draft_id=draft_id)
        if draft is None:
            raise KeyError(f"draft not found: {draft_id}")

        market_view = json.loads(draft["market_view_json"])
        sector_view = json.loads(draft["sector_view_json"])
        watch_items = self._merge_draft_candidates_into_watch_items(draft)
        plan_id = f"plan_{uuid4().hex[:12]}"

        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                "UPDATE trade_plans SET status = 'draft' WHERE trade_date = ? AND status = 'confirmed'",
                (trade_date,),
            )
            conn.execute(
                """
                INSERT INTO trade_plans
                (plan_id, trade_date, title, market_bias, main_themes_json, focus_style,
                 watch_items_json, risk_notes_json, invalidations_json, execution_notes_json,
                 source_draft_id, status, confirmed_by, input_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    trade_date,
                    draft["title"] or f"{trade_date} 交易计划",
                    market_view.get("bias", "混沌"),
                    json.dumps(sector_view.get("main_themes", []), ensure_ascii=False),
                    "趋势",
                    json.dumps(watch_items, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    draft_id,
                    "confirmed",
                    input_by,
                    input_by,
                ),
            )
        return self.get_plan(plan_id=plan_id)

    def get_plan(self, plan_id: str | None = None, trade_date: str | None = None) -> dict[str, Any] | None:
        with get_db(self.db_path) as conn:
            migrate(conn)
            if plan_id:
                row = conn.execute("SELECT * FROM trade_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            elif trade_date:
                row = conn.execute(
                    """
                    SELECT * FROM trade_plans
                    WHERE trade_date = ?
                    ORDER BY (status = 'confirmed') DESC, created_at DESC, plan_id DESC
                    LIMIT 1
                    """,
                    (trade_date,),
                ).fetchone()
            else:
                row = None
        return dict(row) if row else None

    def list_plans(self, *, trade_date: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            if trade_date:
                rows = conn.execute(
                    """
                    SELECT * FROM trade_plans
                    WHERE trade_date = ?
                    ORDER BY (status = 'confirmed') DESC, created_at DESC, plan_id DESC
                    LIMIT ?
                    """,
                    (trade_date, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM trade_plans
                    ORDER BY (status = 'confirmed') DESC, created_at DESC, plan_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def update_plan(
        self,
        plan_id: str,
        *,
        title: str | None = None,
        market_bias: str | None = None,
        main_themes: Any = None,
        focus_style: str | None = None,
        watch_items: Any = None,
        risk_notes: Any = None,
        invalidations: Any = None,
        execution_notes: Any = None,
        status: str | None = None,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        self.get_plan(plan_id=plan_id) or (_ for _ in ()).throw(KeyError(f"plan not found: {plan_id}"))
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if market_bias is not None:
            updates["market_bias"] = market_bias
        if main_themes is not None:
            updates["main_themes_json"] = _json_text(main_themes, [])
        if focus_style is not None:
            updates["focus_style"] = focus_style
        if watch_items is not None:
            updates["watch_items_json"] = _json_text(watch_items, [])
        if risk_notes is not None:
            updates["risk_notes_json"] = _json_text(risk_notes, [])
        if invalidations is not None:
            updates["invalidations_json"] = _json_text(invalidations, [])
        if execution_notes is not None:
            updates["execution_notes_json"] = _json_text(execution_notes, [])
        if status is not None:
            raise ValueError("status must be changed through confirm/review flow")
        if input_by is not None:
            updates["input_by"] = input_by
        self._update_row("trade_plans", "plan_id", plan_id, updates)
        return self.get_plan(plan_id=plan_id)  # type: ignore[return-value]

    def review_plan(
        self,
        *,
        plan_id: str,
        trade_date: str,
        outcome_summary: str,
        input_by: str | None = None,
    ) -> dict[str, Any]:
        plan = self.get_plan(plan_id=plan_id) or (_ for _ in ()).throw(KeyError(f"plan not found: {plan_id}"))
        if plan["trade_date"] != trade_date:
            raise ValueError("trade_date must match plan trade_date")
        review_id = f"plan_review_{uuid4().hex[:12]}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO plan_reviews
                (review_id, plan_id, trade_date, outcome_summary, market_result_json, theme_result_json,
                 watch_item_reviews_json, missed_points_json, lessons_json, input_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    plan_id,
                    trade_date,
                    outcome_summary,
                    json.dumps({}, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    input_by,
                ),
            )
            conn.execute(
                "UPDATE trade_plans SET status = 'reviewed', updated_at = datetime('now') WHERE plan_id = ?",
                (plan_id,),
            )
        return self.get_review(review_id)

    def diagnose_plan(
        self,
        *,
        plan_id: str | None = None,
        trade_date: str | None = None,
    ) -> dict[str, Any] | None:
        plan = self.get_plan(plan_id=plan_id, trade_date=trade_date)
        if plan is None:
            return None

        watch_items = json.loads(plan["watch_items_json"] or "[]")
        snapshot_map = self._load_snapshot_map(plan["trade_date"])
        diagnostics_items: list[dict[str, Any]] = []
        fact_check_count = 0
        judgement_check_count = 0
        data_ready_count = 0
        missing_data_count = 0
        unsupported_check_count = 0

        for item in watch_items:
            fact_results = []
            missing_dependencies: list[str] = []
            unsupported_checks: list[str] = []
            judgement_checks = item.get("judgement_checks", [])
            fact_checks = item.get("fact_checks", [])
            fact_check_count += len(fact_checks)
            judgement_check_count += len(judgement_checks)

            for check in fact_checks:
                result = self._evaluate_fact_check(check, snapshot_map, plan["trade_date"])
                fact_results.append(result)
                if result["result"] == "missing_data":
                    missing_dependencies.append(result["label"])
                elif result["result"] == "unsupported":
                    unsupported_checks.append(result["label"])

            data_ready = not missing_dependencies and not unsupported_checks
            if data_ready:
                data_ready_count += 1
            if missing_dependencies:
                missing_data_count += 1
            if unsupported_checks:
                unsupported_check_count += 1

            diagnostics_items.append(
                {
                    "subject_code": item.get("subject_code", ""),
                    "subject_name": item.get("subject_name", ""),
                    "data_ready": data_ready,
                    "fact_check_results": fact_results,
                    "missing_dependencies": missing_dependencies,
                    "unsupported_checks": unsupported_checks,
                    "notes": [],
                }
            )

        summary = {
            "ready_items": data_ready_count,
            "missing_data_items": missing_data_count,
            "unsupported_checks": unsupported_check_count,
        }

        return {
            "plan_id": plan["plan_id"],
            "trade_date": plan["trade_date"],
            "watch_item_count": len(watch_items),
            "fact_check_count": fact_check_count,
            "judgement_check_count": judgement_check_count,
            "data_ready_count": data_ready_count,
            "missing_data_count": missing_data_count,
            "unsupported_check_count": unsupported_check_count,
            "summary_json": summary,
            "items_json": diagnostics_items,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def get_review(self, review_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute("SELECT * FROM plan_reviews WHERE review_id = ?", (review_id,)).fetchone()
        if row is None:
            raise KeyError(f"review not found: {review_id}")
        return dict(row)

    def _load_observations(self, observation_ids: list[str]) -> list[dict[str, Any]]:
        normalized_ids = self._normalize_observation_ids(observation_ids)
        if not normalized_ids:
            return []
        placeholders = ", ".join("?" for _ in normalized_ids)
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                f"SELECT * FROM market_observations WHERE observation_id IN ({placeholders})",
                normalized_ids,
            ).fetchall()
        row_map = {row["observation_id"]: dict(row) for row in rows}
        return [row_map[observation_id] for observation_id in normalized_ids if observation_id in row_map]

    def _json_value(self, raw: Any, default: Any) -> Any:
        if raw is None:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return default
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return default
        return default

    def _guess_next_trade_date(self, review_date: str) -> str:
        start = datetime.strptime(review_date, "%Y-%m-%d") + timedelta(days=1)
        candidate = start
        known_trade_dates = self._load_future_trade_dates(review_date, limit=15)
        if known_trade_dates:
            return known_trade_dates[0]
        closure_dates = self._load_calendar_closure_dates(start.strftime("%Y-%m-%d"), 15)
        for _ in range(15):
            candidate_str = candidate.strftime("%Y-%m-%d")
            if self._is_trade_date(candidate_str, closure_dates):
                return candidate_str
            candidate += timedelta(days=1)
        return start.strftime("%Y-%m-%d")

    def _load_future_trade_dates(self, review_date: str, limit: int) -> list[str]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT date
                FROM daily_market
                WHERE date > ?
                ORDER BY date ASC
                LIMIT ?
                """,
                (review_date, limit),
            ).fetchall()
        return [str(row["date"]) for row in rows]

    def _load_calendar_closure_dates(self, date_from: str, days: int) -> set[str]:
        date_to = (datetime.strptime(date_from, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = DbQ.get_calendar_range(conn, date_from, date_to)
        closures: set[str] = set()
        for row in rows:
            haystack = " ".join(
                str(row.get(key) or "").lower()
                for key in ("event", "category", "note")
            )
            if any(token in haystack for token in ("休市", "闭市", "假期", "节假日", "holiday", "closed")):
                closures.add(str(row.get("date") or ""))
        return closures

    def _is_trade_date(self, candidate: str, closure_dates: set[str]) -> bool:
        if candidate in closure_dates:
            return False
        candidate_dt = datetime.strptime(candidate, "%Y-%m-%d")
        if candidate_dt.weekday() >= 5:
            return False
        if self.registry is None:
            return True
        result = self.registry.call("is_trade_day", candidate)
        if result.success and result.data is not None:
            return bool(result.data)
        return True

    def _review_market_bias(self, step1_market: dict[str, Any]) -> str:
        direction = step1_market.get("direction")
        if isinstance(direction, dict):
            trend = str(direction.get("trend") or "").strip()
            if trend:
                return trend
        node = step1_market.get("node")
        if isinstance(node, dict):
            current = str(node.get("current") or "").strip()
            if current:
                return current
        return "混沌"

    def _review_main_themes(self, step2_sectors: dict[str, Any]) -> list[str]:
        themes: list[str] = []
        main_theme = step2_sectors.get("main_theme")
        if isinstance(main_theme, dict):
            name = str(main_theme.get("name") or "").strip()
            if name and name not in themes:
                themes.append(name)
        for item in self._review_focus_items(step2_sectors):
            sector_name = str(item.get("sector_name") or "").strip()
            if sector_name and sector_name not in themes:
                themes.append(sector_name)
        return themes

    def _review_stock_focus(self, step2_sectors: dict[str, Any]) -> list[dict[str, Any]]:
        stock_focus: list[dict[str, Any]] = [
            {
                "subject_type": "market",
                "subject_code": "",
                "subject_name": "A股市场",
                "reason": "复盘转计划的大势锚定",
            }
        ]
        seen: set[tuple[str, str]] = {("market", "A股市场")}
        selection_summary = str(step2_sectors.get("selection_summary") or "").strip()
        for item in self._review_focus_items(step2_sectors):
            sector_name = str(item.get("sector_name") or "").strip()
            if sector_name:
                sector_key = ("sector", sector_name)
                if sector_key not in seen:
                    stock_focus.append(
                        {
                            "subject_type": "sector",
                            "subject_code": "",
                            "subject_name": sector_name,
                            "reason": str(item.get("focus_reason") or selection_summary or "复盘次日聚焦").strip(),
                        }
                    )
                    seen.add(sector_key)

            for stock in self._coerce_string_list(item.get("key_stocks")):
                stock_key = ("stock", stock)
                if stock_key in seen:
                    continue
                stock_focus.append(
                    {
                        "subject_type": "stock",
                        "subject_code": "",
                        "subject_name": stock,
                        "reason": f"{sector_name or '板块'} 核心票",
                    }
                )
                seen.add(stock_key)
        return stock_focus

    def _review_judgements(
        self,
        step1_market: dict[str, Any],
        step2_sectors: dict[str, Any],
    ) -> list[dict[str, Any]]:
        judgements: list[dict[str, Any]] = []
        market_bias = self._review_market_bias(step1_market)
        if market_bias and market_bias != "混沌":
            judgements.append(
                {
                    "kind": "market_bias",
                    "market_bias": market_bias,
                    "text": f"大势偏向：{market_bias}",
                }
            )

        projections = step2_sectors.get("projections")
        if isinstance(projections, list):
            for item in projections:
                if not isinstance(item, dict):
                    continue
                sector_name = str(item.get("sector_name") or "").strip()
                if not sector_name:
                    continue
                projection_judgement = {
                    "kind": "sector_projection",
                    "sector_name": sector_name,
                    "sector_type": str(item.get("sector_type") or "").strip(),
                    "big_cycle_stage": str(item.get("big_cycle_stage") or "").strip(),
                    "connection_bias": str(item.get("connection_bias") or "").strip(),
                    "market_fit": str(item.get("market_fit") or "").strip(),
                    "role_expectation": str(item.get("role_expectation") or "").strip(),
                    "return_flow_view": str(item.get("return_flow_view") or "").strip(),
                    "fully_priced_risk": str(item.get("fully_priced_risk") or "").strip(),
                    "logic_aesthetic": str(item.get("logic_aesthetic") or "").strip(),
                    "judgement_notes": str(item.get("judgement_notes") or "").strip(),
                    "key_stocks": self._coerce_string_list(item.get("key_stocks")),
                    "supporting_facts": self._coerce_string_list(item.get("supporting_facts")),
                }
                parts = []
                for key, label in (
                    ("big_cycle_stage", "阶段"),
                    ("connection_bias", "连接点"),
                    ("market_fit", "与大势匹配"),
                    ("return_flow_view", "回流预期"),
                    ("fully_priced_risk", "充分演绎风险"),
                ):
                    value = str(projection_judgement.get(key) or "").strip()
                    if value:
                        parts.append(f"{label}={value}")
                if projection_judgement["logic_aesthetic"]:
                    parts.append(f"逻辑审美={projection_judgement['logic_aesthetic']}")
                if projection_judgement["judgement_notes"]:
                    parts.append(f"备注={projection_judgement['judgement_notes']}")
                if parts:
                    projection_judgement["text"] = f"{sector_name}：" + "，".join(parts)
                else:
                    projection_judgement["text"] = sector_name
                judgements.append(projection_judgement)

        selection_summary = str(step2_sectors.get("selection_summary") or "").strip()
        if selection_summary:
            judgements.append(
                {
                    "kind": "selection_summary",
                    "text": f"次日聚焦：{selection_summary}",
                    "summary": selection_summary,
                }
            )
        return judgements

    def _review_fact_candidates(self, step2_sectors: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = [
            {
                "subject_type": "market",
                "subject_code": "",
                "subject_name": "A股市场",
                "check_type": "market_amount_gte_prev_day",
                "label": "成交额不低于前一日",
                "params": {},
            }
        ]
        seen: set[tuple[str, str]] = set()
        for item in self._review_focus_items(step2_sectors):
            sector_name = str(item.get("sector_name") or "").strip()
            if not sector_name or sector_name in seen:
                continue
            seen.add(sector_name)
            candidates.append(
                {
                    "subject_type": "sector",
                    "subject_code": "",
                    "subject_name": sector_name,
                    "check_type": "sector_change_positive",
                    "label": f"{sector_name} 涨幅保持为正",
                    "params": {"sector_name": sector_name},
                }
            )
            candidates.append(
                {
                    "subject_type": "sector",
                    "subject_code": "",
                    "subject_name": sector_name,
                    "check_type": "sector_limit_up_count_gte",
                    "label": f"{sector_name} 至少有 1 家涨停",
                    "params": {"sector_name": sector_name, "value": 1},
                }
            )
        return candidates

    def _review_judgement_candidates(self, step2_sectors: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        projections = step2_sectors.get("projections")
        if not isinstance(projections, list):
            return candidates
        for item in projections:
            if not isinstance(item, dict):
                continue
            sector_name = str(item.get("sector_name") or "").strip()
            if not sector_name:
                continue
            for key, label in (
                ("connection_bias", "连接点判断"),
                ("market_fit", "与大势匹配度"),
                ("return_flow_view", "回流预期"),
                ("fully_priced_risk", "充分演绎风险"),
            ):
                value = str(item.get(key) or "").strip()
                if value:
                    candidates.append(
                        {
                            "subject_type": "sector",
                            "subject_code": "",
                            "subject_name": sector_name,
                            "label": f"{sector_name}{label}：{value}",
                            "notes": "需人工判断",
                        }
                    )
            logic_aesthetic = str(item.get("logic_aesthetic") or "").strip()
            if logic_aesthetic:
                candidates.append(
                    {
                        "subject_type": "sector",
                        "subject_code": "",
                        "subject_name": sector_name,
                        "label": f"{sector_name}逻辑审美是否成立",
                        "notes": logic_aesthetic,
                    }
                )
        return candidates

    def _review_focus_items(self, step2_sectors: dict[str, Any]) -> list[dict[str, Any]]:
        focus_items = step2_sectors.get("next_day_focus")
        if isinstance(focus_items, list) and focus_items:
            return [item for item in focus_items if isinstance(item, dict)]

        fallback_items: list[dict[str, Any]] = []
        projections = step2_sectors.get("projections")
        if isinstance(projections, list):
            for item in projections:
                if not isinstance(item, dict):
                    continue
                sector_name = str(item.get("sector_name") or "").strip()
                if not sector_name:
                    continue
                fallback_items.append(
                    {
                        "sector_name": sector_name,
                        "key_stocks": item.get("key_stocks") or [],
                        "focus_reason": item.get("judgement_notes") or item.get("logic_aesthetic") or "",
                    }
                )
        return fallback_items

    def _coerce_string_list(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [part.strip() for part in stripped.replace("、", ",").replace("，", ",").split(",") if part.strip()]
        return []

    def _normalize_observation_ids(self, observation_ids: list[str]) -> list[str]:
        normalized: list[str] = []
        for observation_id in observation_ids:
            if observation_id and observation_id not in normalized:
                normalized.append(observation_id)
        return normalized

    def _update_row(self, table: str, id_column: str, id_value: str, updates: dict[str, Any]) -> None:
        if not updates:
            return
        updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
        fields = ", ".join(f"{column} = ?" for column in updates.keys())
        values = list(updates.values()) + [id_value]
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(f"UPDATE {table} SET {fields} WHERE {id_column} = ?", values)

    def _load_snapshot_map(self, trade_date: str) -> dict[tuple[str, str, str], dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT fact_type, subject_type, COALESCE(subject_code, '') AS subject_code, facts_json
                FROM market_fact_snapshots
                WHERE biz_date = ?
                """,
                (trade_date,),
            ).fetchall()
        mapping: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            mapping[(row["fact_type"], row["subject_type"], row["subject_code"])] = json.loads(row["facts_json"])
        return mapping

    def _load_daily_market_context(self, trade_date: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            current = DbQ.get_daily_market(conn, trade_date)
            previous = DbQ.get_prev_daily_market(conn, trade_date)
        return self._enrich_daily_market_row(current), self._enrich_daily_market_row(previous)

    def _load_recent_trade_dates(self, trade_date: str, limit: int) -> list[str]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT date
                FROM daily_market
                WHERE date <= ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (trade_date, limit),
            ).fetchall()
        return [str(row["date"]) for row in rows]

    def _enrich_daily_market_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        raw = row.get("raw_data")
        if not raw:
            return row
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return row
        inner = parsed.get("raw_data") or {}
        if not isinstance(inner, dict):
            inner = {}
        for key in (
            "sector_industry",
            "sector_concept",
            "sector_fund_flow",
            "indices",
            "style_factors",
            "sector_rhythm_industry",
            "sector_rhythm_concept",
        ):
            if key in parsed:
                row[key] = parsed[key]
            elif key in inner:
                row[key] = inner[key]
        return row

    def _find_sector_row(self, daily_market: dict[str, Any] | None, sector_name: str) -> dict[str, Any] | None:
        if daily_market is None or not sector_name:
            return None
        candidates: list[dict[str, Any]] = []
        for key in ("sector_industry", "sector_concept", "sector_rhythm_industry", "sector_rhythm_concept"):
            rows = daily_market.get(key) or []
            if isinstance(rows, list):
                candidates.extend([row for row in rows if isinstance(row, dict)])
        normalized = sector_name.strip()
        for row in candidates:
            names = {
                str(row.get("sector_name", "")).strip(),
                str(row.get("name", "")).strip(),
                str(row.get("sector", "")).strip(),
                str(row.get("label", "")).strip(),
            }
            if normalized in names:
                return row
        return None

    def _extract_numeric(self, row: dict[str, Any] | None, *keys: str) -> float | None:
        if row is None:
            return None
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _evaluate_fact_check(
        self,
        check: dict[str, Any],
        snapshot_map: dict[tuple[str, str, str], dict[str, Any]],
        trade_date: str,
    ) -> dict[str, Any]:
        check_type = check.get("check_type", "")
        label = check.get("label", check_type)
        params = check.get("params", {}) or {}

        if check_type == "northbound_net_positive":
            facts = snapshot_map.get(("capital_flow", "market", "CN"))
            if facts is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            value = facts.get("northbound_net_buy_billion")
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if value is not None and value > 0 else "fail",
                "evidence_json": {"northbound_net_buy_billion": value},
            }

        if check_type == "margin_balance_change_positive":
            facts = snapshot_map.get(("margin_stats", "market", "CN"))
            if facts is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            value = facts.get("total_rzrqye_yi")
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if value is not None and value > 0 else "fail",
                "evidence_json": {"total_rzrqye_yi": value},
            }

        if check_type in {"price_above_ma5", "price_above_ma10", "price_above_ma20"}:
            code = params.get("ts_code")
            period = int(check_type.replace("price_above_ma", ""))
            if not code:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            if self.registry is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            target_date = params.get("trade_date") or trade_date
            daily = self.registry.call("get_stock_daily", code, target_date)
            ma = self.registry.call("get_stock_ma", code, target_date, [period])
            if not daily.success or not ma.success:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            close = (daily.data or {}).get("close")
            ma_value = (ma.data or {}).get(f"ma{period}")
            if close is None or ma_value is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if float(close) >= float(ma_value) else "fail",
                "evidence_json": {"close": close, f"ma{period}": ma_value},
            }

        if check_type == "ret_1d_gte":
            code = params.get("ts_code")
            threshold = float(params.get("value", 0))
            if not code or self.registry is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            target_date = params.get("trade_date") or trade_date
            daily = self.registry.call("get_stock_daily", code, target_date)
            if not daily.success:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            change_pct = (daily.data or {}).get("change_pct")
            if change_pct is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if float(change_pct) >= threshold else "fail",
                "evidence_json": {"change_pct": change_pct, "threshold": threshold},
            }

        if check_type == "announcement_exists":
            code = params.get("ts_code")
            if not code or self.registry is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            start_date = params.get("start_date") or params.get("trade_date") or trade_date
            end_date = params.get("end_date") or params.get("trade_date") or start_date
            anns = self.registry.call("get_stock_announcements", code, start_date, end_date)
            if not anns.success:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            count = len(anns.data or [])
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if count > 0 else "fail",
                "evidence_json": {"announcement_count": count},
            }

        if check_type == "market_amount_gte_prev_day":
            current, previous = self._load_daily_market_context(trade_date)
            current_amount = self._extract_numeric(current, "total_amount")
            previous_amount = self._extract_numeric(previous, "total_amount")
            if current_amount is None or previous_amount is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if current_amount >= previous_amount else "fail",
                "evidence_json": {"current_total_amount": current_amount, "previous_total_amount": previous_amount},
            }

        if check_type == "sector_change_positive":
            sector_name = params.get("sector_name") or params.get("name") or ""
            current, _previous = self._load_daily_market_context(trade_date)
            sector_row = self._find_sector_row(current, sector_name)
            change_pct = self._extract_numeric(sector_row, "change_pct", "pct_chg", "pct", "change")
            if change_pct is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if change_pct > 0 else "fail",
                "evidence_json": {"sector_name": sector_name, "change_pct": change_pct},
            }

        if check_type == "sector_limit_up_count_gte":
            sector_name = params.get("sector_name") or params.get("name") or ""
            threshold = float(params.get("value", 0))
            current, _previous = self._load_daily_market_context(trade_date)
            sector_row = self._find_sector_row(current, sector_name)
            limit_up_count = self._extract_numeric(
                sector_row,
                "limit_up_count",
                "limit_ups",
                "涨停家数",
                "count_limit_up",
            )
            if limit_up_count is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if limit_up_count >= threshold else "fail",
                "evidence_json": {
                    "sector_name": sector_name,
                    "limit_up_count": limit_up_count,
                    "threshold": threshold,
                },
            }

        if check_type == "ret_5d_gte":
            code = params.get("ts_code")
            threshold = float(params.get("value", 0))
            if not code or self.registry is None:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            trade_dates = self._load_recent_trade_dates(params.get("trade_date") or trade_date, 5)
            if len(trade_dates) < 5:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            latest = self.registry.call("get_stock_daily", code, trade_dates[0])
            baseline = self.registry.call("get_stock_daily", code, trade_dates[-1])
            if not latest.success or not baseline.success:
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            latest_close = (latest.data or {}).get("close")
            baseline_close = (baseline.data or {}).get("close")
            if latest_close in (None, 0) or baseline_close in (None, 0):
                return {"check_type": check_type, "label": label, "result": "missing_data", "evidence_json": {}}
            ret_5d = (float(latest_close) / float(baseline_close) - 1.0) * 100
            return {
                "check_type": check_type,
                "label": label,
                "result": "pass" if ret_5d >= threshold else "fail",
                "evidence_json": {
                    "ret_5d": round(ret_5d, 2),
                    "threshold": threshold,
                    "latest_close": latest_close,
                    "baseline_close": baseline_close,
                    "window_dates": list(reversed(trade_dates)),
                },
            }

        return {
            "check_type": check_type,
            "label": label,
            "result": "unsupported",
            "evidence_json": {},
        }

    def _merge_market_view(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        bias = "混沌"
        notes: list[str] = []
        for obs in observations:
            facts = json.loads(obs["market_facts_json"] or "{}")
            judgements = json.loads(obs["judgements_json"] or "[]")
            if isinstance(facts, dict) and facts.get("bias"):
                bias = facts["bias"]
            for item in judgements if isinstance(judgements, list) else []:
                if isinstance(item, dict) and item.get("market_bias"):
                    bias = item["market_bias"]
                elif isinstance(item, str):
                    notes.append(item)
        return {"bias": bias, "notes": notes}

    def _merge_sector_view(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        main_themes: list[str] = []
        for obs in observations:
            sector_facts = json.loads(obs["sector_facts_json"] or "{}")
            if isinstance(sector_facts, dict):
                for theme in sector_facts.get("main_themes", []):
                    if theme not in main_themes:
                        main_themes.append(theme)
        return {"main_themes": main_themes}

    def _merge_stock_focus(self, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        merged: list[dict[str, Any]] = []
        for obs in observations:
            stock_facts = json.loads(obs["stock_facts_json"] or "[]")
            rows = stock_facts if isinstance(stock_facts, list) else stock_facts.get("stocks", [])
            for item in rows:
                if not isinstance(item, dict):
                    continue
                key = self._subject_identity(item)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "subject_type": item.get("subject_type", "stock"),
                        "subject_code": item.get("subject_code", ""),
                        "subject_name": item.get("subject_name", ""),
                        "reason": item.get("reason", "来自 observation 聚合"),
                    }
                )
        return merged

    def _merge_judgements(self, observations: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        assumptions: list[str] = []
        ambiguities: list[str] = []
        for obs in observations:
            judgements = json.loads(obs["judgements_json"] or "[]")
            rows = judgements if isinstance(judgements, list) else [judgements]
            for item in rows:
                if isinstance(item, str):
                    if item not in assumptions:
                        assumptions.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("label")
                    if text and text not in assumptions:
                        assumptions.append(text)
                    if item.get("ambiguous") and text and text not in ambiguities:
                        ambiguities.append(text)
        return assumptions, ambiguities

    def _subject_identity(self, item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("subject_type", "stock") or "stock"),
            str(item.get("subject_code", "") or ""),
            str(item.get("subject_name", "") or ""),
        )

    def _default_summary(self, market_view: dict[str, Any], sector_view: dict[str, Any]) -> str:
        themes = "、".join(sector_view.get("main_themes", [])) or "暂无明确主线"
        return f"市场偏向{market_view.get('bias', '混沌')}，重点关注 {themes}。"

    def _default_watch_items(self, stock_focus: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "subject_type": item.get("subject_type", "stock"),
                "subject_code": item.get("subject_code", ""),
                "subject_name": item.get("subject_name", ""),
                "reason": item.get("reason", ""),
                "fact_checks": [],
                "judgement_checks": [],
                "trigger_conditions": [],
                "invalidations": [],
                "priority": index + 1,
            }
            for index, item in enumerate(stock_focus)
        ]

    def _default_fact_candidates(self, stock_focus: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in stock_focus:
            code = item.get("subject_code")
            if code:
                candidates.append(
                    {
                        "subject_type": item.get("subject_type", "stock"),
                        "subject_code": code,
                        "subject_name": item.get("subject_name", ""),
                        "check_type": "price_above_ma20",
                        "label": "站稳20日线",
                        "params": {"ts_code": code},
                    }
                )
        return candidates

    def _default_judgement_candidates(self, ambiguities: list[str]) -> list[dict[str, Any]]:
        return [{"label": text, "notes": "需人工确认"} for text in ambiguities]

    def _build_watch_items(
        self,
        stock_focus: list[dict[str, Any]],
        fact_candidates: list[dict[str, Any]],
        judgement_candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_subject: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for candidate in fact_candidates:
            identity = self._candidate_identity(candidate)
            by_subject.setdefault(identity, []).append(
                {
                    "check_type": candidate["check_type"],
                    "label": candidate["label"],
                    "params": candidate["params"],
                }
            )

        watch_items: list[dict[str, Any]] = []
        for index, item in enumerate(stock_focus):
            identity = self._watch_item_identity(item)
            watch_items.append(
                {
                    "subject_type": item.get("subject_type", "stock"),
                    "subject_code": item.get("subject_code", ""),
                    "subject_name": item.get("subject_name", ""),
                    "reason": item.get("reason", ""),
                    "fact_checks": by_subject.get(identity, []),
                    "judgement_checks": judgement_candidates if index == 0 else [],
                    "trigger_conditions": [],
                    "invalidations": [],
                    "priority": index + 1,
                }
            )
        return watch_items

    def _merge_draft_candidates_into_watch_items(self, draft: dict[str, Any]) -> list[dict[str, Any]]:
        watch_items = json.loads(draft["watch_items_json"] or "[]")
        fact_candidates = json.loads(draft["fact_check_candidates_json"] or "[]")
        judgement_candidates = json.loads(draft["judgement_check_candidates_json"] or "[]")
        if not watch_items:
            return watch_items

        by_subject: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        general_fact_candidates: list[dict[str, Any]] = []
        for candidate in fact_candidates:
            identity = self._candidate_identity(candidate)
            target = (
                by_subject.setdefault(identity, [])
                if identity != ("stock", "", "")
                else general_fact_candidates
            )
            target.append(
                {
                    "check_type": candidate["check_type"],
                    "label": candidate["label"],
                    "params": candidate.get("params", {}),
                }
            )

        by_judgement_subject: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        general_judgement_candidates: list[dict[str, Any]] = []
        for candidate in judgement_candidates:
            identity = self._candidate_identity(candidate)
            target = (
                by_judgement_subject.setdefault(identity, [])
                if identity != ("stock", "", "")
                else general_judgement_candidates
            )
            target.append(candidate)

        merged_watch_items: list[dict[str, Any]] = []
        for index, item in enumerate(watch_items):
            merged_item = dict(item)
            existing_fact_checks = merged_item.get("fact_checks") or []
            watch_id = self._watch_item_identity(merged_item)
            matched_fact_candidates: list[dict[str, Any]] = []
            for cand_id, pool in by_subject.items():
                if self._identity_matches(watch_id, cand_id):
                    matched_fact_candidates.extend(pool)
            if index == 0:
                matched_fact_candidates.extend(general_fact_candidates)
            for candidate in matched_fact_candidates:
                    duplicate = any(
                        existing.get("check_type") == candidate["check_type"]
                        and existing.get("params", {}) == candidate.get("params", {})
                        for existing in existing_fact_checks
                    )
                    if not duplicate:
                        existing_fact_checks.append(candidate)
            merged_item["fact_checks"] = existing_fact_checks

            existing_judgement_checks = merged_item.get("judgement_checks") or []
            matched_judgement_candidates: list[dict[str, Any]] = []
            for cand_id, pool in by_judgement_subject.items():
                if self._identity_matches(watch_id, cand_id):
                    matched_judgement_candidates.extend(pool)
            if index == 0:
                matched_judgement_candidates.extend(general_judgement_candidates)
            for pool in [matched_judgement_candidates]:
                for candidate in pool:
                    duplicate = any(
                        existing.get("label") == candidate.get("label")
                        and existing.get("notes", "") == candidate.get("notes", "")
                        for existing in existing_judgement_checks
                    )
                    if not duplicate:
                        existing_judgement_checks.append(
                            {
                                "label": candidate.get("label", ""),
                                "notes": candidate.get("notes", ""),
                            }
                        )
            merged_item["judgement_checks"] = existing_judgement_checks
            merged_watch_items.append(merged_item)
        return merged_watch_items

    def _watch_item_identity(self, item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("subject_type") or "stock"),
            str(item.get("subject_code") or ""),
            str(item.get("subject_name") or ""),
        )

    def _candidate_identity(self, candidate: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(candidate.get("subject_type") or "stock"),
            str(candidate.get("subject_code") or ""),
            str(candidate.get("subject_name") or ""),
        )

    def _identity_matches(self, watch_identity: tuple[str, str, str], candidate_identity: tuple[str, str, str]) -> bool:
        if watch_identity == candidate_identity:
            return True
        if watch_identity[0] == candidate_identity[0] and watch_identity[1] and watch_identity[1] == candidate_identity[1]:
            if not candidate_identity[2] or not watch_identity[2]:
                return True
        return False
