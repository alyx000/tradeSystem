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
