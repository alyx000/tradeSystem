"""月线模式的行情、财务快照与运行审计仓储。"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timezone


def _dump(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load(value, default):
    if value in (None, ""):
        return default
    return json.loads(value)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _canonical_json_hash(value: dict) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _local_validate_derived_fact_row(row: dict) -> dict:
    """derived_facts 模块尚未装载时的最小同契约校验。

    正常运行优先调用 ``derived_facts.validate_fact_row``；保留本地实现使
    repository 的旧库只读 fallback 与独立测试不依赖模块导入顺序。
    """
    item = dict(row)
    status = str(item.get("fact_status") or "")
    if status not in {"certified_bar", "certified_no_trade"}:
        raise ValueError("fact_status must be certified_bar or certified_no_trade")
    try:
        date.fromisoformat(str(item.get("month_end") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("month_end must be YYYY-MM-DD") from exc
    stock_code = str(item.get("stock_code") or "").split(".")[0].strip()
    if not stock_code:
        raise ValueError("stock_code must not be empty")
    item["stock_code"] = stock_code
    for field in (
        "formula_version",
        "replacement_reason",
        "raw_crosscheck_status",
        "source_payload_hash",
    ):
        value = str(item.get(field) or "").strip()
        if not value:
            raise ValueError(f"{field} must not be empty")
        item[field] = value
    if (
        len(item["source_payload_hash"]) != 64
        or any(char not in "0123456789abcdef" for char in item["source_payload_hash"])
    ):
        raise ValueError("source_payload_hash must be lowercase sha256")
    source_meta = item.get("source_meta")
    if source_meta is None:
        source_meta = {}
    if not isinstance(source_meta, dict):
        raise ValueError("source_meta must be a mapping")
    item["source_meta"] = source_meta

    bar_fields = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "anchor_adj_factor",
    )
    if status == "certified_no_trade":
        if any(item.get(field) is not None for field in bar_fields):
            raise ValueError("certified_no_trade must not contain bar values")
        if item.get("first_trade_date") is not None or item.get("last_trade_date") is not None:
            raise ValueError("certified_no_trade must not contain trade dates")
        if item.get("trading_days") != 0:
            raise ValueError("certified_no_trade trading_days must be 0")
        return item

    numbers: dict[str, float] = {}
    for field in bar_fields:
        try:
            number = float(item[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be finite") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        numbers[field] = number
        item[field] = number
    if min(numbers[field] for field in ("open", "high", "low", "close")) <= 0:
        raise ValueError("OHLC must be positive")
    if (
        numbers["high"] < max(numbers["open"], numbers["close"])
        or numbers["low"] > min(numbers["open"], numbers["close"])
    ):
        raise ValueError("invalid OHLC")
    if numbers["volume"] < 0 or numbers["amount"] < 0:
        raise ValueError("volume and amount must be non-negative")
    if numbers["anchor_adj_factor"] <= 0:
        raise ValueError("anchor_adj_factor must be positive")
    try:
        trading_days = int(item.get("trading_days"))
    except (TypeError, ValueError) as exc:
        raise ValueError("trading_days must be a positive integer") from exc
    if isinstance(item.get("trading_days"), bool) or trading_days <= 0:
        raise ValueError("trading_days must be a positive integer")
    item["trading_days"] = trading_days
    for field in ("first_trade_date", "last_trade_date"):
        try:
            item[field] = date.fromisoformat(str(item.get(field) or "")).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be YYYY-MM-DD") from exc
    if item["first_trade_date"] > item["last_trade_date"]:
        raise ValueError("first_trade_date must not exceed last_trade_date")
    return item


def _validate_derived_fact_row(row: dict) -> dict:
    try:
        from .derived_facts import validate_fact_row
    except ImportError:
        return _local_validate_derived_fact_row(row)
    normalized = validate_fact_row(dict(row))
    if not isinstance(normalized, dict):
        raise ValueError("derived_facts.validate_fact_row must return dict")
    if "source_meta_json" in normalized:
        try:
            source_meta = _load(normalized["source_meta_json"], {})
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("derived fact source_meta_json invalid") from exc
        if not isinstance(source_meta, dict):
            raise ValueError("derived fact source_meta_json must be an object")
        normalized["source_meta"] = source_meta
    return normalized


def _compute_derived_fact_hash(row: dict) -> str:
    try:
        from .derived_facts import compute_fact_hash
    except ImportError:
        payload = {
            field: row.get(field)
            for field in (
                "month_end",
                "stock_code",
                "fact_status",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "anchor_adj_factor",
                "trading_days",
                "first_trade_date",
                "last_trade_date",
                "source_payload_hash",
                "formula_version",
                "replacement_reason",
                "raw_crosscheck_status",
                "source_meta",
            )
        }
        return _canonical_json_hash(payload)
    return str(compute_fact_hash(dict(row)))


def _financial_payload_hash(row: dict) -> str:
    """财务事实内容身份；来源重试元数据不参与版本身份。"""
    payload = {
        "fina_indicator": row.get("fina_indicator") or {},
        "balancesheet": row.get("balancesheet") or {},
        "income": row.get("income") or {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_revision_sensitive(row: dict) -> bool:
    if bool(row.get("revision_sensitive")):
        return True
    for component in ("fina_indicator", "balancesheet", "income"):
        payload = row.get(component) or {}
        if str(payload.get("update_flag") or "").strip() == "1":
            return True
        if component in {"balancesheet", "income"}:
            try:
                if float(str(payload.get("report_type") or "").strip()) == 4.0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def save_month_bars(conn: sqlite3.Connection, rows: list[dict]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO monthly_pattern_bars (
            month_end, stock_code, stock_name, open, high, low, close,
            volume, amount, adj_factor, source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(month_end, stock_code) DO UPDATE SET
            stock_name=COALESCE(excluded.stock_name, monthly_pattern_bars.stock_name),
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            amount=excluded.amount,
            adj_factor=excluded.adj_factor,
            source=excluded.source,
            fetched_at=datetime('now')
        """,
        [
            (
                row["month_end"],
                str(row["stock_code"]).split(".")[0],
                row.get("stock_name"),
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row.get("volume"),
                row.get("amount"),
                row["adj_factor"],
                row.get("source"),
            )
            for row in rows
        ],
    )


def replace_month_bars(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """用一次已通过来源校验的完整月快照替换该月裸事实，避免遗留旧代码行。"""
    if not rows:
        raise ValueError("完整月快照不能为空")
    month_ends = {str(row.get("month_end") or "") for row in rows}
    if len(month_ends) != 1 or "" in month_ends:
        raise ValueError("完整月快照必须且只能包含一个 month_end")
    month_end = next(iter(month_ends))
    conn.execute(
        "DELETE FROM monthly_pattern_bar_manifests WHERE month_end = ?",
        (month_end,),
    )
    conn.execute(
        "DELETE FROM monthly_pattern_bars WHERE month_end = ?",
        (month_end,),
    )
    save_month_bars(conn, rows)


def save_month_bar_manifest(conn: sqlite3.Connection, manifest: dict) -> None:
    """保存完成覆盖校验后的月快照认证收据。"""
    conn.execute(
        """
        INSERT INTO monthly_pattern_bar_manifests (
            month_end, status, universe_source, universe_count,
            quote_count, factor_count, joined_count,
            quote_coverage, factor_coverage, source_meta_json, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(month_end) DO UPDATE SET
            status=excluded.status,
            universe_source=excluded.universe_source,
            universe_count=excluded.universe_count,
            quote_count=excluded.quote_count,
            factor_count=excluded.factor_count,
            joined_count=excluded.joined_count,
            quote_coverage=excluded.quote_coverage,
            factor_coverage=excluded.factor_coverage,
            source_meta_json=excluded.source_meta_json,
            fetched_at=datetime('now')
        """,
        (
            manifest["month_end"],
            manifest.get("status") or "certified",
            manifest["universe_source"],
            manifest["universe_count"],
            manifest["quote_count"],
            manifest["factor_count"],
            manifest["joined_count"],
            manifest["quote_coverage"],
            manifest["factor_coverage"],
            _dump(manifest.get("source_meta")),
        ),
    )


def load_month_bar_manifests(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> list[dict]:
    if not month_ends:
        return []
    placeholders = ",".join("?" for _ in month_ends)
    rows = conn.execute(
        f"""
        SELECT m.month_end, m.status, m.universe_source, m.universe_count,
               m.quote_count, m.factor_count, m.joined_count,
               m.quote_coverage, m.factor_coverage, m.source_meta_json,
               m.fetched_at, COUNT(b.stock_code) AS actual_count
        FROM monthly_pattern_bar_manifests AS m
        LEFT JOIN monthly_pattern_bars AS b ON b.month_end = m.month_end
        WHERE m.month_end IN ({placeholders})
        GROUP BY m.month_end
        ORDER BY m.month_end
        """,
        tuple(month_ends),
    ).fetchall()
    output: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            source_meta = _load(item.pop("source_meta_json"), {})
        except (json.JSONDecodeError, TypeError):
            source_meta = None
        if not isinstance(source_meta, dict):
            # 只把损坏的 manifest 当 cache miss；后续完整重采会原子替换它。
            item["status"] = "invalid"
            item["source_meta"] = {"manifest_invalid": "source_meta_json"}
        else:
            item["source_meta"] = source_meta
        output.append(item)
    return output


def existing_month_ends(
    conn: sqlite3.Connection,
    month_ends: list[str],
    *,
    min_rows: int = 1,
    min_universe_coverage: float = 0.95,
    min_factor_coverage: float = 0.95,
) -> set[str]:
    """返回有 certified manifest 且事实行数/覆盖率仍匹配的可复用月份。

    ``min_rows`` 仅保留旧调用兼容，不再作为历史全市场完整性的固定地板。
    """
    del min_rows
    if not month_ends:
        return set()
    output: set[str] = set()
    for manifest in load_month_bar_manifests(conn, month_ends):
        if manifest.get("status") != "certified":
            continue
        try:
            from .market import (
                SourceCoverageError,
                validate_code_alias_normalization_receipts,
            )

            alias_receipts = validate_code_alias_normalization_receipts(manifest)
        except SourceCoverageError:
            continue
        try:
            joined_count = int(manifest.get("joined_count"))
            actual_count = int(manifest.get("actual_count"))
            universe_count = int(manifest.get("universe_count"))
            quote_coverage = float(manifest.get("quote_coverage"))
            factor_coverage = float(manifest.get("factor_coverage"))
            source_meta = manifest.get("source_meta")
            valid_coverage = (
                float(source_meta["valid_universe_coverage"])
                if isinstance(source_meta, dict)
                and source_meta.get("valid_universe_coverage") is not None
                else joined_count / universe_count
            )
        except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
            continue
        numeric_values = (quote_coverage, factor_coverage, valid_coverage)
        if not all(math.isfinite(value) for value in numeric_values):
            continue
        if joined_count != actual_count:
            continue
        if quote_coverage < min_universe_coverage:
            continue
        if factor_coverage < min_factor_coverage:
            continue
        if universe_count <= 0 or valid_coverage < min_universe_coverage:
            continue
        alias_cache_valid = True
        for receipt in alias_receipts:
            cached_rows = conn.execute(
                """
                SELECT stock_code, open, high, low, close,
                       volume, amount, adj_factor
                FROM monthly_pattern_bars
                WHERE month_end = ?
                  AND stock_code IN (?, ?)
                """,
                (
                    manifest["month_end"],
                    receipt["alias_code"],
                    receipt["canonical_code"],
                ),
            ).fetchall()
            cached_by_code = {str(row["stock_code"]): row for row in cached_rows}
            canonical_row = cached_by_code.get(receipt["canonical_code"])
            if canonical_row is None or receipt["alias_code"] in cached_by_code:
                alias_cache_valid = False
                break
            fingerprint = receipt["quote_fingerprint"]
            cached_fields = {
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "vol": "volume",
                "amount": "amount",
            }
            try:
                cached_factor = float(canonical_row["adj_factor"])
                receipt_factor = float(receipt["canonical_adj_factor"])
                cached_values = {
                    field: float(canonical_row[column])
                    for field, column in cached_fields.items()
                }
                receipt_values = {
                    field: float(fingerprint[field])
                    for field in cached_fields
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                alias_cache_valid = False
                break
            scalar_values = [
                cached_factor,
                receipt_factor,
                *cached_values.values(),
                *receipt_values.values(),
            ]
            if (
                cached_factor <= 0
                or not all(math.isfinite(value) for value in scalar_values)
                or cached_factor != receipt_factor
                or any(
                    cached_values[field] != receipt_values[field]
                    for field in cached_fields
                )
            ):
                alias_cache_valid = False
                break
        if not alias_cache_valid:
            continue
        output.add(str(manifest["month_end"]))
    return output


def load_month_bars(conn: sqlite3.Connection, month_ends: list[str]) -> list[dict]:
    if not month_ends:
        return []
    placeholders = ",".join("?" for _ in month_ends)
    rows = conn.execute(
        f"""
        SELECT month_end, stock_code, stock_name, open, high, low, close,
               volume, amount, adj_factor, source, fetched_at
        FROM monthly_pattern_bars
        WHERE month_end IN ({placeholders})
        ORDER BY stock_code, month_end
        """,
        tuple(month_ends),
    ).fetchall()
    return [dict(row) for row in rows]


def load_derived_month_facts(
    conn: sqlite3.Connection,
    month_ends: list[str],
    *,
    stock_codes: list[str] | None = None,
    verify_hash: bool = True,
) -> list[dict]:
    """读取并验签派生完成月事实。

    存量真实库尚未执行版本无关兜底创建时返回空列表，使只读 monitor 保持
    raw fallback；表一旦存在，损坏的 JSON 或哈希必须 fail-closed。
    """
    if (
        not month_ends
        or not _table_exists(conn, "monthly_pattern_derived_month_facts")
    ):
        return []
    params: list[str] = list(month_ends)
    month_placeholders = ",".join("?" for _ in month_ends)
    stock_clause = ""
    if stock_codes is not None:
        normalized_codes = sorted(
            {str(code).split(".")[0].strip() for code in stock_codes if str(code).strip()}
        )
        if not normalized_codes:
            return []
        stock_placeholders = ",".join("?" for _ in normalized_codes)
        stock_clause = f" AND stock_code IN ({stock_placeholders})"
        params.extend(normalized_codes)
    rows = conn.execute(
        f"""
        SELECT month_end, stock_code, stock_name, fact_status,
               open, high, low, close, volume, amount, anchor_adj_factor,
               trading_days, first_trade_date, last_trade_date,
               source_payload_hash, fact_hash, formula_version,
               replacement_reason, raw_crosscheck_status, source_meta_json,
               first_run_id, created_at
        FROM monthly_pattern_derived_month_facts
        WHERE month_end IN ({month_placeholders}){stock_clause}
        ORDER BY stock_code, month_end
        """,
        tuple(params),
    ).fetchall()
    output: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            source_meta = _load(item.pop("source_meta_json"), {})
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"derived fact source_meta_json invalid: "
                f"{item['month_end']} {item['stock_code']}"
            ) from exc
        item["source_meta"] = source_meta
        normalized = _validate_derived_fact_row(item)
        normalized["stock_name"] = item.get("stock_name")
        normalized["fact_hash"] = item["fact_hash"]
        normalized["first_run_id"] = item["first_run_id"]
        normalized["created_at"] = item["created_at"]
        if verify_hash:
            expected_hash = _compute_derived_fact_hash(normalized)
            if expected_hash != item["fact_hash"]:
                raise ValueError(
                    f"derived fact hash mismatch: "
                    f"{item['month_end']} {item['stock_code']}"
                )
        output.append(normalized)
    return output


def save_derived_month_facts(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    first_run_id: str,
) -> dict[str, int]:
    """追加认证派生事实；同身份同哈希幂等，异哈希拒绝覆盖。"""
    if not rows:
        return {"inserted": 0, "idempotent": 0}
    run_id = str(first_run_id or "").strip()
    if not run_id:
        raise ValueError("first_run_id must not be empty")
    if not _table_exists(conn, "monthly_pattern_derived_month_facts"):
        raise sqlite3.OperationalError(
            "monthly_pattern_derived_month_facts is missing; run db migrate"
        )
    if (
        not _table_exists(conn, "monthly_pattern_derived_fact_runs")
        or conn.execute(
            "SELECT 1 FROM monthly_pattern_derived_fact_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        is None
    ):
        raise ValueError(f"first_run_id not found: {run_id}")

    prepared_by_identity: dict[tuple[str, str], dict] = {}
    for raw in rows:
        item = _validate_derived_fact_row(raw)
        supplied_run_id = str(raw.get("first_run_id") or run_id).strip()
        if supplied_run_id != run_id:
            raise ValueError("fact first_run_id must equal write run_id")
        expected_hash = _compute_derived_fact_hash(item)
        supplied_hash = str(raw.get("fact_hash") or "").strip()
        if supplied_hash != expected_hash:
            raise ValueError(
                f"fact_hash mismatch: {item['month_end']} {item['stock_code']}"
            )
        item["fact_hash"] = expected_hash
        item["first_run_id"] = run_id
        identity = (item["month_end"], item["stock_code"])
        previous = prepared_by_identity.get(identity)
        if previous is not None and previous["fact_hash"] != expected_hash:
            raise ValueError(
                f"conflicting derived facts in batch: {identity[0]} {identity[1]}"
            )
        prepared_by_identity[identity] = item

    to_insert: list[dict] = []
    idempotent = 0
    for identity, item in prepared_by_identity.items():
        existing = conn.execute(
            """
            SELECT fact_hash
            FROM monthly_pattern_derived_month_facts
            WHERE month_end = ? AND stock_code = ?
            """,
            identity,
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != item["fact_hash"]:
                raise ValueError(
                    f"derived fact conflict: {identity[0]} {identity[1]}"
                )
            idempotent += 1
            continue
        to_insert.append(item)

    for item in to_insert:
        conn.execute(
            """
            INSERT INTO monthly_pattern_derived_month_facts (
                month_end, stock_code, stock_name, fact_status,
                open, high, low, close, volume, amount, anchor_adj_factor,
                trading_days, first_trade_date, last_trade_date,
                source_payload_hash, fact_hash, formula_version,
                replacement_reason, raw_crosscheck_status, source_meta_json,
                first_run_id, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, datetime('now')
            )
            """,
            (
                item["month_end"],
                item["stock_code"],
                item.get("stock_name"),
                item["fact_status"],
                item.get("open"),
                item.get("high"),
                item.get("low"),
                item.get("close"),
                item.get("volume"),
                item.get("amount"),
                item.get("anchor_adj_factor"),
                item.get("trading_days"),
                item.get("first_trade_date"),
                item.get("last_trade_date"),
                item["source_payload_hash"],
                item["fact_hash"],
                item["formula_version"],
                item["replacement_reason"],
                item["raw_crosscheck_status"],
                _dump(item.get("source_meta")),
                run_id,
            ),
        )
    return {"inserted": len(to_insert), "idempotent": idempotent}


def save_derived_facts(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    first_run_id: str,
) -> dict[str, int]:
    """``save_derived_month_facts`` 的稳定短名。"""
    return save_derived_month_facts(conn, rows, first_run_id=first_run_id)


def load_effective_month_bars(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> list[dict]:
    """返回 raw 月线叠加认证派生 bar 的计算视图。

    ``certified_bar`` 覆盖同身份 raw 行并映射
    ``anchor_adj_factor -> adj_factor``；``certified_no_trade`` 不删除后来
    出现的 raw 行。返回值不落库。
    """
    raw_rows = load_month_bars(conn, month_ends)
    effective = {
        (str(row["month_end"]), str(row["stock_code"]).split(".")[0]): dict(row)
        for row in raw_rows
    }
    for fact in load_derived_month_facts(conn, month_ends):
        if fact["fact_status"] != "certified_bar":
            continue
        identity = (fact["month_end"], fact["stock_code"])
        raw = effective.get(identity, {})
        effective[identity] = {
            "month_end": fact["month_end"],
            "stock_code": fact["stock_code"],
            "stock_name": fact.get("stock_name") or raw.get("stock_name"),
            "open": fact["open"],
            "high": fact["high"],
            "low": fact["low"],
            "close": fact["close"],
            "volume": fact["volume"],
            "amount": fact["amount"],
            "adj_factor": fact["anchor_adj_factor"],
            "source": "derived_daily_certified",
            "fetched_at": fact["created_at"],
            "shape_certified": True,
            "derived_fact_status": fact["fact_status"],
            "derived_fact_hash": fact["fact_hash"],
            "formula_version": fact["formula_version"],
            "replacement_reason": fact["replacement_reason"],
            "raw_crosscheck_status": fact["raw_crosscheck_status"],
            "source_meta": fact["source_meta"],
        }
    return [
        effective[key]
        for key in sorted(effective, key=lambda item: (item[1], item[0]))
    ]


def load_effective_no_trade_facts(
    conn: sqlite3.Connection,
    month_ends: list[str],
) -> list[dict]:
    """只返回仍未被后来 raw 月线推翻的 ``certified_no_trade`` 事实。"""
    if not month_ends:
        return []
    raw_identities = {
        (str(row["month_end"]), str(row["stock_code"]).split(".")[0])
        for row in load_month_bars(conn, month_ends)
    }
    return [
        fact
        for fact in load_derived_month_facts(conn, month_ends)
        if fact["fact_status"] == "certified_no_trade"
        and (fact["month_end"], fact["stock_code"]) not in raw_identities
    ]


def compute_derived_fact_run_receipt_hash(run: dict) -> str:
    payload = {
        "run_id": str(run.get("run_id") or "").strip(),
        "input_by": str(run.get("input_by") or "").strip(),
        "status": str(run.get("status") or "").strip(),
        "request": run.get("request") or {},
        "counts": run.get("counts") or {},
        "receipt": run.get("receipt") or {},
    }
    return _canonical_json_hash(payload)


def save_derived_fact_run(conn: sqlite3.Connection, run: dict) -> None:
    """追加一条派生事实运行收据；重复 run_id 由主键直接拒绝。"""
    run_id = str(run.get("run_id") or "").strip()
    input_by = str(run.get("input_by") or "").strip()
    status = str(run.get("status") or "").strip()
    if not run_id:
        raise ValueError("run_id must not be empty")
    if not input_by:
        raise ValueError("input_by must not be empty")
    if status not in {"complete", "partial", "failed"}:
        raise ValueError("derived fact run status invalid")
    for field in ("request", "counts", "receipt"):
        value = run.get(field)
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be a mapping")
    expected_hash = compute_derived_fact_run_receipt_hash(run)
    if str(run.get("receipt_hash") or "").strip() != expected_hash:
        raise ValueError("derived fact run receipt_hash mismatch")
    conn.execute(
        """
        INSERT INTO monthly_pattern_derived_fact_runs (
            run_id, input_by, status, receipt_hash,
            request_json, counts_json, receipt_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            run_id,
            input_by,
            status,
            expected_hash,
            _dump(run.get("request")),
            _dump(run.get("counts")),
            _dump(run.get("receipt")),
        ),
    )


def load_derived_fact_runs(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
) -> list[dict]:
    if not _table_exists(conn, "monthly_pattern_derived_fact_runs"):
        return []
    if run_id is None:
        rows = conn.execute(
            """
            SELECT *
            FROM monthly_pattern_derived_fact_runs
            ORDER BY created_at, run_id
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM monthly_pattern_derived_fact_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
    output: list[dict] = []
    for row in rows:
        item = dict(row)
        item["request"] = _load(item.pop("request_json"), {})
        item["counts"] = _load(item.pop("counts_json"), {})
        item["receipt"] = _load(item.pop("receipt_json"), {})
        if compute_derived_fact_run_receipt_hash(item) != item["receipt_hash"]:
            raise ValueError(f"derived fact run receipt hash mismatch: {item['run_id']}")
        output.append(item)
    return output


def save_derived_fact_run_and_facts(
    conn: sqlite3.Connection,
    *,
    run: dict,
    facts: list[dict],
) -> dict[str, int | str]:
    """在调用方事务内原子追加 run，再写同 run 的事实。

    本函数使用 SAVEPOINT，成功后不 commit；调用方仍拥有最终事务边界。
    """
    if str(run.get("status") or "") == "failed" and facts:
        raise ValueError("failed derived fact run must not contain facts")
    conn.execute("SAVEPOINT monthly_pattern_derived_write")
    try:
        save_derived_fact_run(conn, run)
        result = save_derived_month_facts(
            conn,
            facts,
            first_run_id=str(run.get("run_id") or ""),
        )
        conn.execute("RELEASE SAVEPOINT monthly_pattern_derived_write")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT monthly_pattern_derived_write")
        conn.execute("RELEASE SAVEPOINT monthly_pattern_derived_write")
        raise
    return {"run_id": str(run["run_id"]), **result}


def save_financial_snapshots(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    observed_date: str | None = None,
    observed_at: str | None = None,
) -> None:
    """追加财务内容版本。

    上游修订可能沿用原公告日。若来源没有独立修订公开日，修订版只能从本机
    首次观测日开始用于 as-of，不能覆盖或回灌到更早的历史日期。
    """
    if not rows:
        return
    observation_time = observed_at
    if observation_time is None:
        observation_time = (
            f"{observed_date}T23:59:59.999999"
            if observed_date
            else datetime.now().astimezone().isoformat(timespec="microseconds")
        )
    try:
        parsed_observation = datetime.fromisoformat(
            observation_time.replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("observed_at 必须为 ISO-8601 datetime") from exc
    observation_day = observed_date or parsed_observation.date().isoformat()
    try:
        parsed_day = date.fromisoformat(observation_day)
    except (TypeError, ValueError) as exc:
        raise ValueError("observed_date 必须为 YYYY-MM-DD") from exc
    if observed_date and parsed_observation.date() != parsed_day:
        raise ValueError("observed_at 与 observed_date 日期不一致")
    if parsed_observation.tzinfo is None:
        parsed_observation = parsed_observation.replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    observation_time = (
        parsed_observation.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
    known_hashes: dict[tuple[str, str, str], set[str]] = {}
    prepared_rows = []
    for row in rows:
        stock_code = str(
            row.get("stock_code") or row.get("ts_code") or ""
        ).split(".")[0]
        identity = (
            stock_code,
            row["report_period"],
            row["financial_ann_date"],
        )
        snapshot_hash = _financial_payload_hash(row)
        if identity not in known_hashes:
            existing = conn.execute(
                """
                SELECT snapshot_hash
                FROM monthly_pattern_financial_snapshots
                WHERE stock_code = ?
                  AND report_period = ?
                  AND financial_ann_date = ?
                """,
                identity,
            ).fetchall()
            known_hashes[identity] = {str(item[0]) for item in existing}
        content_changed = bool(
            known_hashes[identity]
            and snapshot_hash not in known_hashes[identity]
        )
        base_visible_date = (
            str(row.get("version_visible_date") or "")
            or row["financial_ann_date"]
        )
        version_visible_date = (
            max(base_visible_date, observation_day)
            if _is_revision_sensitive(row) or content_changed
            else base_visible_date
        )
        prepared_rows.append(
            (
                stock_code,
                row["report_period"],
                row["financial_ann_date"],
                version_visible_date,
                observation_time,
                snapshot_hash,
                _dump(row.get("fina_indicator")),
                _dump(row.get("balancesheet")),
                _dump(row.get("income")),
                _dump(row.get("source_meta")),
            )
        )
        known_hashes[identity].add(snapshot_hash)
    conn.executemany(
        """
        INSERT INTO monthly_pattern_financial_snapshots (
            stock_code, report_period, financial_ann_date,
            version_visible_date, version_observed_at, snapshot_hash,
            fina_indicator_json, balancesheet_json, income_json,
            source_meta_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(
            stock_code, report_period, financial_ann_date, snapshot_hash
        ) DO NOTHING
        """,
        prepared_rows,
    )


def load_financial_snapshots(
    conn: sqlite3.Connection,
    *,
    as_of_date: str,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT stock_code, report_period, financial_ann_date,
               version_visible_date, version_observed_at, snapshot_hash,
               fina_indicator_json, balancesheet_json, income_json,
               source_meta_json, created_at
        FROM monthly_pattern_financial_snapshots
        WHERE financial_ann_date <= ? AND version_visible_date <= ?
        ORDER BY stock_code, report_period, financial_ann_date,
                 version_visible_date, version_observed_at,
                 created_at, snapshot_hash
        """,
        (as_of_date, as_of_date),
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["fina_indicator"] = _load(item.pop("fina_indicator_json"), {})
        item["balancesheet"] = _load(item.pop("balancesheet_json"), {})
        item["income"] = _load(item.pop("income_json"), {})
        item["source_meta"] = _load(item.pop("source_meta_json"), {})
        output.append(item)
    return output


def save_run(
    conn: sqlite3.Connection,
    *,
    scan_date: str,
    signal_month: str | None,
    status: str,
    input_by: str,
    source_status: dict,
    counts: dict,
    error: str | None,
) -> None:
    requester = str(input_by or "").strip()
    if not requester:
        raise ValueError("input_by must not be empty")
    conn.execute(
        """
        INSERT INTO monthly_pattern_runs (
            scan_date, signal_month, status, input_by, source_status_json,
            counts_json, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(scan_date) DO UPDATE SET
            signal_month=excluded.signal_month,
            status=excluded.status,
            input_by=excluded.input_by,
            source_status_json=excluded.source_status_json,
            counts_json=excluded.counts_json,
            error=excluded.error,
            updated_at=datetime('now')
        """,
        (
            scan_date,
            signal_month,
            status,
            requester,
            _dump(source_status),
            _dump(counts),
            error,
        ),
    )


def get_run(conn: sqlite3.Connection, scan_date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM monthly_pattern_runs WHERE scan_date = ?",
        (scan_date,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["source_status"] = _load(item.pop("source_status_json"), {})
    item["counts"] = _load(item.pop("counts_json"), {})
    return item
