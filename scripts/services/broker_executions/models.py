from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawRow:
    row_index: int
    payload: dict[str, str]


@dataclass
class NormalizedRow:
    account_id: str
    broker_code: str | None
    biz_date: str
    exec_time: str | None
    stock_code_raw: str
    stock_code: str
    stock_name: str
    market: str
    market_raw: str | None
    direction: str
    direction_raw: str
    shares: int
    price: float
    amount: float
    net_amount: float | None
    balance_after: int | None
    commission: float
    stamp_duty: float
    transfer_fee: float
    exchange_fee: float
    regulatory_fee: float
    other_fees: float
    total_fees: float
    broker_contract_no: str | None
    broker_trade_no: str | None
    currency: str
    raw_payload_json: str
    source_file: str
    source_format: str
    source_archive_path: str | None
    input_by: str
    import_run_id: str
    notes: str | None
    row_index: int = -1
    _dedupe_mode: str = "strict"


@dataclass
class RowSummary:
    row_index: int
    biz_date: str
    exec_time: str | None
    stock_code: str
    stock_name: str
    direction: str
    shares: int
    price: float
    broker_trade_no: str | None


@dataclass
class ConflictRow:
    summary: RowSummary
    diffs: dict[str, tuple[Any, Any]]


@dataclass
class ErrorRow:
    row_index: int
    reason: str
    raw: dict[str, str]


@dataclass
class ImportReport:
    source_file: str
    source_format: str
    import_run_id: str
    parsed: int
    inserted: list[RowSummary] = field(default_factory=list)
    skipped: list[RowSummary] = field(default_factory=list)
    conflicts: list[ConflictRow] = field(default_factory=list)
    degraded: list[RowSummary] = field(default_factory=list)
    errors: list[ErrorRow] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    dry_run: bool = False
    archive_path: str | None = None
    report_path: str | None = None
    # plan I 系列:thesis 中间层触发提示(dry-run / 实写都填,仅作参考)
    thesis_triggers: list[dict] = field(default_factory=list)
    auto_closed_thesis_ids: list[int] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# Broker Executions Import Report",
            "",
            "## Summary",
            "",
            f"- source_file: {self.source_file}",
            f"- source_format: {self.source_format}",
            f"- import_run_id: {self.import_run_id}",
            f"- parsed: {self.parsed}",
            f"- inserted: {len(self.inserted)}",
            f"- skipped: {len(self.skipped)}",
            f"- conflicts: {len(self.conflicts)}",
            f"- degraded: {len(self.degraded)}",
            f"- errors: {len(self.errors)}",
            f"- dry_run: {self.dry_run}",
            f"- archive_path: {self.archive_path or ''}",
            f"- report_path: {self.report_path or ''}",
            f"- started_at: {self.started_at}",
            f"- finished_at: {self.finished_at}",
            "",
        ]
        lines.extend(self._summary_section("Inserted", self.inserted))
        lines.extend(self._summary_section("Skipped", self.skipped))
        lines.extend(self._conflicts_section())
        lines.extend(self._summary_section("Degraded", self.degraded))
        lines.extend(self._errors_section())
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _summary_section(title: str, rows: list[RowSummary]) -> list[str]:
        lines = [f"## {title}", ""]
        if not rows:
            lines.extend(["None.", ""])
            return lines
        lines.extend([
            "| row | biz_date | exec_time | stock_code | stock_name | direction | shares | price | broker_trade_no |",
            "|---:|---|---|---|---|---|---:|---:|---|",
        ])
        for row in rows:
            lines.append(
                "| "
                f"{row.row_index} | {row.biz_date} | {row.exec_time or ''} | "
                f"{row.stock_code} | {row.stock_name} | {row.direction} | "
                f"{row.shares} | {row.price:.3f} | {row.broker_trade_no or ''} |"
            )
        lines.append("")
        return lines

    def _conflicts_section(self) -> list[str]:
        lines = ["## Conflicts", ""]
        if not self.conflicts:
            lines.extend(["None.", ""])
            return lines
        for conflict in self.conflicts:
            summary = conflict.summary
            lines.append(
                f"### Row {summary.row_index} - {summary.biz_date} "
                f"{summary.stock_code} {summary.direction}"
            )
            lines.append("")
            lines.extend([
                "| field | existing | incoming |",
                "|---|---|---|",
            ])
            for field_name, (existing, incoming) in conflict.diffs.items():
                lines.append(f"| {field_name} | {existing} | {incoming} |")
            lines.append("")
        return lines

    def _errors_section(self) -> list[str]:
        lines = ["## Errors", ""]
        if not self.errors:
            lines.extend(["None.", ""])
            return lines
        lines.extend([
            "| row | reason | raw |",
            "|---:|---|---|",
        ])
        for error in self.errors:
            lines.append(f"| {error.row_index} | {error.reason} | {error.raw} |")
        lines.append("")
        return lines
