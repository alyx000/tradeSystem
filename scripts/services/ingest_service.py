"""采集底座服务：围绕接口注册表和原始事实层审计。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any
from uuid import uuid4

from db.connection import get_db
from db.migrate import migrate
from ingest.labels import (
    bool_label,
    error_type_label,
    health_status_label,
    health_status_reason,
    provider_label,
    remediation_hint,
    restriction_label,
    restriction_reason,
    retryable_label,
    short_interface_meaning,
    stage_label,
    status_label,
)
from ingest.registry import get_interface, list_interfaces
from providers.base import DataResult


@dataclass
class IngestService:
    db_path: str | None = None
    registry: Any | None = None

    def _classify_provider_error(self, error_message: str) -> tuple[str, int]:
        msg = str(error_message or "")
        if any(token in msg for token in ("权限不足", "积分不足", "token不对", "未配置 token", "未配置")):
            return "provider", 0
        return "provider", 1

    def list_interfaces(self) -> list[dict[str, Any]]:
        return [self._enrich_interface_record(dict(item)) for item in list_interfaces()]

    def _interface_meta(self, interface_name: str | None) -> dict[str, Any]:
        if not interface_name:
            return {}
        return dict(get_interface(interface_name) or {})

    def _enrich_interface_record(self, item: dict[str, Any]) -> dict[str, Any]:
        note = item.get("notes")
        stage = item.get("stage")
        enabled_by_default = item.get("enabled_by_default")
        return {
            **item,
            "interface_label": short_interface_meaning(item.get("interface_name"), note),
            "stage_label": stage_label(stage),
            "enabled_by_default_label": bool_label(enabled_by_default)
            if enabled_by_default is not None
            else None,
        }

    def _enrich_run_record(self, item: dict[str, Any]) -> dict[str, Any]:
        meta = self._interface_meta(item.get("interface_name"))
        note = meta.get("notes")
        stage = item.get("stage") or meta.get("stage")
        return {
            **item,
            "interface_note": note,
            "interface_label": short_interface_meaning(item.get("interface_name"), note),
            "stage_label": stage_label(stage),
            "status_label": status_label(item.get("status")),
            "provider_label": provider_label(item.get("provider")),
        }

    def _enrich_error_record(self, item: dict[str, Any]) -> dict[str, Any]:
        meta = self._interface_meta(item.get("interface_name"))
        note = meta.get("notes")
        stage = item.get("stage") or meta.get("stage")
        return {
            **item,
            "interface_note": note,
            "interface_label": short_interface_meaning(item.get("interface_name"), note),
            "stage_label": stage_label(stage),
            "error_type_label": error_type_label(item.get("error_type")),
            "retryable_label": retryable_label(item.get("retryable")),
            "restriction_label": restriction_label(item.get("error_message")),
            "restriction_reason": restriction_reason(item.get("error_message")),
            "action_hint": remediation_hint(
                error_type=item.get("error_type"),
                error_message=item.get("error_message"),
                retryable=item.get("retryable"),
            ),
        }

    def _enrich_retry_group(self, item: dict[str, Any]) -> dict[str, Any]:
        meta = self._interface_meta(item.get("interface_name"))
        note = meta.get("notes")
        stage = item.get("stage") or meta.get("stage")
        return {
            **item,
            "interface_note": note,
            "interface_label": short_interface_meaning(item.get("interface_name"), note),
            "stage_label": stage_label(stage),
        }

    def inspect(
        self,
        target_date: str,
        interface_name: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            run_where = "biz_date = ?"
            run_params: list[Any] = [target_date]
            error_where = "biz_date = ?"
            error_params: list[Any] = [target_date]
            if interface_name:
                run_where += " AND interface_name = ?"
                run_params.append(interface_name)
                error_where += " AND interface_name = ?"
                error_params.append(interface_name)
            if stage:
                run_where += " AND stage = ?"
                run_params.append(stage)
                error_where += " AND stage = ?"
                error_params.append(stage)
            runs = [
                self._enrich_run_record(dict(row))
                for row in conn.execute(
                    f"""
                    SELECT run_id, interface_name, provider, stage, biz_date, target_date,
                           status, row_count, started_at, finished_at, duration_ms,
                           triggered_by, input_by, notes
                    FROM ingest_runs
                    WHERE {run_where}
                    ORDER BY started_at DESC, run_id DESC
                    """,
                    tuple(run_params),
                ).fetchall()
            ]
            errors = [
                self._enrich_error_record(dict(row))
                for row in conn.execute(
                    f"""
                    SELECT id, run_id, interface_name, biz_date, stage, error_type,
                           error_message, retryable, created_at, resolved_at
                    FROM ingest_errors
                    WHERE {error_where}
                    ORDER BY created_at DESC, id DESC
                    """,
                    tuple(error_params),
                ).fetchall()
            ]
        return {
            "date": target_date,
            "interface_name": interface_name,
            "stage": stage,
            "run_count": len(runs),
            "error_count": len(errors),
            "runs": runs,
            "errors": errors,
        }

    def health_summary(
        self,
        *,
        end_date: str,
        days: int = 7,
        limit: int = 10,
        stage: str | None = None,
        interface_name: str | None = None,
    ) -> dict[str, Any]:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=max(days - 1, 0))).strftime("%Y-%m-%d")
        with get_db(self.db_path) as conn:
            migrate(conn)
            failed_where = "e.biz_date BETWEEN ? AND ?"
            failed_params: list[Any] = [start_date, end_date]
            success_stage_clause = ""
            success_interface_clause = ""
            if stage:
                failed_where += " AND e.stage = ?"
                failed_params.append(stage)
                success_stage_clause = "AND r.stage = ?"
            if interface_name:
                failed_where += " AND e.interface_name = ?"
                failed_params.append(interface_name)
                success_interface_clause = "AND r.interface_name = ?"
            failed_query_params: list[Any] = []
            if stage:
                failed_query_params.append(stage)
            if interface_name:
                failed_query_params.append(interface_name)
            failed_query_params.append(end_date)
            failed_query_params.extend(failed_params)
            failed_groups = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT
                        e.interface_name,
                        COUNT(*) AS failure_count,
                        SUM(CASE WHEN e.resolved_at IS NULL THEN 1 ELSE 0 END) AS unresolved_count,
                        GROUP_CONCAT(DISTINCT e.biz_date) AS failure_dates_csv,
                        MAX(e.biz_date) AS last_failure_biz_date,
                        (
                            SELECT MAX(r.biz_date)
                            FROM ingest_runs r
                            WHERE r.interface_name = e.interface_name
                              AND r.status = 'success'
                              {success_stage_clause}
                              {success_interface_clause}
                              AND r.biz_date <= ?
                        ) AS last_success_biz_date
                    FROM ingest_errors e
                    WHERE {failed_where}
                    GROUP BY e.interface_name
                    ORDER BY failure_count DESC, unresolved_count DESC, e.interface_name ASC
                    """,
                    tuple(failed_query_params),
                ).fetchall()
            ]
            daily_where = "biz_date BETWEEN ? AND ?"
            daily_params: list[Any] = [start_date, end_date]
            if stage:
                daily_where += " AND stage = ?"
                daily_params.append(stage)
            if interface_name:
                daily_where += " AND interface_name = ?"
                daily_params.append(interface_name)
            daily_rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT biz_date, COUNT(*) AS error_count
                    FROM ingest_errors
                    WHERE {daily_where}
                    GROUP BY biz_date
                    ORDER BY biz_date ASC
                    """,
                    tuple(daily_params),
                ).fetchall()
            ]
            runs_where = "biz_date BETWEEN ? AND ?"
            runs_params: list[Any] = [start_date, end_date]
            if stage:
                runs_where += " AND stage = ?"
                runs_params.append(stage)
            if interface_name:
                runs_where += " AND interface_name = ?"
                runs_params.append(interface_name)
            total_runs = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt
                FROM ingest_runs
                WHERE {runs_where}
                """,
                tuple(runs_params),
            ).fetchone()["cnt"]
        groups = []
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        for item in failed_groups:
            meta = self._interface_meta(item.get("interface_name"))
            failure_dates = sorted(
                {
                    value
                    for value in str(item.get("failure_dates_csv") or "").split(",")
                    if value
                },
                reverse=True,
            )
            consecutive_failure_days = 0
            if failure_dates:
                cursor = datetime.strptime(failure_dates[0], "%Y-%m-%d").date()
                for value in failure_dates:
                    current = datetime.strptime(value, "%Y-%m-%d").date()
                    if current != cursor:
                        break
                    consecutive_failure_days += 1
                    cursor = current - timedelta(days=1)
            last_success_biz_date = item.get("last_success_biz_date")
            days_since_last_success = None
            if last_success_biz_date:
                last_success_date = datetime.strptime(last_success_biz_date, "%Y-%m-%d").date()
                days_since_last_success = (end_date_obj - last_success_date).days
            groups.append(
                {
                    **item,
                    "interface_note": meta.get("notes"),
                    "interface_label": short_interface_meaning(item.get("interface_name"), meta.get("notes")),
                    "consecutive_failure_days": consecutive_failure_days,
                    "days_since_last_success": days_since_last_success,
                }
            )
        top_groups = groups[:limit]
        failed_interface_count = len(groups)
        never_succeeded_count = sum(1 for item in groups if not item.get("last_success_biz_date"))
        total_failures = sum(int(item["failure_count"] or 0) for item in groups)
        unresolved_failures = sum(int(item["unresolved_count"] or 0) for item in groups)
        failure_rate = round(total_failures / total_runs, 4) if total_runs else 0.0
        top_consecutive_failure_days = top_groups[0].get("consecutive_failure_days", 0) if top_groups else 0
        summary_status_label = health_status_label(
            unresolved_failures=unresolved_failures,
            failed_interface_count=failed_interface_count,
            never_succeeded_count=never_succeeded_count,
            consecutive_failure_days=top_consecutive_failure_days,
            total_failures=total_failures,
        )
        summary_status_reason = health_status_reason(
            unresolved_failures=unresolved_failures,
            failed_interface_count=failed_interface_count,
            never_succeeded_count=never_succeeded_count,
            consecutive_failure_days=top_consecutive_failure_days,
            total_failures=total_failures,
        )
        return {
            "start_date": start_date,
            "end_date": end_date,
            "days": days,
            "stage": stage,
            "interface_name": interface_name,
            "total_runs": total_runs,
            "total_failures": total_failures,
            "unresolved_failures": unresolved_failures,
            "failed_interface_count": failed_interface_count,
            "never_succeeded_count": never_succeeded_count,
            "failure_rate": failure_rate,
            "status_label": summary_status_label,
            "status_reason": summary_status_reason,
            "top_failed_interfaces": top_groups,
            "daily_failures": daily_rows,
        }

    def retry_summary(
        self,
        interface_name: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        retryables = self._list_retryable_groups(interface_name=interface_name, stage=stage)
        retryable_count = sum(item["error_count"] for item in retryables)
        failed_interface_count = len(retryables)
        return {
            "interface_name": interface_name,
            "stage": stage,
            "retryable_count": retryable_count,
            "failed_interface_count": failed_interface_count,
            "status_label": health_status_label(
                unresolved_failures=retryable_count,
                failed_interface_count=failed_interface_count,
                total_failures=retryable_count,
            ),
            "status_reason": health_status_reason(
                unresolved_failures=retryable_count,
                failed_interface_count=failed_interface_count,
                total_failures=retryable_count,
            ),
            "groups": retryables,
        }

    def _list_retryable_groups(
        self,
        interface_name: str | None = None,
        stage: str | None = None,
    ) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            where = "retryable = 1 AND resolved_at IS NULL"
            params: list[Any] = []
            if interface_name:
                where += " AND interface_name = ?"
                params.append(interface_name)
            if stage:
                where += " AND stage = ?"
                params.append(stage)
            rows = conn.execute(
                f"""
                SELECT interface_name, biz_date, stage, COUNT(*) AS error_count
                FROM ingest_errors
                WHERE {where}
                GROUP BY interface_name, biz_date, stage
                ORDER BY biz_date DESC, interface_name ASC
                """,
                tuple(params),
            ).fetchall()
        return [self._enrich_retry_group(dict(row)) for row in rows]

    def _resolve_retryable_errors(self, *, interface_name: str, target_date: str, stage: str | None) -> int:
        with get_db(self.db_path) as conn:
            migrate(conn)
            cursor = conn.execute(
                """
                UPDATE ingest_errors
                SET resolved_at = datetime('now')
                WHERE interface_name = ?
                  AND biz_date = ?
                  AND retryable = 1
                  AND resolved_at IS NULL
                  AND (? IS NULL OR stage = ?)
                """,
                (interface_name, target_date, stage, stage),
            )
        return int(cursor.rowcount or 0)

    def retry_unresolved_groups(
        self,
        *,
        limit: int | None = None,
        triggered_by: str = "api",
        input_by: str | None = None,
    ) -> dict[str, Any]:
        groups = self._list_retryable_groups()
        selected = groups[:limit] if limit is not None else groups
        runs: list[dict[str, Any]] = []
        resolved_errors = 0
        for group in selected:
            interface_name = group.get("interface_name")
            target_date = group.get("biz_date")
            if not interface_name or not target_date:
                continue
            result = self.execute_interface(
                interface_name,
                target_date,
                triggered_by=triggered_by,
                input_by=input_by,
            )
            run = result.get("run")
            if isinstance(run, dict):
                runs.append(run)
                if run.get("status") in {"success", "empty"}:
                    resolved_errors += self._resolve_retryable_errors(
                        interface_name=interface_name,
                        target_date=target_date,
                        stage=group.get("stage"),
                    )
        return {
            "requested_groups": len(selected),
            "attempted_groups": len(runs),
            "resolved_errors": resolved_errors,
            "runs": runs,
        }

    def reconcile_stale_runs(self, *, stale_minutes: int = 5) -> dict[str, Any]:
        cutoff = datetime.now() - timedelta(minutes=stale_minutes)
        reconciled: list[dict[str, Any]] = []
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT run_id, interface_name, provider, stage, biz_date, started_at, input_by
                FROM ingest_runs
                WHERE status = 'running' AND finished_at IS NULL
                ORDER BY started_at ASC, run_id ASC
                """
            ).fetchall()
            for row in rows:
                started_at = row["started_at"]
                try:
                    started_dt = datetime.fromisoformat(started_at)
                except (TypeError, ValueError):
                    started_dt = None
                if started_dt is None or started_dt > cutoff:
                    continue
                finished_at = datetime.now().isoformat(timespec="seconds")
                duration_ms = max(
                    0,
                    int((datetime.fromisoformat(finished_at) - started_dt).total_seconds() * 1000),
                )
                note = "stale running record reconciled after interrupted ingest"
                conn.execute(
                    """
                    UPDATE ingest_runs
                    SET status = 'failed', row_count = 0, finished_at = ?, duration_ms = ?, notes = ?
                    WHERE run_id = ?
                    """,
                    (finished_at, duration_ms, note, row["run_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO ingest_errors
                    (run_id, interface_name, biz_date, stage, error_type, error_message, retryable, context_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_id"],
                        row["interface_name"],
                        row["biz_date"],
                        row["stage"],
                        "provider",
                        note,
                        0,
                        json.dumps({"provider": row["provider"], "input_by": row["input_by"]}, ensure_ascii=False),
                    ),
                )
                reconciled.append(
                    {
                        "run_id": row["run_id"],
                        "interface_name": row["interface_name"],
                        "biz_date": row["biz_date"],
                        "stage": row["stage"],
                        "started_at": started_at,
                        "finished_at": finished_at,
                    }
                )
        return {
            "stale_minutes": stale_minutes,
            "reconciled_count": len(reconciled),
            "runs": reconciled,
        }

    def _interfaces_for_stage(self, stage: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in list_interfaces()
            if item["stage"] == stage and item["enabled_by_default"]
        ]

    def _quarter_end_for_date(self, target_date: str) -> str:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        if dt.month <= 3:
            return f"{dt.year - 1}1231"
        if dt.month <= 6:
            return f"{dt.year}0331"
        if dt.month <= 9:
            return f"{dt.year}0630"
        return f"{dt.year}0930"

    def _default_params(self, interface: dict[str, Any], target_date: str) -> dict[str, Any]:
        policy = interface["params_policy"]
        if policy == "trade_date":
            return {"trade_date": target_date.replace("-", "")}
        if policy == "date_range_day":
            return {"start_date": target_date.replace("-", ""), "end_date": target_date.replace("-", "")}
        if policy == "float_date":
            return {"float_date": target_date.replace("-", "")}
        if policy == "quarter_end":
            return {"end_date": self._quarter_end_for_date(target_date)}
        return {}

    def _begin_run(
        self,
        interface: dict[str, Any],
        target_date: str,
        *,
        triggered_by: str,
        input_by: str | None,
        notes: str,
    ) -> tuple[str, str, dict[str, Any]]:
        started_at = datetime.now().isoformat(timespec="seconds")
        run_id = f"ingest_{interface['interface_name']}_{uuid4().hex[:12]}"
        params = self._default_params(interface, target_date)
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO ingest_runs
                (run_id, interface_name, provider, stage, biz_date, target_date, params_json,
                 status, row_count, started_at, triggered_by, input_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    interface["interface_name"],
                    interface["provider_method"],
                    interface["stage"],
                    target_date,
                    target_date,
                    json.dumps(params, ensure_ascii=False),
                    "running",
                    0,
                    started_at,
                    triggered_by,
                    input_by,
                    notes,
                ),
            )
        return run_id, started_at, params

    def _finalize_run(
        self,
        run_id: str,
        *,
        provider: str,
        status: str,
        row_count: int,
        notes: str,
        started_at: str,
    ) -> None:
        finished_at = datetime.now().isoformat(timespec="seconds")
        duration_ms = max(
            0,
            int(
                (
                    datetime.fromisoformat(finished_at)
                    - datetime.fromisoformat(started_at)
                ).total_seconds()
                * 1000
            ),
        )
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                UPDATE ingest_runs
                SET provider = ?, status = ?, row_count = ?, finished_at = ?, duration_ms = ?, notes = ?
                WHERE run_id = ?
                """,
                (provider, status, row_count, finished_at, duration_ms, notes, run_id),
            )

    def _record_error(
        self,
        run_id: str,
        interface: dict[str, Any],
        target_date: str,
        *,
        error_type: str,
        error_message: str,
        context: dict[str, Any] | None = None,
        retryable: int = 1,
    ) -> None:
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO ingest_errors
                (run_id, interface_name, biz_date, stage, error_type, error_message, retryable, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    interface["interface_name"],
                    target_date,
                    interface["stage"],
                    error_type,
                    error_message,
                    retryable,
                    json.dumps(context or {}, ensure_ascii=False),
                ),
            )

    def _normalize_rows(self, data: Any) -> list[Any]:
        if data is None:
            return []
        if isinstance(data, list):
            return data
        return [data]

    def _store_payload(
        self,
        interface: dict[str, Any],
        target_date: str,
        *,
        provider: str,
        params: dict[str, Any],
        result: DataResult,
    ) -> tuple[str, int]:
        rows = self._normalize_rows(result.data)
        payload = {
            "interface_name": interface["interface_name"],
            "provider": provider,
            "biz_date": target_date,
            "params": params,
            "rows": rows,
            "summary": {"row_count": len(rows)},
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        dedupe_key = self._build_payload_dedupe_key(interface, target_date, params)
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO raw_interface_payloads
                (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
                 payload_json, payload_hash, row_count, status, params_json, source_meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(interface_name, dedupe_key) DO UPDATE SET
                    provider = excluded.provider,
                    stage = excluded.stage,
                    target_date = excluded.target_date,
                    raw_table = excluded.raw_table,
                    payload_json = excluded.payload_json,
                    payload_hash = excluded.payload_hash,
                    row_count = excluded.row_count,
                    status = excluded.status,
                    params_json = excluded.params_json,
                    source_meta_json = excluded.source_meta_json,
                    inserted_at = datetime('now')
                """,
                (
                    interface["interface_name"],
                    provider,
                    interface["stage"],
                    target_date,
                    target_date,
                    interface["raw_table"],
                    dedupe_key,
                    payload_json,
                    payload_hash,
                    len(rows),
                    "success" if rows else "empty",
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(result.to_dict(), ensure_ascii=False),
                ),
            )
        return dedupe_key, len(rows)

    def _build_payload_dedupe_key(
        self,
        interface: dict[str, Any],
        target_date: str,
        params: dict[str, Any],
    ) -> str:
        dedupe_fields = interface.get("dedupe_keys") or []
        dedupe_payload = {
            key: params[key]
            for key in dedupe_fields
            if key in params
        }
        if not dedupe_payload:
            dedupe_payload = dict(params)
        if "trade_date" not in dedupe_payload:
            dedupe_payload["trade_date"] = target_date.replace("-", "")
        dedupe_payload_json = json.dumps(dedupe_payload, ensure_ascii=False, sort_keys=True)
        params_hash = hashlib.sha256(dedupe_payload_json.encode("utf-8")).hexdigest()[:16]
        return f"{interface['interface_name']}:{target_date}:{params_hash}"

    def _upsert_market_fact_snapshot(
        self,
        *,
        biz_date: str,
        fact_type: str,
        subject_type: str,
        subject_code: str | None,
        subject_name: str | None,
        facts: dict[str, Any],
        source_interfaces: list[str],
        confidence: str = "high",
    ) -> str:
        subject_code = subject_code or ""
        snapshot_id = f"{biz_date}:{fact_type}:{subject_type}:{subject_code}"
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO market_fact_snapshots
                (snapshot_id, biz_date, fact_type, subject_type, subject_code, subject_name,
                 facts_json, source_interfaces_json, confidence, inserted_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(biz_date, fact_type, subject_type, subject_code) DO UPDATE SET
                    subject_name = excluded.subject_name,
                    facts_json = excluded.facts_json,
                    source_interfaces_json = excluded.source_interfaces_json,
                    confidence = excluded.confidence,
                    updated_at = datetime('now')
                """,
                (
                    snapshot_id,
                    biz_date,
                    fact_type,
                    subject_type,
                    subject_code or None,
                    subject_name,
                    json.dumps(facts, ensure_ascii=False, sort_keys=True),
                    json.dumps(source_interfaces, ensure_ascii=False),
                    confidence,
                ),
            )
        return snapshot_id

    def _replace_fact_entities(
        self,
        *,
        biz_date: str,
        interface_name: str,
        entities: list[dict[str, Any]],
    ) -> int:
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                "DELETE FROM fact_entities WHERE biz_date = ? AND interface_name = ?",
                (biz_date, interface_name),
            )
            for entity in entities:
                conn.execute(
                    """
                    INSERT INTO fact_entities
                    (biz_date, interface_name, entity_type, entity_code, entity_name, role, attributes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        biz_date,
                        interface_name,
                        entity["entity_type"],
                        entity.get("entity_code"),
                        entity["entity_name"],
                        entity["role"],
                        json.dumps(entity.get("attributes", {}), ensure_ascii=False, sort_keys=True),
                    ),
                )
        return len(entities)

    def _derive_snapshots(
        self,
        interface: dict[str, Any],
        *,
        target_date: str,
        result: DataResult,
    ) -> list[str]:
        created: list[str] = []
        data = result.data

        if interface["interface_name"] == "margin" and isinstance(data, dict):
            facts = {
                "trade_date": data.get("trade_date", target_date),
                "total_rzye_yi": data.get("total_rzye_yi"),
                "total_rqye_yi": data.get("total_rqye_yi"),
                "total_rzrqye_yi": data.get("total_rzrqye_yi"),
                "exchanges": data.get("exchanges", []),
            }
            created.append(
                self._upsert_market_fact_snapshot(
                    biz_date=target_date,
                    fact_type="margin_stats",
                    subject_type="market",
                    subject_code="CN",
                    subject_name="A股市场",
                    facts=facts,
                    source_interfaces=[interface["interface_name"]],
                )
            )

        if interface["interface_name"] == "moneyflow_hsgt" and isinstance(data, dict):
            facts = {
                "north_money": data.get("north_money"),
                "south_money": data.get("south_money"),
                "northbound_net_buy_billion": data.get("net_buy_billion"),
            }
            created.append(
                self._upsert_market_fact_snapshot(
                    biz_date=target_date,
                    fact_type="capital_flow",
                    subject_type="market",
                    subject_code="CN",
                    subject_name="A股市场",
                    facts=facts,
                    source_interfaces=[interface["interface_name"]],
                )
            )

        if interface["interface_name"] == "top_inst":
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                top_positive = sorted(
                    rows,
                    key=lambda item: float(item.get("net_amount", 0) or 0),
                    reverse=True,
                )[:10]
                facts = {
                    "record_count": len(rows),
                    "positive_net_count": sum(1 for item in rows if float(item.get("net_amount", 0) or 0) > 0),
                    "top_positive_stocks": [
                        {
                            "code": item.get("code"),
                            "name": item.get("name"),
                            "reason": item.get("reason"),
                            "net_amount": float(item.get("net_amount", 0) or 0),
                        }
                        for item in top_positive
                    ],
                }
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="watchlist_context",
                        subject_type="market",
                        subject_code="CN",
                        subject_name="龙虎榜机构席位",
                        facts=facts,
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        if interface["interface_name"] == "daily_info":
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="market_stats",
                        subject_type="market",
                        subject_code="CN",
                        subject_name="A股市场交易统计",
                        facts={
                            "record_count": len(rows),
                            "rows": rows[:30],
                        },
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        if interface["interface_name"] == "limit_step":
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                highest_board = 0
                for row in rows:
                    for key in ("height", "step", "limit_times"):
                        value = row.get(key)
                        try:
                            highest_board = max(highest_board, int(value))
                            break
                        except (TypeError, ValueError):
                            continue
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="limit_ladder",
                        subject_type="market",
                        subject_code="CN",
                        subject_name="连板天梯",
                        facts={
                            "record_count": len(rows),
                            "highest_board": highest_board,
                            "rows": rows[:50],
                        },
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        if interface["interface_name"] == "limit_cpt_list":
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="strongest_sectors",
                        subject_type="market",
                        subject_code="CN",
                        subject_name="最强板块统计",
                        facts={
                            "record_count": len(rows),
                            "top_sectors": rows[:20],
                        },
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        if interface["interface_name"] in {"moneyflow_ind_ths", "moneyflow_ind_dc"}:
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                subject_code = "THS" if interface["interface_name"].endswith("_ths") else "DC"
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="sector_fund_flow",
                        subject_type="market",
                        subject_code=subject_code,
                        subject_name="板块资金流向",
                        facts={
                            "record_count": len(rows),
                            "top_rows": rows[:20],
                        },
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        if interface["interface_name"] == "moneyflow_mkt_dc":
            rows = [row for row in self._normalize_rows(data) if isinstance(row, dict)]
            if rows:
                created.append(
                    self._upsert_market_fact_snapshot(
                        biz_date=target_date,
                        fact_type="market_moneyflow",
                        subject_type="market",
                        subject_code="CN",
                        subject_name="大盘资金流向",
                        facts=rows[0] if len(rows) == 1 else {"rows": rows[:10]},
                        source_interfaces=[interface["interface_name"]],
                    )
                )

        return created

    def _derive_fact_entities(
        self,
        interface: dict[str, Any],
        *,
        target_date: str,
        result: DataResult,
    ) -> int:
        rows = [row for row in self._normalize_rows(result.data) if isinstance(row, dict)]
        entities: list[dict[str, Any]] = []

        if interface["interface_name"] == "top_inst":
            for row in rows:
                code = str(row.get("code", "") or "").upper()
                name = str(row.get("name", "") or "").strip()
                if not code or not name:
                    continue
                entities.append(
                    {
                        "entity_type": "stock",
                        "entity_code": code,
                        "entity_name": name,
                        "role": "top_ranked",
                        "attributes": {
                            "reason": row.get("reason", ""),
                            "buy_amount": float(row.get("buy_amount", 0) or 0),
                            "sell_amount": float(row.get("sell_amount", 0) or 0),
                            "net_amount": float(row.get("net_amount", 0) or 0),
                        },
                    }
                )

        if interface["interface_name"] == "block_trade":
            for row in rows:
                code = str(row.get("code") or row.get("ts_code") or "").upper()
                name = str(row.get("name", "") or "").strip()
                if not code or not name:
                    continue
                entities.append(
                    {
                        "entity_type": "stock",
                        "entity_code": code,
                        "entity_name": name,
                        "role": "mentioned",
                        "attributes": {
                            key: row.get(key)
                            for key in ("price", "vol", "amount", "buyer", "seller", "trade_date")
                            if key in row
                        },
                    }
                )

        if interface["interface_name"] == "limit_cpt_list":
            for row in rows[:20]:
                sector_code = str(row.get("ts_code") or row.get("code") or "").strip()
                sector_name = str(
                    row.get("name")
                    or row.get("concept_name")
                    or row.get("industry_name")
                    or row.get("sector_name")
                    or ""
                ).strip()
                if not sector_name:
                    continue
                entities.append(
                    {
                        "entity_type": "sector",
                        "entity_code": sector_code or None,
                        "entity_name": sector_name,
                        "role": "top_ranked",
                        "attributes": {
                            key: row.get(key)
                            for key in ("trade_date", "amount", "up_num", "rank", "pct_chg")
                            if key in row
                        },
                    }
                )

        if interface["interface_name"] in {"moneyflow_ind_ths", "moneyflow_ind_dc"}:
            for row in rows[:30]:
                sector_code = str(row.get("ts_code") or row.get("code") or "").strip()
                sector_name = str(
                    row.get("name")
                    or row.get("板块名称")
                    or row.get("industry_name")
                    or row.get("concept_name")
                    or ""
                ).strip()
                if not sector_name:
                    continue
                entities.append(
                    {
                        "entity_type": "sector",
                        "entity_code": sector_code or None,
                        "entity_name": sector_name,
                        "role": "mentioned",
                        "attributes": row,
                    }
                )

        if not entities:
            return 0
        return self._replace_fact_entities(
            biz_date=target_date,
            interface_name=interface["interface_name"],
            entities=entities,
        )

    def _record_skeleton_run(
        self,
        interface: dict[str, Any],
        target_date: str,
        *,
        triggered_by: str,
        input_by: str | None,
        notes: str,
    ) -> dict[str, Any]:
        started_at = datetime.now().isoformat(timespec="seconds")
        run_id = f"ingest_{interface['interface_name']}_{uuid4().hex[:12]}"
        params = self._default_params(interface, target_date)
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO ingest_runs
                (run_id, interface_name, provider, stage, biz_date, target_date, params_json,
                 status, row_count, started_at, finished_at, duration_ms, triggered_by, input_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    interface["interface_name"],
                    interface["provider_method"],
                    interface["stage"],
                    target_date,
                    target_date,
                    json.dumps(params, ensure_ascii=False),
                    "empty",
                    0,
                    started_at,
                    started_at,
                    0,
                    triggered_by,
                    input_by,
                    notes,
                ),
            )
        return {
            "run_id": run_id,
            "interface_name": interface["interface_name"],
            "provider": interface["provider_method"],
            "stage": interface["stage"],
            "biz_date": target_date,
            "target_date": target_date,
            "status": "empty",
            "row_count": 0,
            "params": params,
            "notes": notes,
        }

    def _call_registered_interface(self, interface: dict[str, Any], target_date: str) -> DataResult:
        method_name = interface["provider_method"]
        if self.registry is None:
            return DataResult(data=None, source="ingest_service", error="provider registry 未注入")
        if not any(provider.supports(method_name) for provider in self.registry.providers):
            return DataResult(data=None, source="ingest_service", error=f"当前 provider 未实现 {method_name}")
        return self.registry.call(method_name, target_date)

    def execute_stage(
        self,
        stage: str,
        target_date: str,
        *,
        triggered_by: str = "cli",
        input_by: str | None = None,
    ) -> dict[str, Any]:
        interfaces = self._interfaces_for_stage(stage)
        runs = [
            self._execute_interface_config(
                interface,
                target_date,
                triggered_by=triggered_by,
                input_by=input_by,
            )
            for interface in interfaces
        ]
        return {
            "status": "ok",
            "message": "采集任务已执行并写入审计表",
            "stage": stage,
            "stage_label": stage_label(stage),
            "date": target_date,
            "interface_count": len(interfaces),
            "recorded_runs": len(runs),
            "runs": runs,
        }

    def execute_interface(
        self,
        name: str,
        target_date: str,
        *,
        triggered_by: str = "cli",
        input_by: str | None = None,
    ) -> dict[str, Any]:
        interface = get_interface(name)
        if not interface:
            return {
                "status": "not_found",
                "message": f"接口未注册: {name}",
                "name": name,
                "date": target_date,
            }
        run = self._execute_interface_config(
            dict(interface),
            target_date,
            triggered_by=triggered_by,
            input_by=input_by,
        )
        return {
            "status": run["status"],
            "message": "单接口执行完成并写入审计表",
            "name": name,
            "date": target_date,
            "run": run,
        }

    def _execute_interface_config(
        self,
        interface: dict[str, Any],
        target_date: str,
        *,
        triggered_by: str,
        input_by: str | None,
    ) -> dict[str, Any]:
        run_id, started_at, params = self._begin_run(
            interface,
            target_date,
            triggered_by=triggered_by,
            input_by=input_by,
            notes="Executing ingest interface",
        )
        result = self._call_registered_interface(interface, target_date)
        provider = result.source or interface["provider_method"]
        if not result.success:
            error_type, retryable = self._classify_provider_error(result.error)
            self._record_error(
                run_id,
                interface,
                target_date,
                error_type=error_type,
                error_message=result.error,
                context={"provider_method": interface["provider_method"]},
                retryable=retryable,
            )
            self._finalize_run(
                run_id,
                provider=provider,
                status="failed",
                row_count=0,
                notes=result.error,
                started_at=started_at,
            )
            return self._enrich_run_record({
                "run_id": run_id,
                "interface_name": interface["interface_name"],
                "provider": provider,
                "stage": interface["stage"],
                "biz_date": target_date,
                "target_date": target_date,
                "status": "failed",
                "row_count": 0,
                "params": params,
                "error": result.error,
            })

        dedupe_key, row_count = self._store_payload(interface, target_date, provider=provider, params=params, result=result)
        snapshot_ids = self._derive_snapshots(interface, target_date=target_date, result=result)
        entity_count = self._derive_fact_entities(interface, target_date=target_date, result=result)
        status = "success" if row_count > 0 else "empty"
        self._finalize_run(
            run_id,
            provider=provider,
            status=status,
            row_count=row_count,
            notes="provider execution complete",
            started_at=started_at,
        )
        return self._enrich_run_record({
            "run_id": run_id,
            "interface_name": interface["interface_name"],
            "provider": provider,
            "stage": interface["stage"],
            "biz_date": target_date,
            "target_date": target_date,
            "status": status,
            "row_count": row_count,
            "params": params,
            "dedupe_key": dedupe_key,
            "snapshot_ids": snapshot_ids,
            "entity_count": entity_count,
        })

    def describe_run_request(self, stage: str, target_date: str) -> dict[str, Any]:
        interfaces = self._interfaces_for_stage(stage)
        return {
            "stage": stage,
            "date": target_date,
            "interface_count": len(interfaces),
            "interfaces": interfaces,
            "status": "todo",
            "message": "IngestRunner 尚未接入 provider 调用；当前返回任务编排预览。",
        }

    def describe_interface_run(self, name: str, target_date: str) -> dict[str, Any]:
        interface = get_interface(name)
        if not interface:
            return {
                "status": "not_found",
                "message": f"接口未注册: {name}",
                "name": name,
                "date": target_date,
            }
        return {
            "status": "todo",
            "message": "单接口执行骨架已接入，真实 provider 调用待后续实现。",
            "name": name,
            "date": target_date,
            "interface": dict(interface),
        }
