"""采集底座服务：围绕接口注册表和原始事实层审计。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any
from uuid import uuid4

from db.connection import get_db
from db.migrate import migrate
from ingest.registry import get_interface, list_interfaces
from providers.base import DataResult


@dataclass
class IngestService:
    db_path: str | None = None
    registry: Any | None = None

    def list_interfaces(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list_interfaces()]

    def inspect(self, target_date: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            runs = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT run_id, interface_name, provider, stage, biz_date, target_date,
                           status, row_count, started_at, finished_at, duration_ms,
                           triggered_by, input_by, notes
                    FROM ingest_runs
                    WHERE biz_date = ?
                    ORDER BY started_at DESC, run_id DESC
                    """,
                    (target_date,),
                ).fetchall()
            ]
            errors = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, run_id, interface_name, biz_date, stage, error_type,
                           error_message, retryable, created_at, resolved_at
                    FROM ingest_errors
                    WHERE biz_date = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (target_date,),
                ).fetchall()
            ]
        return {
            "date": target_date,
            "run_count": len(runs),
            "error_count": len(errors),
            "runs": runs,
            "errors": errors,
        }

    def retry_summary(self) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT interface_name, biz_date, stage, COUNT(*) AS error_count
                FROM ingest_errors
                WHERE retryable = 1 AND resolved_at IS NULL
                GROUP BY interface_name, biz_date, stage
                ORDER BY biz_date DESC, interface_name ASC
                """
            ).fetchall()
        retryables = [dict(row) for row in rows]
        return {
            "retryable_count": sum(item["error_count"] for item in retryables),
            "groups": retryables,
        }

    def _interfaces_for_stage(self, stage: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in list_interfaces()
            if item["stage"] == stage and item["enabled_by_default"]
        ]

    def _default_params(self, interface: dict[str, Any], target_date: str) -> dict[str, Any]:
        policy = interface["params_policy"]
        if policy == "trade_date":
            return {"trade_date": target_date.replace("-", "")}
        if policy == "date_range_day":
            return {"start_date": target_date.replace("-", ""), "end_date": target_date.replace("-", "")}
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
        runs = [self._execute_interface_config(interface, target_date, triggered_by=triggered_by, input_by=input_by) for interface in interfaces]
        return {
            "status": "ok",
            "message": "采集任务已执行并写入审计表",
            "stage": stage,
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
        run = self._execute_interface_config(dict(interface), target_date, triggered_by=triggered_by, input_by=input_by)
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
            self._record_error(
                run_id,
                interface,
                target_date,
                error_type="provider",
                error_message=result.error,
                context={"provider_method": interface["provider_method"]},
            )
            self._finalize_run(
                run_id,
                provider=provider,
                status="failed",
                row_count=0,
                notes=result.error,
                started_at=started_at,
            )
            return {
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
            }

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
        return {
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
            }

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
