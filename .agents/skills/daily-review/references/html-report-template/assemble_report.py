#!/usr/bin/env python3
"""组装并校验每日多 Agent 盘后复盘 HTML（compact-v1）。

用法：
    python3 assemble_report.py <TMP目录> <YYYY-MM-DD> [--output PATH]

TMP 必须包含 8 个 chunk：
    b<DATE>_{head,s0,s1,s2,s456,s7t,proj,s8ops}.html

默认输出 ``data/reports/复盘_<DATE>.html``；``--output`` 可用于生成不覆盖正式
档案的验收样例。组装器只做确定性校验，不会在超限时自动删字或截表。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date as date_type, timedelta
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Sequence


REPORT_SCHEMA = "compact-v1"
CAPACITY_MANIFEST_SCHEMA = "capacity-health-v1"
NEW_HIGH_MANIFEST_SCHEMA = "rolling-new-high-structure-v1"
CAPACITY_MIN_UNIVERSE = 4_000
CAPACITY_MAX_REPORT_LAG_DAYS = 10
CAPACITY_MAX_TRADE_GAP_DAYS = 10
NEW_HIGH_MAX_TRADE_GAP_DAYS = 14
CHUNK_ORDER = ("head", "s0", "s1", "s2", "s456", "s7t", "proj", "s8ops")
ANCHOR_MAP = {
    "head": ("tldr", "factor"),
    "s0": ("s0",),
    "s1": ("s1",),
    "s2": ("s2",),
    "s456": ("s3", "s4", "s5", "s6"),
    "s7t": ("s7", "teachers", "industry", "cognition"),
    "proj": ("proj",),
    "s8ops": ("s8", "ops"),
}
REQUIRED_ANCHORS = tuple(anchor for chunk in CHUNK_ORDER for anchor in ANCHOR_MAP[chunk])
NAV_LABELS = {
    "tldr": "速览",
    "s0": "判分",
    "s1": "大盘",
    "s2": "板块",
    "s3": "情绪",
    "s4": "风格",
    "s5": "龙头",
    "s6": "节点",
    "s7": "持仓",
    "teachers": "老师",
    "industry": "行业",
    "cognition": "认知",
    "factor": "因子",
    "proj": "推演",
    "s8": "计划",
    "ops": "缺口",
}
NAV = tuple((anchor, NAV_LABELS[anchor]) for anchor in REQUIRED_ANCHORS)

TLDR_CHAR_LIMIT = 500
VISIBLE_CHAR_TARGET = 6_000
VISIBLE_CHAR_LIMIT = 10_000
VISIBLE_TABLE_LIMIT = 12
VISIBLE_ROW_LIMIT = 80
EVIDENCE_CHAR_LIMIT = 40_000
EVIDENCE_TABLE_LIMIT = 60
EVIDENCE_ROW_LIMIT = 400
FACTOR_MODES = frozenset({"formal", "rule_only", "shadow", "no_data"})
FACTOR_STATUS_TEXTS = {
    "formal": frozenset({"[事实]状态：正式factor-score已完成"}),
    "rule_only": frozenset({"[事实]状态：rule_only结果，仅作只读引用"}),
    "shadow": frozenset(
        {
            "[事实]状态：本日未运行正式factor-score；本日仅影子口径，不写库",
            "[事实]状态：本日尚未评分；本日仅影子口径，不写库",
            "[事实]状态：正式factor-score完成条件未满足；本日仅影子口径，不写库",
        }
    ),
}
FACTOR_SHADOW_STALE_STATUS_RE = re.compile(
    r"\A\[事实\]状态：正式factor-score停在(?:\d{4}|\d{4}-\d{2}-\d{2})；"
    r"本日仅影子口径，不写库\Z"
)
FACTOR_NO_DATA_STATEMENTS = frozenset(
    {"[事实]本日无可判数据", "[事实]本日无新增"}
)
FACTOR_NO_DATA_INLINE_TAGS = frozenset(
    {"b", "code", "em", "i", "p", "span", "strong"}
)
FACTOR_DETAIL_KEYS = (
    "market_node",
    "sector_rhythm",
    "style_regime",
    "leader_signal",
)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SCRIPT_NETWORK_RE = re.compile(
    r"(?:\bfetch\s*\(|\bXMLHttpRequest\b|\bWebSocket\s*\(|\bEventSource\s*\(|"
    r"\bsendBeacon\s*\(|\bimport\s*\()",
    re.IGNORECASE,
)
CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)
CSS_URL_RE = re.compile(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
RESOURCE_ATTRIBUTES = {
    "audio": ("src",),
    "base": ("href",),
    "embed": ("src",),
    "feimage": ("href", "xlink:href"),
    "iframe": ("src",),
    "img": ("src", "srcset"),
    "image": ("href", "xlink:href"),
    "input": ("src",),
    "link": ("href",),
    "object": ("data",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "use": ("href", "xlink:href"),
    "video": ("src", "poster"),
}
ACTIVE_RESOURCE_TAGS = {"base", "embed", "iframe", "link", "object", "script"}
CAPACITY_NONE_TEXT = "[事实]本日无可确认容量中军"
CAPACITY_MISSING_TEXT = "[事实]容量排名数据不完整，本日无法判定"
CAPACITY_SOURCE_STATUSES = frozenset({"complete", "partial", "failed"})
CAPACITY_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
STRUCTURED_CONTRACT_ATTRIBUTES = (
    "data-sector-concentration",
    "data-rising-recognition",
    "data-falling-recognition",
    "data-new-high-structure",
    "data-event-window",
)
SECTOR_CONCENTRATION_NONE_TEXT = "[事实]本日无可用板块集中度数据"
SECTOR_CONCENTRATION_MISSING_TEXT = "[事实]板块集中度数据不完整，本日无法判定"
RISING_RECOGNITION_NONE_TEXT = "[事实]本日无符合规则的主升辨识度个股"
RISING_RECOGNITION_MISSING_TEXT = "[事实]主升辨识度矩阵数据不完整，本日无法判定"
FALLING_RECOGNITION_NONE_TEXT = "[事实]本日无符合规则的主跌辨识度个股"
FALLING_RECOGNITION_MISSING_TEXT = "[事实]主跌辨识度矩阵数据不完整，本日无法判定"
NEW_HIGH_STRUCTURE_NONE_TEXT = "[事实]本日无符合60/120/250日滚动新高口径的个股"
NEW_HIGH_STRUCTURE_MISSING_TEXT = "[事实]滚动新高结构数据不完整，本日无法判定"
EVENT_WINDOW_NONE_TEXT = "[事实]未来7个自然日无影响次日验证的新增事件"
EVENT_WINDOW_MISSING_TEXT = "[事实]未来7个自然日事件窗数据不完整，本日无法判定"
CSS_HIDDEN_CLASSES = frozenset(
    {
        "toc",
        "mobile-chapters",
        "back-to-top",
        "reader-sidebar",
        "reader-brand",
        "reader-search",
        "evidence-toggle",
    }
)


@dataclass
class SectionMetrics:
    visible_chars: int = 0
    visible_tables: int = 0
    visible_rows: int = 0
    evidence_chars: int = 0
    evidence_tables: int = 0
    evidence_rows: int = 0


@dataclass
class ReportMetrics:
    tldr_chars: int
    visible_chars: int
    visible_tables: int
    visible_rows: int
    evidence_chars: int
    evidence_tables: int
    evidence_rows: int
    sections: dict[str, SectionMetrics] = field(default_factory=dict)
    visible_target_exceeded: bool = False

    @property
    def appendix_chars(self) -> int:
        """兼容文档中的“附录”命名；与 evidence_chars 为同一预算。"""

        return self.evidence_chars

    @property
    def appendix_tables(self) -> int:
        return self.evidence_tables

    @property
    def appendix_rows(self) -> int:
        return self.evidence_rows


class ReportValidationError(ValueError):
    """带稳定错误码和责任章节的报告校验异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        section: str | None = None,
        metrics: ReportMetrics | None = None,
    ) -> None:
        self.code = code
        self.section = section
        self.metrics = metrics
        super().__init__(message)

    def __str__(self) -> str:
        location = f" section={self.section}" if self.section else ""
        return f"[{self.code}]{location} {super().__str__()}"


@dataclass
class _Evidence:
    section: str | None
    as_of: str
    items: str
    kind: str = ""
    summary_count: int = 0
    summary_text: list[str] = field(default_factory=list)
    first_child_is_summary: bool = True
    child_elements: int = 0
    body_chars: int = 0
    body_artifacts: int = 0
    body_text: list[str] = field(default_factory=list)


@dataclass
class _Claim:
    claim_id: str
    kind: str
    source: str
    as_of: str
    section: str | None
    in_evidence_body: bool
    default_hidden: bool
    text: list[str] = field(default_factory=list)
    visible_text: list[str] = field(default_factory=list)


@dataclass
class _FactorItem:
    default_hidden: bool
    visible_text: list[str] = field(default_factory=list)


@dataclass
class _CapacityCell:
    tag: str
    text: list[str] = field(default_factory=list)


@dataclass
class _CapacityRow:
    attrs: dict[str, str]
    text: list[str] = field(default_factory=list)
    cells: list[_CapacityCell] = field(default_factory=list)


@dataclass
class _CapacityTable:
    section: str | None
    attrs: dict[str, str]
    rows: list[_CapacityRow] = field(default_factory=list)


@dataclass
class _CapacityNoData:
    section: str | None
    attrs: dict[str, str]
    text: list[str] = field(default_factory=list)


@dataclass
class _StructuredRow:
    attrs: dict[str, str]
    text: list[str] = field(default_factory=list)


@dataclass
class _StructuredContract:
    name: str
    value: str
    tag: str
    section: str | None
    attrs: dict[str, str]
    default_hidden: bool
    text: list[str] = field(default_factory=list)
    rendered_text: list[str] = field(default_factory=list)
    rows: list[_StructuredRow] = field(default_factory=list)


@dataclass
class _Frame:
    tag: str
    element_id: str | None = None
    chunk: str | None = None
    evidence: _Evidence | None = None
    evidence_summary: bool = False
    claim: _Claim | None = None
    default_hidden: bool = False
    explicit_hidden: bool = False
    factor_role: str | None = None
    factor_item: _FactorItem | None = None
    heading_text: list[str] | None = None
    capacity_table: _CapacityTable | None = None
    capacity_row: _CapacityRow | None = None
    capacity_cell: _CapacityCell | None = None
    capacity_none: _CapacityNoData | None = None
    capacity_tbody: bool = False
    structured_contract: _StructuredContract | None = None
    structured_row: _StructuredRow | None = None


def _compact_char_count(value: str) -> int:
    return sum(1 for char in value if not char.isspace())


def _has_labeled_content(parts: Sequence[str]) -> bool:
    compact = "".join("".join(parts).split())
    has_label = "[事实]" in compact or "[判断]" in compact
    substantive = compact.replace("[事实]", "").replace("[判断]", "")
    return has_label and bool(substantive)


def _valid_factor_status(mode: str, value: str) -> bool:
    normalized = re.sub(r"\s+", "", value).rstrip("。.;；")
    if normalized in FACTOR_STATUS_TEXTS.get(mode, frozenset()):
        return True
    return bool(
        mode == "shadow" and FACTOR_SHADOW_STALE_STATUS_RE.fullmatch(normalized)
    )


def _valid_date(value: str) -> bool:
    if not DATE_RE.fullmatch(value):
        return False
    try:
        date_type.fromisoformat(value)
    except ValueError:
        return False
    return True


def _cell_text(cell: _CapacityCell) -> str:
    return re.sub(r"\s+", "", "".join(cell.text))


def _capacity_visible_fields(row: _CapacityRow) -> dict[str, str]:
    """读取容量表的两种受支持列布局，并返回页面真正可见的资格字段。"""

    cells = [_cell_text(cell) for cell in row.cells]
    if len(cells) == 7:
        return {
            "code": cells[0].upper(),
            "name": cells[1],
            "direction": cells[2],
            "tier": cells[3],
            "market_rank": cells[4],
            "direction_rank": cells[5],
            "top50_days": cells[6],
            "amount_text": "",
        }
    if len(cells) >= 9:
        ranks = cells[3].split("/")
        top50 = cells[4].split("/")
        return {
            "code": row.attrs.get("data-code", "").upper(),
            "name": cells[2],
            "direction": cells[1],
            "tier": cells[0],
            "market_rank": ranks[0] if len(ranks) == 2 else "",
            "direction_rank": ranks[1] if len(ranks) == 2 else "",
            "top50_days": top50[0] if len(top50) == 2 and top50[1] == "5" else "",
            "amount_text": cells[5],
        }
    return {}


def _is_embedded_resource(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or normalized.startswith(("data:", "#"))


def _has_external_css(css: str) -> bool:
    if CSS_IMPORT_RE.search(css):
        return True
    return any(
        not _is_embedded_resource(match.group(2)) for match in CSS_URL_RE.finditer(css)
    )


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Frame] = []
        self.errors: list[tuple[str, str, str | None]] = []
        self.sections = {anchor: SectionMetrics() for anchor in REQUIRED_ANCHORS}
        self.unscoped = SectionMetrics()
        self.ids: dict[str, int] = {}
        self.anchor_chunks: dict[str, list[str | None]] = {
            anchor: [] for anchor in REQUIRED_ANCHORS
        }
        self.anchors_seen: list[str] = []
        self.chunks: list[str] = []
        self.schema_hosts: list[tuple[str, str, str | None, str]] = []
        self.evidences: list[_Evidence] = []
        self.claims: dict[str, _Claim] = {}
        self.claim_refs: list[tuple[str, str, str | None]] = []
        self.visible_document_text: list[str] = []
        self.factor_hosts: list[tuple[str, frozenset[str]]] = []
        self.factor_modes: list[str] = []
        self.factor_items: list[_FactorItem] = []
        self.factor_statuses = 0
        self.factor_no_data_statements = 0
        self.factor_section_hidden = False
        self.factor_hidden_contract_elements = 0
        self.factor_no_data_forbidden_elements = 0
        self.factor_other_elements = 0
        self.factor_status_text: list[str] = []
        self.factor_no_data_text: list[str] = []
        self.factor_other_text: list[str] = []
        self.document_text: list[str] = []
        self.section_text: dict[str, list[str]] = {
            anchor: [] for anchor in REQUIRED_ANCHORS
        }
        self.capacity_tables: list[_CapacityTable] = []
        self.capacity_none_states: list[_CapacityNoData] = []
        self.capacity_heading_pending = False
        self.structured_contracts: dict[str, list[_StructuredContract]] = {
            name: [] for name in STRUCTURED_CONTRACT_ATTRIBUTES
        }

    def _error(self, code: str, message: str, section: str | None = None) -> None:
        self.errors.append((code, message, section))

    def _current_chunk(self) -> str | None:
        for frame in reversed(self.stack):
            if frame.chunk:
                return frame.chunk
        return None

    def _current_section(self) -> str | None:
        for frame in reversed(self.stack):
            if frame.element_id in REQUIRED_ANCHORS:
                return frame.element_id
        return None

    def _inside_report_document(self) -> bool:
        return any(frame.element_id == "report-document" for frame in self.stack)

    def _metrics_bucket(self, section: str | None) -> SectionMetrics:
        return self.sections[section] if section else self.unscoped

    def _current_evidence(self) -> _Evidence | None:
        for frame in reversed(self.stack):
            if frame.evidence:
                return frame.evidence
        return None

    def _in_evidence_summary(self) -> bool:
        return any(frame.evidence_summary for frame in self.stack)

    def _in_evidence_body(self) -> bool:
        return self._current_evidence() is not None and not self._in_evidence_summary()

    def _current_claim(self) -> _Claim | None:
        for frame in reversed(self.stack):
            if frame.claim:
                return frame.claim
        return None

    def _in_default_hidden(self) -> bool:
        return any(frame.default_hidden for frame in self.stack)

    def _current_factor_role(self) -> str | None:
        for frame in reversed(self.stack):
            if frame.factor_role:
                return frame.factor_role
        return None

    def _inside_tag(self, tag: str) -> bool:
        return any(frame.tag == tag for frame in self.stack)

    def _current_factor_item(self) -> _FactorItem | None:
        for frame in reversed(self.stack):
            if frame.factor_item:
                return frame.factor_item
        return None

    def _current_capacity_table(self) -> _CapacityTable | None:
        for frame in reversed(self.stack):
            if frame.capacity_table:
                return frame.capacity_table
        return None

    def _current_capacity_row(self) -> _CapacityRow | None:
        for frame in reversed(self.stack):
            if frame.capacity_row:
                return frame.capacity_row
        return None

    def _current_capacity_cell(self) -> _CapacityCell | None:
        for frame in reversed(self.stack):
            if frame.capacity_cell:
                return frame.capacity_cell
        return None

    def _inside_capacity_tbody(self) -> bool:
        return any(frame.capacity_tbody for frame in self.stack)

    def _current_structured_contract(self) -> _StructuredContract | None:
        for frame in reversed(self.stack):
            if frame.structured_contract:
                return frame.structured_contract
        return None

    def _current_structured_row(self) -> _StructuredRow | None:
        for frame in reversed(self.stack):
            if frame.structured_row:
                return frame.structured_row
        return None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_names = [name.lower() for name, _ in attrs_list]
        duplicate_attr = next(
            (name for name in attr_names if attr_names.count(name) > 1), None
        )
        if duplicate_attr:
            self._error(
                "duplicate_attribute",
                f"<{tag}> 属性重复：{duplicate_attr}",
                self._current_section(),
            )
        attrs = {name.lower(): (value or "") for name, value in attrs_list}
        compact_style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        classes = set(attrs.get("class", "").split())
        explicit_hidden = (
            "hidden" in attrs
            or attrs.get("aria-hidden", "").strip().lower() == "true"
            or "display:none" in compact_style
            or "visibility:hidden" in compact_style
            or bool(classes & CSS_HIDDEN_CLASSES)
        )
        default_hidden = (
            self._in_default_hidden()
            or explicit_hidden
            or (tag == "details" and "open" not in attrs)
        )
        inside_document_scope = self._inside_report_document() or (
            attrs.get("id") == "report-document"
        )
        if inside_document_scope and (
            tag in {"embed", "form", "iframe", "object", "script", "style"}
        ):
            self._error(
                "external_dependency",
                f"report-document 内不允许活动内容或内联样式：<{tag}>",
                self._current_section(),
            )
        for resource_attr in RESOURCE_ATTRIBUTES.get(tag, ()):
            value = attrs.get(resource_attr, "")
            if value and (
                tag in ACTIVE_RESOURCE_TAGS
                or resource_attr == "srcset"
                or not _is_embedded_resource(value)
            ):
                self._error(
                    "external_dependency",
                    f"<{tag}> 的 {resource_attr} 引用了外部资源",
                    self._current_section(),
                )
        if tag == "iframe" and "srcdoc" in attrs:
            self._error(
                "external_dependency",
                "iframe srcdoc 不允许出现在单文件报告中",
                self._current_section(),
            )
        if tag == "meta" and attrs.get("http-equiv", "").strip().lower() == "refresh":
            self._error(
                "external_dependency",
                "meta refresh 不允许出现在单文件报告中",
                self._current_section(),
            )
        for attr_name, attr_value in attrs.items():
            if attr_name.startswith("on") and (
                inside_document_scope or SCRIPT_NETWORK_RE.search(attr_value)
            ):
                self._error(
                    "external_dependency",
                    f"<{tag}> 的 {attr_name} 包含网络运行时调用",
                    self._current_section(),
                )
            if (
                attr_name in {"href", "action", "formaction"}
                and attr_value.strip().lower().startswith("javascript:")
                and (inside_document_scope or SCRIPT_NETWORK_RE.search(attr_value))
            ):
                self._error(
                    "external_dependency",
                    f"<{tag}> 的 {attr_name} 包含网络运行时调用",
                    self._current_section(),
                )
        if _has_external_css(attrs.get("style", "")):
            self._error(
                "external_dependency",
                f"<{tag}> 的 style 引用了外部资源",
                self._current_section(),
            )
        parent = self.stack[-1] if self.stack else None
        element_id = attrs.get("id") or None
        section_for_element = self._current_section()
        if element_id in REQUIRED_ANCHORS:
            section_for_element = element_id

        if self.capacity_heading_pending and section_for_element == "s5":
            if tag == "table" and attrs.get("data-capacity-health") == "v1":
                self.capacity_heading_pending = False
            else:
                self._error(
                    "invalid_capacity_health",
                    "中军健康度标题后必须立即放置结构化容量表",
                    section="s5",
                )
                self.capacity_heading_pending = False

        capacity_table: _CapacityTable | None = None
        capacity_none: _CapacityNoData | None = None
        capacity_health = attrs.get("data-capacity-health")
        if capacity_health is not None:
            if section_for_element != "s5":
                self._error(
                    "invalid_capacity_health",
                    "容量健康度契约只能位于 s5",
                    section=section_for_element,
                )
            elif tag == "table" and capacity_health == "v1":
                capacity_table = _CapacityTable(
                    section=section_for_element,
                    attrs=attrs,
                )
                self.capacity_tables.append(capacity_table)
            elif tag == "p" and capacity_health in {"none", "missing-data"}:
                capacity_none = _CapacityNoData(
                    section=section_for_element,
                    attrs=attrs,
                )
                self.capacity_none_states.append(capacity_none)
            else:
                self._error(
                    "invalid_capacity_health",
                    "容量健康度仅允许 table[v1]、p[none] 或 p[missing-data]",
                    section="s5",
                )

        structured_names = [
            name for name in STRUCTURED_CONTRACT_ATTRIBUTES if name in attrs
        ]
        structured_contract: _StructuredContract | None = None
        if len(structured_names) > 1:
            self._error(
                "invalid_structured_contract",
                f"<{tag}> 不得同时承载多份结构契约",
                section=section_for_element,
            )
        elif structured_names:
            structured_name = structured_names[0]
            structured_contract = _StructuredContract(
                name=structured_name,
                value=attrs[structured_name],
                tag=tag,
                section=section_for_element,
                attrs=attrs,
                default_hidden=default_hidden,
            )
            self.structured_contracts[structured_name].append(structured_contract)

        current_structured_contract = self._current_structured_contract()
        structured_row: _StructuredRow | None = None
        if tag == "tr" and current_structured_contract:
            structured_row = _StructuredRow(attrs=attrs)
            current_structured_contract.rows.append(structured_row)

        current_capacity_table = self._current_capacity_table()
        capacity_row: _CapacityRow | None = None
        if tag == "tr" and current_capacity_table and self._inside_capacity_tbody():
            capacity_row = _CapacityRow(attrs=attrs)
            current_capacity_table.rows.append(capacity_row)
        current_capacity_row = self._current_capacity_row()
        capacity_cell: _CapacityCell | None = None
        if tag in {"td", "th"} and current_capacity_row:
            capacity_cell = _CapacityCell(tag=tag)
            current_capacity_row.cells.append(capacity_cell)
        capacity_tbody = bool(tag == "tbody" and current_capacity_table)
        heading_text = [] if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} else None
        if element_id == "factor":
            self.factor_hosts.append((tag, frozenset(classes)))
            self.factor_modes.append(attrs.get("data-factor-mode", ""))
            self.factor_section_hidden = default_hidden
        factor_role = attrs.get("data-factor-role") or None
        factor_item: _FactorItem | None = None
        if (
            tag == "li"
            and self._current_section() == "factor"
            and not self._in_evidence_body()
        ):
            factor_item = _FactorItem(default_hidden=default_hidden)
            self.factor_items.append(factor_item)

        current_evidence = self._current_evidence()
        if (
            current_evidence
            and self._in_evidence_body()
            and (
                tag == "table"
                or (
                    tag in {"audio", "img", "source", "track", "video"}
                    and any(attrs.get(name) for name in RESOURCE_ATTRIBUTES[tag])
                )
            )
        ):
            current_evidence.body_artifacts += 1
        if current_evidence and parent and parent.evidence is current_evidence:
            current_evidence.child_elements += 1
            if current_evidence.child_elements == 1 and tag != "summary":
                current_evidence.first_child_is_summary = False

        chunk = attrs.get("data-report-chunk") or None
        if chunk:
            self.chunks.append(chunk)
        schema = attrs.get("data-report-schema")
        if schema is not None:
            self.schema_hosts.append(
                (schema, tag, element_id, attrs.get("data-report-date", ""))
            )

        if element_id:
            self.ids[element_id] = self.ids.get(element_id, 0) + 1

        evidence: _Evidence | None = None
        if tag == "details" and "evidence" in classes:
            if current_evidence:
                self._error("nested_evidence", "evidence 不允许嵌套", self._current_section())
            if "open" in attrs:
                self._error(
                    "evidence_default_open",
                    "evidence 必须默认收起，不得带 open 属性",
                    self._current_section(),
                )
            evidence = _Evidence(
                section=self._current_section(),
                as_of=attrs.get("data-as-of", ""),
                items=attrs.get("data-items", ""),
                kind=attrs.get("data-evidence-kind", ""),
            )
            self.evidences.append(evidence)

        is_summary = bool(
            tag == "summary" and parent and parent.evidence is not None
        )
        if is_summary and parent and parent.evidence:
            parent.evidence.summary_count += 1

        section_for_claim = self._current_section()
        if element_id in REQUIRED_ANCHORS:
            section_for_claim = element_id

        claim: _Claim | None = None
        if element_id and element_id.startswith("claim-"):
            claim = _Claim(
                claim_id=element_id,
                kind=attrs.get("data-claim-kind", ""),
                source=attrs.get("data-source", ""),
                as_of=attrs.get("data-as-of", ""),
                section=section_for_claim,
                in_evidence_body=self._in_evidence_body(),
                default_hidden=default_hidden,
            )
            if element_id in self.claims:
                self._error("duplicate_claim", f"claim owner 重复：{element_id}", section_for_claim)
            else:
                self.claims[element_id] = claim

        claim_ref = attrs.get("data-claim-ref")
        href = attrs.get("href", "")
        if href.startswith("#claim-") and claim_ref is None:
            self._error(
                "invalid_claim_ref",
                f"claim 链接必须带 data-claim-ref：{href}",
                section_for_claim,
            )
        if claim_ref is not None:
            self.claim_refs.append((claim_ref, href, section_for_claim))

        frame = _Frame(
            tag=tag,
            element_id=element_id,
            chunk=chunk,
            evidence=evidence,
            evidence_summary=is_summary,
            claim=claim,
            default_hidden=default_hidden,
            explicit_hidden=explicit_hidden,
            factor_role=factor_role,
            factor_item=factor_item,
            heading_text=heading_text,
            capacity_table=capacity_table,
            capacity_row=capacity_row,
            capacity_cell=capacity_cell,
            capacity_none=capacity_none,
            capacity_tbody=capacity_tbody,
            structured_contract=structured_contract,
            structured_row=structured_row,
        )
        if tag not in VOID_TAGS:
            self.stack.append(frame)

        inside_document = self._inside_report_document()
        if chunk and not inside_document:
            self._error(
                "invalid_chunk_host",
                f"chunk {chunk} 必须位于 article#report-document 内",
                self._current_section(),
            )
        if element_id in REQUIRED_ANCHORS and not inside_document:
            self._error(
                "invalid_anchor",
                f"锚点 {element_id} 必须位于 article#report-document 内",
                element_id,
            )

        if element_id in REQUIRED_ANCHORS:
            self.anchor_chunks[element_id].append(self._current_chunk())
            self.anchors_seen.append(element_id)

        section = self._current_section()
        if section == "factor" and not self._in_evidence_body():
            current_role = self._current_factor_role()
            in_heading = self._inside_tag("h2")
            if (
                element_id != "factor"
                and not in_heading
                and current_role != "no-data"
            ):
                self.factor_other_elements += 1
            if current_role == "no-data" and tag not in FACTOR_NO_DATA_INLINE_TAGS:
                self.factor_no_data_forbidden_elements += 1
            if not self._in_default_hidden() and factor_role == "status":
                self.factor_statuses += 1
            if not self._in_default_hidden() and factor_role == "no-data":
                self.factor_no_data_statements += 1
            if self._in_default_hidden() and (
                tag == "li"
                or factor_role in {"status", "no-data"}
                or claim is not None
            ):
                self.factor_hidden_contract_elements += 1
        if self._inside_report_document() and tag in {"table", "tr"}:
            metrics = self._metrics_bucket(section)
            if self._in_evidence_body():
                if tag == "table":
                    metrics.evidence_tables += 1
                else:
                    metrics.evidence_rows += 1
            elif tag == "table":
                metrics.visible_tables += 1
            else:
                metrics.visible_rows += 1

    def handle_startendtag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs_list)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        if not self.stack:
            self._error("unbalanced_tag", f"多余闭合标签 </{tag}>")
            return
        if self.stack[-1].tag != tag:
            self._error(
                "unbalanced_tag",
                f"标签嵌套不配平：期望 </{self.stack[-1].tag}>，实际 </{tag}>",
                self._current_section(),
            )
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index].tag == tag:
                    del self.stack[index:]
                    return
            return
        frame = self.stack.pop()
        if frame.heading_text is not None and frame.element_id is None:
            heading = re.sub(r"\s+", "", "".join(frame.heading_text))
            if self._current_section() == "s5" and "中军健康度" in heading:
                self.capacity_heading_pending = True

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1].tag == "script":
            if SCRIPT_NETWORK_RE.search(data):
                self._error(
                    "external_dependency",
                    "script 包含网络运行时调用",
                    self._current_section(),
                )
            return
        if self.stack and self.stack[-1].tag == "style":
            if _has_external_css(data):
                self._error(
                    "external_dependency",
                    "style 包含外部资源",
                    self._current_section(),
                )
            return
        claim = self._current_claim()
        if claim:
            claim.text.append(data)
            if not self._in_default_hidden():
                claim.visible_text.append(data)
        factor_item = self._current_factor_item()
        if factor_item and not self._in_default_hidden():
            factor_item.visible_text.append(data)
        evidence = self._current_evidence()
        if evidence and self._in_evidence_summary():
            evidence.summary_text.append(data)
        for frame in reversed(self.stack):
            if frame.heading_text is not None:
                frame.heading_text.append(data)
                break
        capacity_row = self._current_capacity_row()
        if capacity_row:
            capacity_row.text.append(data)
        capacity_cell = self._current_capacity_cell()
        if capacity_cell:
            capacity_cell.text.append(data)
        for frame in reversed(self.stack):
            if frame.capacity_none:
                frame.capacity_none.text.append(data)
                break
        structured_row = self._current_structured_row()
        if structured_row:
            structured_row.text.append(data)
        structured_contract = self._current_structured_contract()
        if structured_contract:
            structured_contract.text.append(data)
            if not any(frame.explicit_hidden for frame in self.stack):
                structured_contract.rendered_text.append(data)

        count = _compact_char_count(data)
        if not count:
            return
        if evidence and self._in_evidence_body():
            evidence.body_chars += count
            evidence.body_text.append(data)
        section = self._current_section()
        if (
            section == "factor"
            and not self._in_evidence_body()
            and self._in_default_hidden()
            and (
                claim is not None
                or factor_item is not None
                or self._current_factor_role() in {"status", "no-data"}
            )
        ):
            self.factor_hidden_contract_elements += 1
        if self._inside_report_document():
            self.document_text.append(data)
            if section and not self._in_default_hidden() and not self._in_evidence_body():
                self.section_text[section].append(data)
            metrics = self._metrics_bucket(section)
            if self._in_evidence_body():
                metrics.evidence_chars += count
            else:
                metrics.visible_chars += count
        if self._inside_report_document() and not self._in_evidence_body():
            self.visible_document_text.append(data)
        if section == "factor" and not self._in_evidence_body():
            role = self._current_factor_role()
            if self._inside_tag("h2"):
                return
            if role == "status" and not self._in_default_hidden():
                self.factor_status_text.append(data)
            elif role == "no-data" and not self._in_default_hidden():
                self.factor_no_data_text.append(data)
            else:
                self.factor_other_text.append(data)

    def _validate_factor_contract(self) -> None:
        if (
            len(self.factor_hosts) != 1
            or self.factor_hosts[0][0] != "section"
            or "blk" not in self.factor_hosts[0][1]
        ):
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor 必须使用 section.blk 作为默认可见章节容器",
                section="factor",
            )

        mode = self.factor_modes[0] if len(self.factor_modes) == 1 else ""
        if mode not in FACTOR_MODES:
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor 必须声明 data-factor-mode=formal|rule_only|shadow|no_data",
                section="factor",
            )

        visible_claims = [
            claim
            for claim in self.claims.values()
            if claim.section == "factor"
            and not claim.in_evidence_body
            and not claim.default_hidden
        ]
        factor_evidence = [
            evidence for evidence in self.evidences if evidence.section == "factor"
        ]
        visible_items = [
            item for item in self.factor_items if not item.default_hidden
        ]

        if mode == "no_data":
            no_data_text = re.sub(
                r"\s+", "", "".join(self.factor_no_data_text)
            ).rstrip("。.;；")
            has_analysis_content = bool(
                visible_claims
                or self.factor_items
                or self.factor_statuses
                or factor_evidence
            )
            if (
                self.factor_no_data_statements != 1
                or no_data_text not in FACTOR_NO_DATA_STATEMENTS
                or _compact_char_count("".join(self.factor_other_text)) != 0
                or self.factor_other_elements != 0
                or self.factor_no_data_forbidden_elements != 0
                or self.factor_section_hidden
                or self.factor_hidden_contract_elements != 0
                or has_analysis_content
            ):
                raise ReportValidationError(
                    "invalid_factor_contract",
                    "no_data 因子节只能有唯一可见的 [事实] 本日无新增/无可判数据",
                    section="factor",
                )
            return

        if self.factor_section_hidden or self.factor_hidden_contract_elements:
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor 的裁决、证据和状态必须默认可见，不得放入折叠或 hidden 子树",
                section="factor",
            )
        if len(visible_claims) != 1 or visible_claims[0].kind != "judgment":
            raise ReportValidationError(
                "invalid_factor_contract",
                "有分析的 factor 必须且只能有 1 条可见 judgment Claim",
                section="factor",
            )
        if not _has_labeled_content(visible_claims[0].visible_text):
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor judgment Claim 必须有默认可见的标签和实质正文",
                section="factor",
            )
        if not 1 <= len(visible_items) <= 3:
            raise ReportValidationError(
                "invalid_factor_contract",
                "有分析的 factor 必须有 1 至 3 条可见证据",
                section="factor",
            )
        if not all(_has_labeled_content(item.visible_text) for item in visible_items):
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor 每条可见证据必须有 [事实]/[判断] 标签和实质正文",
                section="factor",
            )
        if self.factor_statuses != 1:
            raise ReportValidationError(
                "invalid_factor_contract",
                "有分析的 factor 必须且只能有 1 条 data-factor-role=status",
                section="factor",
            )
        detail_evidence = [
            evidence for evidence in factor_evidence if evidence.kind == "factor-detail"
        ]
        if len(detail_evidence) != 1:
            raise ReportValidationError(
                "invalid_factor_contract",
                "有分析的 factor 必须保留唯一 data-evidence-kind=factor-detail 折叠证据",
                section="factor",
            )
        detail_text = "".join(detail_evidence[0].body_text)
        if not all(key in detail_text for key in FACTOR_DETAIL_KEYS):
            raise ReportValidationError(
                "invalid_factor_contract",
                "factor-detail 必须包含四个重点因子的完整对账",
                section="factor",
            )
        status_text = "".join(self.factor_status_text)
        if not _valid_factor_status(mode, status_text):
            raise ReportValidationError(
                "invalid_factor_contract",
                f"factor 可见状态不符合 data-factor-mode={mode} 的规范模板",
                section="factor",
            )

    def _validate_capacity_health_contract(self, report_date: date_type) -> None:
        document_text = re.sub(r"\s+", "", "".join(self.document_text))
        if "旧池中军" in document_text:
            raise ReportValidationError(
                "invalid_capacity_health",
                "禁止使用“旧池中军”；历史趋势池身份必须与容量资格分开",
                section="s5",
            )

        contract_count = len(self.capacity_tables) + len(self.capacity_none_states)
        if contract_count != 1:
            raise ReportValidationError(
                "invalid_capacity_health",
                "s5 必须且只能包含一份容量表、无合格项或数据缺失状态",
                section="s5",
            )

        if self.capacity_none_states:
            state = self.capacity_none_states[0]
            if state.section != "s5":
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量状态必须归属 s5",
                    section="s5",
                )
            as_of = state.attrs.get("data-as-of", "")
            source_status = state.attrs.get("data-source-status", "")
            if (
                not _valid_date(as_of)
                or date_type.fromisoformat(as_of) > report_date
                or source_status not in CAPACITY_SOURCE_STATUSES
            ):
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量状态必须带不晚于报告日的 data-as-of 和合法 data-source-status",
                    section="s5",
                )
            mode = state.attrs.get("data-capacity-health", "")
            compact_text = re.sub(r"\s+", "", "".join(state.text)).rstrip("。.;；")
            if mode == "none":
                valid = source_status == "complete" and compact_text == CAPACITY_NONE_TEXT
            else:
                valid = (
                    mode == "missing-data"
                    and source_status in {"partial", "failed"}
                    and compact_text == CAPACITY_MISSING_TEXT
                    and "容量排名数据不完整" in "".join(self.section_text["ops"])
                )
            if not valid:
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "none 仅表示完整数据下无合格项；partial/failed 必须使用 missing-data 并在 ops 展示缺口",
                    section="s5",
                )
            return

        table = self.capacity_tables[0]
        attrs = table.attrs
        as_of = attrs.get("data-as-of", "")
        source_status = attrs.get("data-source-status", "")
        universe_text = attrs.get("data-universe-count", "")
        if (
            table.section != "s5"
            or not _valid_date(as_of)
            or date_type.fromisoformat(as_of) > report_date
            or source_status != "complete"
            or attrs.get("data-rank-source") != "daily.amount"
            or not universe_text.isdigit()
            or int(universe_text) < 50
        ):
            raise ReportValidationError(
                "invalid_capacity_health",
                "容量表必须声明完整 daily.amount 全市场排名、有效来源日和 universe 数量",
                section="s5",
            )
        if not table.rows:
            raise ReportValidationError(
                "invalid_capacity_health",
                "容量表不得为空；无合格项必须使用结构化 none 状态",
                section="s5",
            )

        universe_count = int(universe_text)
        seen_codes: set[str] = set()
        for row in table.rows:
            row_attrs = row.attrs
            required = (
                "data-code",
                "data-direction",
                "data-tier",
                "data-market-rank",
                "data-direction-rank",
                "data-top50-days",
            )
            if any(not row_attrs.get(key, "").strip() for key in required):
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量表每行必须带 code、direction、tier 和完整排名元数据",
                    section="s5",
                )
            code = row_attrs["data-code"].strip().upper()
            if not CAPACITY_CODE_RE.fullmatch(code) or code in seen_codes:
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量表 data-code 必须是唯一规范 ts_code",
                    section="s5",
                )
            seen_codes.add(code)
            tier = row_attrs["data-tier"].strip()
            market_text = row_attrs["data-market-rank"].strip()
            direction_text = row_attrs["data-direction-rank"].strip()
            top50_text = row_attrs["data-top50-days"].strip()
            if not all(value.isdigit() for value in (market_text, direction_text, top50_text)):
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量排名和 Top50 天数必须是整数",
                    section="s5",
                )
            market_rank = int(market_text)
            direction_rank = int(direction_text)
            top50_days = int(top50_text)
            qualified = (
                tier == "core"
                and 1 <= market_rank <= 30
                and 1 <= direction_rank <= 2
            ) or (
                tier == "candidate"
                and 31 <= market_rank <= 50
                and 1 <= direction_rank <= 2
            )
            if (
                not qualified
                or market_rank > universe_count
                or not 0 <= top50_days <= 5
            ):
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "core 仅限全市场 1-30，candidate 仅限 31-50；方向排名须 1-2，Top50 天数须 0-5",
                    section="s5",
                )
            if _compact_char_count("".join(row.text)) == 0:
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量表数据行不得为空",
                    section="s5",
                )
            visible = _capacity_visible_fields(row)
            expected_visible = {
                "code": code,
                "direction": row_attrs["data-direction"].strip(),
                "tier": tier,
                "market_rank": str(market_rank),
                "direction_rank": str(direction_rank),
                "top50_days": str(top50_days),
            }
            if (
                not visible
                or not visible.get("name")
                or any(visible.get(key) != value for key, value in expected_visible.items())
            ):
                raise ReportValidationError(
                    "invalid_capacity_health",
                    "容量表可见代码、方向、层级或排名必须与结构化元数据一致",
                    section="s5",
                )

    def _valid_contract_as_of(
        self, contract: _StructuredContract, report_date: date_type
    ) -> bool:
        as_of = contract.attrs.get("data-as-of", "")
        return bool(
            _valid_date(as_of) and date_type.fromisoformat(as_of) <= report_date
        )

    def _validate_data_contract_state(
        self,
        contract: _StructuredContract,
        *,
        report_date: date_type,
        section: str,
        none_text: str,
        missing_text: str,
        missing_marker: str,
        error_code: str,
    ) -> None:
        if contract.section != section or not self._valid_contract_as_of(
            contract, report_date
        ):
            raise ReportValidationError(
                error_code,
                "结构化数据块必须归属固定章节并带不晚于报告日的 data-as-of",
                section=section,
            )
        source_status = contract.attrs.get("data-source-status", "")
        compact_text = re.sub(r"\s+", "", "".join(contract.rendered_text)).rstrip("。.;；")
        if contract.value == "v1":
            valid = (
                contract.tag == "table"
                and source_status == "complete"
                and bool(compact_text)
                and len(contract.rows) >= 2
                and all(
                    _compact_char_count("".join(row.text)) > 0
                    for row in contract.rows
                )
            )
        elif contract.value == "none":
            valid = (
                contract.tag == "p"
                and source_status == "complete"
                and compact_text == none_text
            )
        elif contract.value == "missing-data":
            valid = (
                contract.tag == "p"
                and source_status in {"partial", "failed"}
                and compact_text == missing_text
                and missing_marker in "".join(self.section_text["ops"])
            )
        else:
            valid = False
        if not valid:
            raise ReportValidationError(
                error_code,
                "结构化数据块必须使用完整 v1、complete none 或带 ops 缺口的 missing-data",
                section=section,
            )

    def _validate_sector_contracts(self, report_date: date_type) -> None:
        concentration = self.structured_contracts["data-sector-concentration"]
        verdicts = [item for item in concentration if item.value == "verdict"]
        data_blocks = [item for item in concentration if item.value != "verdict"]
        if len(verdicts) != 1 or len(data_blocks) != 1:
            raise ReportValidationError(
                "invalid_sector_concentration",
                "s2 必须且只能包含一句集中度裁决和一份集中度数据块",
                section="s2",
            )
        verdict = verdicts[0]
        if (
            verdict.tag != "p"
            or verdict.section != "s2"
            or verdict.default_hidden
            or not _has_labeled_content(verdict.text)
        ):
            raise ReportValidationError(
                "invalid_sector_concentration",
                "板块集中度裁决必须是 s2 默认可见且带标签的 p",
                section="s2",
            )
        self._validate_data_contract_state(
            data_blocks[0],
            report_date=report_date,
            section="s2",
            none_text=SECTOR_CONCENTRATION_NONE_TEXT,
            missing_text=SECTOR_CONCENTRATION_MISSING_TEXT,
            missing_marker="板块集中度数据不完整",
            error_code="invalid_sector_concentration",
        )
        concentration_block = data_blocks[0]
        if concentration_block.value == "v1":
            seen_keys: set[str] = set()
            for row in concentration_block.rows[1:]:
                identity = (
                    row.attrs.get("data-direction", "").strip()
                    or row.attrs.get("data-trade-date", "").strip()
                )
                share_text = row.attrs.get("data-market-share", "").strip()
                try:
                    share = float(share_text)
                except ValueError:
                    share = -1.0
                if (
                    not identity
                    or identity in seen_keys
                    or not 0 <= share <= 100
                ):
                    raise ReportValidationError(
                        "invalid_sector_concentration",
                        "集中度数据行必须带唯一方向/交易日与 0-100 的市场占比",
                        section="s2",
                    )
                seen_keys.add(identity)

        paired = (
            (
                "data-rising-recognition",
                RISING_RECOGNITION_NONE_TEXT,
                RISING_RECOGNITION_MISSING_TEXT,
                "主升辨识度矩阵数据不完整",
            ),
            (
                "data-falling-recognition",
                FALLING_RECOGNITION_NONE_TEXT,
                FALLING_RECOGNITION_MISSING_TEXT,
                "主跌辨识度矩阵数据不完整",
            ),
        )
        for name, none_text, missing_text, missing_marker in paired:
            blocks = self.structured_contracts[name]
            if len(blocks) != 1:
                raise ReportValidationError(
                    "invalid_recognition_matrix",
                    "s2 必须成对保留唯一主升与主跌辨识度矩阵",
                    section="s2",
                )
            self._validate_data_contract_state(
                blocks[0],
                report_date=report_date,
                section="s2",
                none_text=none_text,
                missing_text=missing_text,
                missing_marker=missing_marker,
                error_code="invalid_recognition_matrix",
            )
            block = blocks[0]
            if block.value == "v1":
                seen_rows: set[tuple[str, str]] = set()
                for row in block.rows[1:]:
                    direction = row.attrs.get("data-direction", "").strip()
                    code = row.attrs.get("data-code", "").strip().upper()
                    key = (direction, code)
                    visible = re.sub(r"\s+", "", "".join(row.text))
                    if (
                        not direction
                        or not CAPACITY_CODE_RE.fullmatch(code)
                        or key in seen_rows
                        or direction not in visible
                    ):
                        raise ReportValidationError(
                            "invalid_recognition_matrix",
                            "主升/主跌矩阵数据行必须带唯一方向、规范代表代码和可见正文",
                            section="s2",
                        )
                    seen_rows.add(key)

    def _validate_new_high_structure_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-new-high-structure"]
        verdicts = [item for item in contracts if item.value == "verdict"]
        data_blocks = [item for item in contracts if item.value != "verdict"]
        if len(verdicts) != 1 or len(data_blocks) != 1:
            raise ReportValidationError(
                "invalid_new_high_structure",
                "s5 必须且只能包含一句滚动新高裁决和一份结构数据块",
                section="s5",
            )
        verdict = verdicts[0]
        if (
            verdict.tag != "p"
            or verdict.section != "s5"
            or verdict.default_hidden
            or not _has_labeled_content(verdict.text)
        ):
            raise ReportValidationError(
                "invalid_new_high_structure",
                "滚动新高裁决必须是 s5 默认可见且带标签的 p",
                section="s5",
            )

        block = data_blocks[0]
        self._validate_data_contract_state(
            block,
            report_date=report_date,
            section="s5",
            none_text=NEW_HIGH_STRUCTURE_NONE_TEXT,
            missing_text=NEW_HIGH_STRUCTURE_MISSING_TEXT,
            missing_marker="滚动新高结构数据不完整",
            error_code="invalid_new_high_structure",
        )
        if block.value != "v1":
            return

        as_of = block.attrs.get("data-as-of", "")
        prev_as_of = block.attrs.get("data-prev-as-of", "")
        count_keys = tuple(
            f"data-{period}-{window}-count"
            for period in ("current", "prev")
            for window in (60, 120, 250)
        )
        count_texts = [block.attrs.get(key, "") for key in count_keys]
        market_text = block.attrs.get("data-market-count", "")
        if (
            not _valid_date(prev_as_of)
            or prev_as_of >= as_of
            or block.attrs.get("data-basis") != "rolling-adjusted-high"
            or not market_text.isdigit()
            or int(market_text) < CAPACITY_MIN_UNIVERSE
            or any(not value.isdigit() for value in count_texts)
        ):
            raise ReportValidationError(
                "invalid_new_high_structure",
                "完整滚动新高结构必须带前一交易日、完整市场覆盖、前复权口径及三窗口双日计数",
                section="s5",
            )
        current = [int(value) for value in count_texts[:3]]
        previous = [int(value) for value in count_texts[3:]]
        if not (
            current[0] >= current[1] >= current[2]
            and previous[0] >= previous[1] >= previous[2]
        ):
            raise ReportValidationError(
                "invalid_new_high_structure",
                "60/120/250 日新高家数必须随窗口扩大单调不增",
                section="s5",
            )

    def _validate_event_window_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-event-window"]
        verdicts = [item for item in contracts if item.value == "verdict"]
        data_blocks = [item for item in contracts if item.value != "verdict"]
        if len(verdicts) != 1 or len(data_blocks) != 1:
            raise ReportValidationError(
                "invalid_event_window",
                "s6 必须且只能包含一句事件窗裁决和一份未来 7 日数据块",
                section="s6",
            )
        verdict = verdicts[0]
        if (
            verdict.tag != "p"
            or verdict.section != "s6"
            or verdict.default_hidden
            or not _has_labeled_content(verdict.text)
        ):
            raise ReportValidationError(
                "invalid_event_window",
                "事件窗裁决必须是 s6 默认可见且带标签的 p",
                section="s6",
            )

        block = data_blocks[0]
        self._validate_data_contract_state(
            block,
            report_date=report_date,
            section="s6",
            none_text=EVENT_WINDOW_NONE_TEXT,
            missing_text=EVENT_WINDOW_MISSING_TEXT,
            missing_marker="未来7个自然日事件窗数据不完整",
            error_code="invalid_event_window",
        )
        expected_start = report_date + timedelta(days=1)
        expected_end = report_date + timedelta(days=7)
        start_text = block.attrs.get("data-window-start", "")
        end_text = block.attrs.get("data-window-end", "")
        if (
            start_text != expected_start.isoformat()
            or end_text != expected_end.isoformat()
        ):
            raise ReportValidationError(
                "invalid_event_window",
                "事件窗必须严格覆盖报告日后第 1 至第 7 个自然日",
                section="s6",
            )
        if block.value != "v1":
            return
        dated_rows = [row for row in block.rows if row.attrs.get("data-event-date")]
        dates = [row.attrs["data-event-date"] for row in dated_rows]
        expected_dates = [
            (expected_start + timedelta(days=offset)).isoformat()
            for offset in range(7)
        ]
        if dates != expected_dates:
            raise ReportValidationError(
                "invalid_event_window",
                "事件窗表必须按顺序逐日覆盖 7 个自然日且不得重复或遗漏",
                section="s6",
            )
        if any(
            row.attrs.get("data-market-status") not in {"open", "closed"}
            or _compact_char_count("".join(row.text)) == 0
            for row in dated_rows
        ):
            raise ReportValidationError(
                "invalid_event_window",
                "事件窗每行必须声明 open/closed 并保留可见事件说明",
                section="s6",
            )

    def finalize(self) -> None:
        if self.stack:
            tags = " > ".join(frame.tag for frame in self.stack[-6:])
            self._error("unbalanced_tag", f"存在未闭合标签：{tags}")
        if self.capacity_heading_pending:
            self._error(
                "invalid_capacity_health",
                "中军健康度标题后缺少结构化容量表",
                section="s5",
            )

        if self.errors:
            code, message, section = self.errors[0]
            raise ReportValidationError(code, message, section=section)

        if len(self.schema_hosts) != 1:
            raise ReportValidationError(
                "invalid_schema",
                f"data-report-schema 必须且只能出现一次，并取值 {REPORT_SCHEMA}",
            )
        schema, schema_tag, schema_host, report_date_value = self.schema_hosts[0]
        if (
            schema != REPORT_SCHEMA
            or schema_tag != "article"
            or schema_host != "report-document"
            or not _valid_date(report_date_value)
        ):
            raise ReportValidationError(
                "invalid_schema",
                "article#report-document 必须带 compact-v1 schema 和有效 data-report-date",
            )
        report_date = date_type.fromisoformat(report_date_value)

        if tuple(self.chunks) != CHUNK_ORDER:
            raise ReportValidationError(
                "invalid_chunks",
                f"chunk 必须按顺序且各出现一次：{', '.join(CHUNK_ORDER)}；实际：{', '.join(self.chunks)}",
            )

        for anchor in REQUIRED_ANCHORS:
            count = self.ids.get(anchor, 0)
            if count != 1:
                raise ReportValidationError(
                    "invalid_anchor",
                    f"锚点 {anchor} 必须出现一次，实际 {count}",
                    section=anchor,
                )
            expected_chunk = next(
                chunk for chunk, anchors in ANCHOR_MAP.items() if anchor in anchors
            )
            actual_chunks = self.anchor_chunks[anchor]
            if actual_chunks != [expected_chunk]:
                raise ReportValidationError(
                    "anchor_chunk_mismatch",
                    f"锚点 {anchor} 必须归属 chunk {expected_chunk}，实际 {actual_chunks}",
                    section=anchor,
                )

        if tuple(self.anchors_seen) != REQUIRED_ANCHORS:
            raise ReportValidationError(
                "invalid_anchor_order",
                "章节锚点必须按固定顺序出现：" + ", ".join(REQUIRED_ANCHORS),
            )

        self._validate_factor_contract()
        self._validate_capacity_health_contract(report_date)
        self._validate_sector_contracts(report_date)
        self._validate_new_high_structure_contract(report_date)
        self._validate_event_window_contract(report_date)

        duplicate_id = next((key for key, count in self.ids.items() if count != 1), None)
        if duplicate_id:
            raise ReportValidationError(
                "duplicate_id", f"HTML id 重复：{duplicate_id}"
            )

        for evidence in self.evidences:
            if evidence.section not in REQUIRED_ANCHORS:
                raise ReportValidationError(
                    "evidence_without_home",
                    "evidence 必须归属一个固定章节",
                )
            if not _valid_date(evidence.as_of):
                raise ReportValidationError(
                    "invalid_evidence_metadata",
                    "evidence 的 data-as-of 必须是有效 YYYY-MM-DD",
                    section=evidence.section,
                )
            if date_type.fromisoformat(evidence.as_of) > report_date:
                raise ReportValidationError(
                    "future_evidence_date",
                    "evidence 的 data-as-of 不得晚于报告交易日",
                    section=evidence.section,
                )
            if not evidence.items.isdigit() or int(evidence.items) < 1:
                raise ReportValidationError(
                    "invalid_evidence_metadata",
                    "evidence 的 data-items 必须是大于 0 的整数",
                    section=evidence.section,
                )
            if evidence.summary_count != 1 or not evidence.first_child_is_summary:
                raise ReportValidationError(
                    "invalid_evidence_summary",
                    "evidence 必须以唯一 summary 作为第一个元素",
                    section=evidence.section,
                )
            summary_text = "".join(evidence.summary_text)
            if _compact_char_count(summary_text) == 0:
                raise ReportValidationError(
                    "invalid_evidence_summary",
                    "evidence summary 不得为空",
                    section=evidence.section,
                )
            if not re.search(rf"(?<!\d){re.escape(evidence.items)}(?!\d)", summary_text):
                raise ReportValidationError(
                    "invalid_evidence_summary",
                    "evidence summary 必须显示 data-items 数量",
                    section=evidence.section,
                )
            if evidence.body_chars == 0 and evidence.body_artifacts == 0:
                raise ReportValidationError(
                    "empty_evidence_body",
                    "evidence summary 之后必须保留非空证据正文",
                    section=evidence.section,
                )

        for claim_id, claim in self.claims.items():
            if claim.section not in REQUIRED_ANCHORS:
                raise ReportValidationError(
                    "claim_without_home",
                    f"claim owner 必须位于唯一正文章节：{claim_id}",
                )
            if claim.in_evidence_body:
                raise ReportValidationError(
                    "claim_in_evidence",
                    f"claim owner 不得隐藏在 evidence：{claim_id}",
                    section=claim.section,
                )
            if claim.kind not in {"fact", "judgment"}:
                raise ReportValidationError(
                    "invalid_claim_metadata",
                    f"{claim_id} 的 data-claim-kind 必须为 fact 或 judgment",
                    section=claim.section,
                )
            if not claim.source.strip() or not _valid_date(claim.as_of):
                raise ReportValidationError(
                    "invalid_claim_metadata",
                    f"{claim_id} 必须带非空 data-source 和有效 data-as-of",
                    section=claim.section,
                )
            if date_type.fromisoformat(claim.as_of) > report_date:
                raise ReportValidationError(
                    "future_claim_date",
                    f"{claim_id} 的 data-as-of 不得晚于报告交易日",
                    section=claim.section,
                )
            label = "[事实]" if claim.kind == "fact" else "[判断]"
            if label not in "".join(claim.text):
                raise ReportValidationError(
                    "claim_label_mismatch",
                    f"{claim_id} 可见文本必须包含 {label}",
                    section=claim.section,
                )

        claim_ref_counts: dict[str, int] = {}
        for claim_ref, href, section in self.claim_refs:
            if claim_ref not in self.claims:
                raise ReportValidationError(
                    "dangling_claim_ref",
                    f"claim 引用无 owner：{claim_ref}",
                    section=section,
                )
            if href != f"#{claim_ref}":
                raise ReportValidationError(
                    "invalid_claim_ref",
                    f"claim 引用 href 必须为 #{claim_ref}",
                    section=section,
                )
            claim_ref_counts[claim_ref] = claim_ref_counts.get(claim_ref, 0) + 1
            if claim_ref_counts[claim_ref] > 1:
                raise ReportValidationError(
                    "duplicate_claim_ref",
                    f"同一 claim 最多允许一个短引用：{claim_ref}",
                    section=section,
                )

        visible_text = "".join(self.visible_document_text)
        required_literals = (
            "只读",
            "北向禁用",
            "000001.SH + 399106.SZ",
            "[事实]",
            "[判断]",
        )
        missing = [item for item in required_literals if item not in visible_text]
        if missing:
            raise ReportValidationError(
                "missing_guardrail",
                f"缺少可见边界或口径声明：{', '.join(missing)}",
            )

    def metrics(self) -> ReportMetrics:
        report_sections = dict(self.sections)
        report_sections["document"] = self.unscoped
        visible_chars = sum(item.visible_chars for item in report_sections.values())
        visible_tables = sum(item.visible_tables for item in report_sections.values())
        visible_rows = sum(item.visible_rows for item in report_sections.values())
        evidence_chars = sum(item.evidence_chars for item in report_sections.values())
        evidence_tables = sum(item.evidence_tables for item in report_sections.values())
        evidence_rows = sum(item.evidence_rows for item in report_sections.values())
        return ReportMetrics(
            tldr_chars=self.sections["tldr"].visible_chars,
            visible_chars=visible_chars,
            visible_tables=visible_tables,
            visible_rows=visible_rows,
            evidence_chars=evidence_chars,
            evidence_tables=evidence_tables,
            evidence_rows=evidence_rows,
            sections=report_sections,
            visible_target_exceeded=visible_chars > VISIBLE_CHAR_TARGET,
        )


def _parse_report(html: str) -> _ReportParser:
    parser = _ReportParser()
    try:
        parser.feed(html)
        parser.close()
    except ReportValidationError:
        raise
    except Exception as exc:  # pragma: no cover - HTMLParser 的保护边界
        raise ReportValidationError("invalid_html", str(exc)) from exc
    parser.finalize()
    return parser


def collect_metrics(html: str) -> ReportMetrics:
    """解析完整 HTML，并按 compact-v1 的唯一口径返回正文/证据预算。"""

    return _parse_report(html).metrics()


def load_capacity_manifest(
    manifest_path: str | os.PathLike[str], report_date: str
) -> dict:
    """读取并校验确定性容量排名 sidecar。"""

    path = Path(manifest_path)
    if not path.is_file():
        raise ReportValidationError(
            "missing_capacity_manifest",
            f"缺少容量排名 sidecar：{path.name}",
            section="s5",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportValidationError(
            "invalid_capacity_manifest",
            f"容量排名 sidecar 无法读取：{exc}",
            section="s5",
        ) from exc
    if not isinstance(payload, dict):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "容量排名 sidecar 顶层必须是对象",
            section="s5",
        )
    _validate_capacity_manifest_payload(payload, report_date)
    return payload


def load_new_high_structure_manifest(
    manifest_path: str | os.PathLike[str], report_date: str
) -> dict:
    """读取并校验前复权滚动新高结构 sidecar。"""

    path = Path(manifest_path)
    if not path.is_file():
        raise ReportValidationError(
            "missing_new_high_manifest",
            f"缺少滚动新高 sidecar：{path.name}",
            section="s5",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportValidationError(
            "invalid_new_high_manifest",
            f"滚动新高 sidecar 无法读取：{exc}",
            section="s5",
        ) from exc
    if not isinstance(payload, dict):
        raise ReportValidationError(
            "invalid_new_high_manifest",
            "滚动新高 sidecar 顶层必须是对象",
            section="s5",
        )
    _validate_new_high_manifest_payload(payload, report_date)
    return payload


def _validate_capacity_manifest_payload(payload: dict, report_date: str) -> None:
    if (
        payload.get("schema") != CAPACITY_MANIFEST_SCHEMA
        or payload.get("report_date") != report_date
        or not _valid_date(str(payload.get("as_of") or ""))
        or str(payload.get("as_of")) > report_date
        or payload.get("status") not in CAPACITY_SOURCE_STATUSES
        or payload.get("rank_metric") != "daily.amount"
        or payload.get("generator") != "build_capacity_manifest.py"
        or not isinstance(payload.get("rows"), list)
    ):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "容量排名 sidecar 的 schema、日期、状态或排名口径无效",
            section="s5",
        )
    complete = payload.get("complete") is True
    if complete != (payload.get("status") == "complete"):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "容量排名 sidecar 的 complete 与 status 不一致",
            section="s5",
        )
    rows = payload["rows"]
    if not complete:
        if rows or not payload.get("errors"):
            raise ReportValidationError(
                "invalid_capacity_manifest",
                "不完整 sidecar 不得携带资格行，且必须记录 errors",
                section="s5",
            )
        return

    universe = payload.get("market_universe_count")
    reference_count = payload.get("market_reference_count")
    market_coverage = payload.get("market_coverage")
    industry_coverage = payload.get("industry_coverage")
    directions = payload.get("directions")
    trade_dates = payload.get("rank_trade_dates")
    as_of_day = date_type.fromisoformat(payload["as_of"])
    report_day = date_type.fromisoformat(report_date)
    if (
        not isinstance(universe, int)
        or universe < CAPACITY_MIN_UNIVERSE
        or not isinstance(reference_count, int)
        or reference_count < CAPACITY_MIN_UNIVERSE
        or not isinstance(market_coverage, (int, float))
        or not 0.90 <= float(market_coverage) <= 1.05
        or abs(float(market_coverage) - universe / reference_count) > 0.00001
        or not isinstance(industry_coverage, (int, float))
        or not 0.90 <= float(industry_coverage) <= 1.0
        or not str(payload.get("market_source") or "").strip()
        or not str(payload.get("market_reference_source") or "").strip()
        or not str(payload.get("direction_source") or "").strip()
        or not str(payload.get("calendar_source") or "").strip()
        or not isinstance(directions, list)
        or not 1 <= len(directions) <= 3
        or not isinstance(trade_dates, list)
        or len(trade_dates) != 5
        or any(not _valid_date(str(item)) for item in trade_dates)
        or sorted(set(trade_dates)) != trade_dates
        or trade_dates[-1] != payload["as_of"]
        or (report_day - as_of_day).days > CAPACITY_MAX_REPORT_LAG_DAYS
    ):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "完整 sidecar 必须保留全市场、方向成员和最近 5 个开放日证据",
            section="s5",
        )
    trade_days = [date_type.fromisoformat(str(item)) for item in trade_dates]
    if any(item.weekday() >= 5 for item in trade_days) or any(
        (later - earlier).days > CAPACITY_MAX_TRADE_GAP_DAYS
        for earlier, later in zip(trade_days, trade_days[1:])
    ):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "容量 sidecar 的最近 5 个开放日不得含周末或异常断档",
            section="s5",
        )
    direction_ids: list[str] = []
    for item in directions:
        if (
            not isinstance(item, dict)
            or not str(item.get("id") or "").strip()
            or not isinstance(item.get("member_count"), int)
            or item["member_count"] < 1
        ):
            raise ReportValidationError(
                "invalid_capacity_manifest",
                "sidecar 方向必须带唯一 id 与正成员数",
                section="s5",
            )
        direction_ids.append(str(item["id"]))
    if len(set(direction_ids)) != len(direction_ids):
        raise ReportValidationError(
            "invalid_capacity_manifest",
            "sidecar 方向 id 不得重复",
            section="s5",
        )

    seen_codes: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReportValidationError(
                "invalid_capacity_manifest",
                "sidecar 资格行必须是对象",
                section="s5",
            )
        code = str(row.get("ts_code") or "").upper()
        direction = str(row.get("direction") or "")
        tier = row.get("tier")
        market_rank = row.get("market_rank")
        direction_rank = row.get("direction_rank")
        top50_days = row.get("top50_days")
        qualified = (
            tier == "core"
            and isinstance(market_rank, int)
            and 1 <= market_rank <= 30
        ) or (
            tier == "candidate"
            and isinstance(market_rank, int)
            and 31 <= market_rank <= 50
        )
        if (
            not CAPACITY_CODE_RE.fullmatch(code)
            or code in seen_codes
            or direction not in direction_ids
            or not qualified
            or not isinstance(direction_rank, int)
            or not 1 <= direction_rank <= 2
            or not isinstance(top50_days, int)
            or not 0 <= top50_days <= 5
            or market_rank > universe
        ):
            raise ReportValidationError(
                "invalid_capacity_manifest",
                "sidecar 资格行不符合容量阈值或元数据契约",
                section="s5",
            )
        seen_codes.add(code)


def _validate_capacity_manifest_match(
    parser: _ReportParser, manifest: dict
) -> None:
    rows = manifest["rows"]
    if manifest["complete"] and rows:
        if len(parser.capacity_tables) != 1:
            raise ReportValidationError(
                "capacity_manifest_mismatch",
                "sidecar 有合格项时 s5 必须输出容量表",
                section="s5",
            )
        table = parser.capacity_tables[0]
        if (
            table.attrs.get("data-as-of") != manifest["as_of"]
            or table.attrs.get("data-source-status") != "complete"
            or table.attrs.get("data-rank-source") != manifest["rank_metric"]
            or table.attrs.get("data-universe-count")
            != str(manifest["market_universe_count"])
        ):
            raise ReportValidationError(
                "capacity_manifest_mismatch",
                "容量表来源日、完整性、口径或 universe 与 sidecar 不一致",
                section="s5",
            )
        html_rows = {
            row.attrs["data-code"].upper(): row for row in table.rows
        }
        manifest_rows = {row["ts_code"].upper(): row for row in rows}
        if set(html_rows) != set(manifest_rows):
            raise ReportValidationError(
                "capacity_manifest_mismatch",
                "容量表必须完整展示 sidecar 选定方向内全部合格项",
                section="s5",
            )
        for code, expected in manifest_rows.items():
            html_row = html_rows[code]
            actual = html_row.attrs
            expected_attrs = {
                "data-direction": str(expected["direction"]),
                "data-tier": str(expected["tier"]),
                "data-market-rank": str(expected["market_rank"]),
                "data-direction-rank": str(expected["direction_rank"]),
                "data-top50-days": str(expected["top50_days"]),
            }
            if any(actual.get(key) != value for key, value in expected_attrs.items()):
                raise ReportValidationError(
                    "capacity_manifest_mismatch",
                    f"{code} 的容量排名元数据与 sidecar 不一致",
                    section="s5",
                )
            visible = _capacity_visible_fields(html_row)
            expected_name = re.sub(r"\s+", "", str(expected.get("name") or ""))
            if visible.get("name") != expected_name:
                raise ReportValidationError(
                    "capacity_manifest_mismatch",
                    f"{code} 的可见名称与 sidecar 不一致",
                    section="s5",
                )
            if visible.get("amount_text"):
                try:
                    amount_token = f"{float(expected['amount_yi']):.2f}亿"
                except (KeyError, TypeError, ValueError):
                    amount_token = ""
                if not amount_token or amount_token not in visible["amount_text"]:
                    raise ReportValidationError(
                        "capacity_manifest_mismatch",
                        f"{code} 的可见成交额与 sidecar 不一致",
                        section="s5",
                    )
        return

    if len(parser.capacity_none_states) != 1:
        raise ReportValidationError(
            "capacity_manifest_mismatch",
            "sidecar 无合格项或数据不完整时必须输出对应结构化状态",
            section="s5",
        )
    state = parser.capacity_none_states[0]
    expected_mode = "none" if manifest["complete"] else "missing-data"
    if (
        state.attrs.get("data-capacity-health") != expected_mode
        or state.attrs.get("data-as-of") != manifest["as_of"]
        or state.attrs.get("data-source-status") != manifest["status"]
    ):
        raise ReportValidationError(
            "capacity_manifest_mismatch",
            "s5 结构化状态与 sidecar 完整性不一致",
            section="s5",
        )


def _new_high_manifest_error(message: str) -> ReportValidationError:
    return ReportValidationError(
        "invalid_new_high_manifest", message, section="s5"
    )


def _validate_new_high_manifest_payload(payload: dict, report_date: str) -> None:
    status = payload.get("status")
    as_of = str(payload.get("as_of") or "")
    if (
        payload.get("schema") != NEW_HIGH_MANIFEST_SCHEMA
        or payload.get("generator") != "build_new_high_structure_manifest.py"
        or payload.get("report_date") != report_date
        or not _valid_date(as_of)
        or as_of > report_date
        or status not in CAPACITY_SOURCE_STATUSES
        or payload.get("basis") != "rolling-adjusted-high"
        or payload.get("windows") != [60, 120, 250]
    ):
        raise _new_high_manifest_error(
            "滚动新高 sidecar 的 schema、生成器、日期、状态或窗口口径无效"
        )

    complete = payload.get("complete") is True
    if complete != (status == "complete"):
        raise _new_high_manifest_error(
            "滚动新高 sidecar 的 complete 与 status 不一致"
        )
    if not complete:
        empty_fields = (
            "counts",
            "sectors",
            "representatives",
            "current_codes",
            "previous_codes",
            "trade_dates",
            "daily_market_counts",
        )
        if (
            any(payload.get(key) for key in empty_fields)
            or payload.get("market_count") not in {0, None}
            or not isinstance(payload.get("errors"), list)
            or not payload["errors"]
        ):
            raise _new_high_manifest_error(
                "不完整滚动新高 sidecar 不得携带结果，且必须记录 errors"
            )
        return

    prev_as_of = str(payload.get("prev_as_of") or "")
    trade_dates = payload.get("trade_dates")
    market_count = payload.get("market_count")
    reference_count = payload.get("market_reference_count")
    market_coverage = payload.get("market_coverage")
    industry_coverage = payload.get("industry_coverage")
    daily_market_counts = payload.get("daily_market_counts")
    daily_market_coverage_min = payload.get("daily_market_coverage_min")
    report_day = date_type.fromisoformat(report_date)
    as_of_day = date_type.fromisoformat(as_of)
    if (
        not _valid_date(prev_as_of)
        or prev_as_of >= as_of
        or (report_day - as_of_day).days > CAPACITY_MAX_REPORT_LAG_DAYS
        or not isinstance(trade_dates, list)
        or len(trade_dates) != 251
        or any(not _valid_date(str(day)) for day in trade_dates)
        or trade_dates != sorted(set(trade_dates))
        or trade_dates[-1] != as_of
        or trade_dates[-2] != prev_as_of
        or not isinstance(market_count, int)
        or market_count < CAPACITY_MIN_UNIVERSE
        or not isinstance(reference_count, int)
        or reference_count < CAPACITY_MIN_UNIVERSE
        or not isinstance(market_coverage, (int, float))
        or not 0.90 <= float(market_coverage) <= 1.05
        or abs(float(market_coverage) - market_count / reference_count) > 0.000001
        or not isinstance(industry_coverage, (int, float))
        or not 0.90 <= float(industry_coverage) <= 1.0
        or not isinstance(daily_market_counts, dict)
        or set(daily_market_counts) != set(trade_dates)
        or any(
            type(daily_market_counts.get(day)) is not int
            or daily_market_counts[day] < CAPACITY_MIN_UNIVERSE
            or daily_market_counts[day] / reference_count < 0.90
            for day in trade_dates
        )
        or not isinstance(daily_market_coverage_min, (int, float))
        or abs(
            float(daily_market_coverage_min)
            - min(daily_market_counts[day] / reference_count for day in trade_dates)
        )
        > 0.000001
    ):
        raise _new_high_manifest_error(
            "完整滚动新高 sidecar 必须保留 251 个开放日、双日锚点及全市场覆盖证据"
        )
    parsed_trade_dates = [date_type.fromisoformat(str(day)) for day in trade_dates]
    if any(day.weekday() >= 5 for day in parsed_trade_dates) or any(
        (later - earlier).days > NEW_HIGH_MAX_TRADE_GAP_DAYS
        for earlier, later in zip(parsed_trade_dates, parsed_trade_dates[1:])
    ):
        raise _new_high_manifest_error(
            "滚动新高 sidecar 的开放日脊柱不得含周末或异常断档"
        )

    counts = payload.get("counts")
    current_codes = payload.get("current_codes")
    previous_codes = payload.get("previous_codes")
    if not all(isinstance(item, dict) for item in (counts, current_codes, previous_codes)):
        raise _new_high_manifest_error("滚动新高计数与代码集必须是对象")
    expected_keys = {"60", "120", "250"}
    if any(set(item) != expected_keys for item in (counts, current_codes, previous_codes)):
        raise _new_high_manifest_error("滚动新高计数与代码集必须完整覆盖 60/120/250 日")

    current_sets: dict[str, set[str]] = {}
    previous_sets: dict[str, set[str]] = {}
    current_values: list[int] = []
    previous_values: list[int] = []
    for window in ("60", "120", "250"):
        count = counts[window]
        current_list = current_codes[window]
        previous_list = previous_codes[window]
        if (
            not isinstance(count, dict)
            or any(type(count.get(key)) is not int for key in ("current", "previous", "delta"))
            or count["current"] < 0
            or count["previous"] < 0
            or count["delta"] != count["current"] - count["previous"]
            or not isinstance(current_list, list)
            or not isinstance(previous_list, list)
        ):
            raise _new_high_manifest_error(f"{window} 日新高计数或增量无效")
        normalized_current = [str(code).upper() for code in current_list]
        normalized_previous = [str(code).upper() for code in previous_list]
        if (
            any(not CAPACITY_CODE_RE.fullmatch(code) for code in normalized_current + normalized_previous)
            or len(set(normalized_current)) != len(normalized_current)
            or len(set(normalized_previous)) != len(normalized_previous)
            or len(normalized_current) != count["current"]
            or len(normalized_previous) != count["previous"]
        ):
            raise _new_high_manifest_error(
                f"{window} 日新高代码集必须唯一、规范且与计数一致"
            )
        current_sets[window] = set(normalized_current)
        previous_sets[window] = set(normalized_previous)
        current_values.append(count["current"])
        previous_values.append(count["previous"])
    if not (
        current_values[0] >= current_values[1] >= current_values[2]
        and previous_values[0] >= previous_values[1] >= previous_values[2]
        and current_sets["250"] <= current_sets["120"] <= current_sets["60"]
        and previous_sets["250"] <= previous_sets["120"] <= previous_sets["60"]
    ):
        raise _new_high_manifest_error(
            "60/120/250 日新高计数和代码集必须随窗口扩大单调不增"
        )

    current_60 = current_sets["60"]
    previous_60 = previous_sets["60"]
    overlap = len(current_60 & previous_60)
    expected_retention = round(overlap / len(previous_60) * 100, 2) if previous_60 else None
    expected_turnover = round((len(current_60) - overlap) / len(current_60) * 100, 2) if current_60 else None
    if (
        payload.get("sixty_day_overlap") != overlap
        or payload.get("sixty_day_retention_pct") != expected_retention
        or payload.get("sixty_day_turnover_pct") != expected_turnover
    ):
        raise _new_high_manifest_error("60 日名单延续/换手必须由双日代码集确定性计算")

    sectors = payload.get("sectors")
    sector_cr3 = payload.get("sector_cr3_pct")
    if not isinstance(sectors, list) or not isinstance(sector_cr3, (int, float)):
        raise _new_high_manifest_error("60 日行业结构或 CR3 无效")
    seen_industries: set[str] = set()
    sector_total = 0
    for item in sectors:
        if not isinstance(item, dict):
            raise _new_high_manifest_error("行业结构行必须是对象")
        industry = str(item.get("industry") or "").strip()
        count = item.get("count")
        share = item.get("share_pct")
        if (
            not industry
            or industry in seen_industries
            or type(count) is not int
            or count < 1
            or not isinstance(share, (int, float))
            or not 0 <= float(share) <= 100
            or abs(float(share) - round(count / max(1, len(current_60)) * 100, 2)) > 0.001
        ):
            raise _new_high_manifest_error("行业结构名称、计数或占比无效")
        seen_industries.add(industry)
        sector_total += count
    expected_cr3 = round(sum(item["count"] for item in sectors[:3]) / max(1, len(current_60)) * 100, 2)
    if (
        sector_total != len(current_60)
        or abs(float(sector_cr3) - expected_cr3) > 0.001
        or (not current_60 and (sectors or float(sector_cr3) != 0.0))
    ):
        raise _new_high_manifest_error("行业家数合计或 CR3 与 60 日新高集合不一致")

    representatives = payload.get("representatives")
    if not isinstance(representatives, list) or len(representatives) != min(5, len(current_60)):
        raise _new_high_manifest_error("代表票必须覆盖最多 5 个 60 日新高高成交标的")
    seen_representatives: set[str] = set()
    previous_amount: float | None = None
    for item in representatives:
        if not isinstance(item, dict):
            raise _new_high_manifest_error("代表票行必须是对象")
        code = str(item.get("ts_code") or "").upper()
        amount = item.get("amount_yi")
        windows = item.get("windows")
        expected_windows = [
            int(window) for window in ("60", "120", "250") if code in current_sets[window]
        ]
        if (
            code not in current_60
            or code in seen_representatives
            or not str(item.get("name") or "").strip()
            or not str(item.get("industry") or "").strip()
            or not isinstance(amount, (int, float))
            or float(amount) < 0
            or not isinstance(item.get("pct_chg"), (int, float))
            or windows != expected_windows
            or (previous_amount is not None and float(amount) > previous_amount)
        ):
            raise _new_high_manifest_error("代表票代码、可见字段、窗口或成交额排序无效")
        seen_representatives.add(code)
        previous_amount = float(amount)

    sources = payload.get("sources")
    if (
        not isinstance(sources, dict)
        or any(not str(sources.get(key) or "").strip() for key in ("quote", "adj_factor", "calendar", "industry"))
        or payload.get("errors") != []
    ):
        raise _new_high_manifest_error("完整滚动新高 sidecar 必须保留非空来源且不得带 errors")


def _validate_new_high_manifest_match(
    parser: _ReportParser, manifest: dict
) -> None:
    contracts = parser.structured_contracts["data-new-high-structure"]
    verdicts = [item for item in contracts if item.value == "verdict"]
    blocks = [item for item in contracts if item.value != "verdict"]
    if len(verdicts) != 1 or len(blocks) != 1:
        raise ReportValidationError(
            "new_high_manifest_mismatch",
            "滚动新高 sidecar 必须对应唯一结构数据块",
            section="s5",
        )
    block = blocks[0]
    current_counts = [
        manifest.get("counts", {}).get(str(window), {}).get("current", 0)
        for window in (60, 120, 250)
    ]
    expected_mode = (
        "v1"
        if manifest["complete"] and any(current_counts)
        else "none"
        if manifest["complete"]
        else "missing-data"
    )
    expected_status = "complete" if manifest["complete"] else manifest["status"]
    if (
        block.value != expected_mode
        or block.attrs.get("data-as-of") != manifest["as_of"]
        or block.attrs.get("data-source-status") != expected_status
    ):
        raise ReportValidationError(
            "new_high_manifest_mismatch",
            "滚动新高结构状态、来源日或完整性与 sidecar 不一致",
            section="s5",
        )
    verdict_text = re.sub(r"\s+", "", "".join(verdicts[0].rendered_text))
    if expected_mode != "v1":
        required_terms = (
            ("0/0/0", "无符合", "无新高")
            if expected_mode == "none"
            else ("无法判定", "数据不完整")
        )
        if not any(term in verdict_text for term in required_terms):
            raise ReportValidationError(
                "new_high_manifest_mismatch",
                "滚动新高裁决必须明确对应 sidecar 的无结果或数据缺失状态",
                section="s5",
            )
        return

    expected_attrs = {
        "data-prev-as-of": str(manifest["prev_as_of"]),
        "data-market-count": str(manifest["market_count"]),
        "data-basis": str(manifest["basis"]),
    }
    for window in (60, 120, 250):
        item = manifest["counts"][str(window)]
        expected_attrs[f"data-current-{window}-count"] = str(item["current"])
        expected_attrs[f"data-prev-{window}-count"] = str(item["previous"])
    if any(block.attrs.get(key) != value for key, value in expected_attrs.items()):
        raise ReportValidationError(
            "new_high_manifest_mismatch",
            "滚动新高表的日期、市场覆盖或双日计数与 sidecar 不一致",
            section="s5",
        )

    compact_text = re.sub(r"\s+", "", "".join(block.rendered_text))
    current_token = "/".join(str(value) for value in current_counts)
    previous_token = "/".join(
        str(manifest["counts"][str(window)]["previous"])
        for window in (60, 120, 250)
    )
    cr3_token = f"{float(manifest['sector_cr3_pct']):.1f}%"
    if (
        current_token not in compact_text
        or previous_token not in compact_text
        or cr3_token not in compact_text
        or current_token not in verdict_text
    ):
        raise ReportValidationError(
            "new_high_manifest_mismatch",
            "滚动新高裁决或表格的可见双日计数/CR3 与 sidecar 不一致",
            section="s5",
        )
    overlap_token = f"重合{manifest['sixty_day_overlap']}"
    retention_value = manifest["sixty_day_retention_pct"]
    turnover_value = manifest["sixty_day_turnover_pct"]
    retention_token = (
        "延续率—"
        if retention_value is None
        else f"延续率{float(retention_value):.2f}%"
    )
    turnover_token = (
        "换手率—"
        if turnover_value is None
        else f"换手率{float(turnover_value):.2f}%"
    )
    if any(
        token not in compact_text
        for token in (overlap_token, retention_token, turnover_token)
    ):
        raise ReportValidationError(
            "new_high_manifest_mismatch",
            "滚动新高表未展示 sidecar 的 60 日名单延续/换手",
            section="s5",
        )
    for item in manifest["sectors"][:3]:
        if str(item["industry"]) not in compact_text:
            raise ReportValidationError(
                "new_high_manifest_mismatch",
                "滚动新高表未展示 sidecar 的 60 日行业 Top3",
                section="s5",
            )
    for item in manifest["representatives"]:
        if re.sub(r"\s+", "", str(item["name"])) not in compact_text:
            raise ReportValidationError(
                "new_high_manifest_mismatch",
                "滚动新高表未展示 sidecar 的代表票",
                section="s5",
            )


def _largest_section(metrics: ReportMetrics, field_name: str) -> str:
    return max(
        metrics.sections,
        key=lambda section: getattr(metrics.sections[section], field_name),
    )


def validate_report(
    html: str,
    *,
    capacity_manifest: dict | None = None,
    new_high_manifest: dict | None = None,
) -> ReportMetrics:
    """校验结构、Claim、折叠证据、边界声明及双层预算。"""

    parser = _parse_report(html)
    metrics = parser.metrics()
    if capacity_manifest is not None:
        report_date = next(item[3] for item in parser.schema_hosts)
        _validate_capacity_manifest_payload(capacity_manifest, report_date)
        _validate_capacity_manifest_match(parser, capacity_manifest)
    if new_high_manifest is not None:
        report_date = next(item[3] for item in parser.schema_hosts)
        _validate_new_high_manifest_payload(new_high_manifest, report_date)
        _validate_new_high_manifest_match(parser, new_high_manifest)
    checks = (
        (
            metrics.tldr_chars > TLDR_CHAR_LIMIT,
            "tldr_chars_exceeded",
            "tldr",
            f"速览 {metrics.tldr_chars} 字，硬上限 {TLDR_CHAR_LIMIT}",
        ),
        (
            metrics.visible_chars > VISIBLE_CHAR_LIMIT,
            "visible_chars_exceeded",
            _largest_section(metrics, "visible_chars"),
            f"正文 {metrics.visible_chars} 字，硬上限 {VISIBLE_CHAR_LIMIT}",
        ),
        (
            metrics.visible_tables > VISIBLE_TABLE_LIMIT,
            "visible_tables_exceeded",
            _largest_section(metrics, "visible_tables"),
            f"正文 {metrics.visible_tables} 张表，硬上限 {VISIBLE_TABLE_LIMIT}",
        ),
        (
            metrics.visible_rows > VISIBLE_ROW_LIMIT,
            "visible_rows_exceeded",
            _largest_section(metrics, "visible_rows"),
            f"正文 {metrics.visible_rows} 行，硬上限 {VISIBLE_ROW_LIMIT}",
        ),
        (
            metrics.evidence_chars > EVIDENCE_CHAR_LIMIT,
            "evidence_chars_exceeded",
            _largest_section(metrics, "evidence_chars"),
            f"证据层 {metrics.evidence_chars} 字，硬上限 {EVIDENCE_CHAR_LIMIT}",
        ),
        (
            metrics.evidence_tables > EVIDENCE_TABLE_LIMIT,
            "evidence_tables_exceeded",
            _largest_section(metrics, "evidence_tables"),
            f"证据层 {metrics.evidence_tables} 张表，硬上限 {EVIDENCE_TABLE_LIMIT}",
        ),
        (
            metrics.evidence_rows > EVIDENCE_ROW_LIMIT,
            "evidence_rows_exceeded",
            _largest_section(metrics, "evidence_rows"),
            f"证据层 {metrics.evidence_rows} 行，硬上限 {EVIDENCE_ROW_LIMIT}",
        ),
    )
    for failed, code, section, message in checks:
        if failed:
            raise ReportValidationError(
                code, message, section=section, metrics=metrics
            )
    return metrics


JS = r"""
(function(){
  document.documentElement.classList.remove('no-js');
  var bar=document.querySelector('.reading-progress');
  function prog(){var h=document.documentElement,max=h.scrollHeight-h.clientHeight;
    bar.style.transform='scaleX('+(max>0?h.scrollTop/max:0)+')';}
  addEventListener('scroll',prog,{passive:true});prog();

  document.querySelectorAll('.report-document table').forEach(function(t){
    if(t.closest('.table-scroll-region'))return;
    var shell=document.createElement('div');shell.className='table-scroll-shell';
    var region=document.createElement('div');region.className='table-scroll-region';
    region.setAttribute('tabindex','0');
    if(t.rows.length>18)region.classList.add('table-scroll-region--long');
    var hint=document.createElement('p');hint.className='table-scroll-hint';hint.textContent='表格可横向滚动 →';
    t.parentNode.insertBefore(shell,t);shell.appendChild(hint);shell.appendChild(region);region.appendChild(t);
  });

  var links=[].slice.call(document.querySelectorAll('.reader-sidebar a, .mobile-chapters a'));
  var ids=links.map(function(a){return a.getAttribute('href').slice(1);})
               .filter(function(v,i,arr){return arr.indexOf(v)===i;});
  var secs=ids.map(function(id){return document.getElementById(id);}).filter(Boolean);
  function setCurrent(id){links.forEach(function(a){
    if(a.getAttribute('href')==='#'+id)a.setAttribute('aria-current','location');
    else a.removeAttribute('aria-current');});}
  if('IntersectionObserver' in window){
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){if(e.isIntersecting)setCurrent(e.target.id);});
    },{rootMargin:'-15% 0px -70% 0px'});
    secs.forEach(function(s){io.observe(s);});
  }else if(ids.length){setCurrent(ids[0]);}

  var btt=document.querySelector('.back-to-top');
  addEventListener('scroll',function(){btt.classList.toggle('show',scrollY>600);},{passive:true});
  btt.addEventListener('click',function(){scrollTo({top:0,behavior:'smooth'});});

  var allDetails=[].slice.call(document.querySelectorAll('details'));
  var evidence=allDetails.filter(function(d){return d.classList.contains('evidence');});
  var evidenceButton=document.querySelector('.evidence-toggle');
  var autoOpened=new Set();
  function syncEvidenceButton(){
    var allOpen=evidence.length>0&&evidence.every(function(d){return d.open;});
    evidenceButton.textContent=allOpen?'收起证据':'展开证据';
    evidenceButton.setAttribute('aria-expanded',allOpen?'true':'false');
    evidenceButton.disabled=evidence.length===0;
  }
  allDetails.forEach(function(d){
    var summary=d.firstElementChild;
    if(summary&&summary.tagName==='SUMMARY')summary.addEventListener('click',function(){
      if(d.hasAttribute('data-search-opened')){
        d.removeAttribute('data-search-opened');autoOpened.delete(d);}
    });
    d.addEventListener('toggle',syncEvidenceButton);
  });
  evidenceButton.addEventListener('click',function(){
    var open=!(evidence.length>0&&evidence.every(function(d){return d.open;}));
    restoreSearchEvidence();
    evidence.forEach(function(d){d.removeAttribute('data-search-opened');d.open=open;});
    syncEvidenceButton();
  });
  syncEvidenceButton();

  var input=document.querySelector('.reader-search input');
  var count=document.querySelector('.search-controls span');
  var prev=document.querySelector('[data-dir="prev"]'),next=document.querySelector('[data-dir="next"]');
  var doc=document.querySelector('.report-document');
  var hits=[],cur=-1,timer=null;
  function restoreSearchEvidence(){
    autoOpened.forEach(function(d){
      if(d.hasAttribute('data-search-opened')){
        d.open=false;d.removeAttribute('data-search-opened');}
    });
    autoOpened.clear();
    syncEvidenceButton();
  }
  function clear(restore){
    doc.querySelectorAll('.search-hit').forEach(function(m){
      var p=m.parentNode;p.replaceChild(document.createTextNode(m.textContent),m);p.normalize();});
    hits=[];cur=-1;count.textContent='';
    if(restore)restoreSearchEvidence();
  }
  function walk(node,q,out){
    if(node.nodeType===3){
      var txt=node.nodeValue,lo=txt.toLowerCase(),i=lo.indexOf(q);
      if(i<0)return;
      var frag=document.createDocumentFragment(),pos=0;
      while(i>=0){
        frag.appendChild(document.createTextNode(txt.slice(pos,i)));
        var m=document.createElement('mark');m.className='search-hit';
        m.textContent=txt.slice(i,i+q.length);frag.appendChild(m);out.push(m);
        pos=i+q.length;i=lo.indexOf(q,pos);}
      frag.appendChild(document.createTextNode(txt.slice(pos)));
      node.parentNode.replaceChild(frag,node);
    }else if(node.nodeType===1&&!/^(SCRIPT|STYLE|MARK)$/.test(node.tagName)){
      [].slice.call(node.childNodes).forEach(function(c){walk(c,q,out);});}}
  function openAncestors(node){
    var d=node.closest('details');
    while(d){
      if(!d.open){
        d.setAttribute('data-search-opened','true');d.open=true;autoOpened.add(d);}
      d=d.parentElement?d.parentElement.closest('details'):null;
    }
    syncEvidenceButton();
  }
  function activate(index){
    if(!hits.length)return;
    if(cur>=0)hits[cur].removeAttribute('data-active');
    cur=index;var m=hits[cur];openAncestors(m);m.setAttribute('data-active','true');
    m.scrollIntoView({block:'center',behavior:'smooth'});
    count.textContent=(cur+1)+' / '+hits.length;
  }
  function go(d){if(hits.length)activate((cur+d+hits.length)%hits.length);}
  function run(){
    clear(true);
    var q=input.value.trim().toLowerCase();
    if(q.length<2)return;
    walk(doc,q,hits);
    count.textContent=hits.length?'1 / '+hits.length:'0 项';
    if(hits.length)activate(0);
  }
  input.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(run,300);});
  input.addEventListener('keydown',function(e){
    if(e.key==='Enter'){e.preventDefault();go(e.shiftKey?-1:1);}});
  prev.addEventListener('click',function(){go(-1);});
  next.addEventListener('click',function(){go(1);});
})();
"""


def _chunk_path(tmp_dir: Path, report_date: str, chunk: str) -> Path:
    return tmp_dir / f"b{report_date}_{chunk}.html"


def _validate_date(report_date: str) -> None:
    if not _valid_date(report_date):
        raise ReportValidationError(
            "invalid_date", "日期必须是有效的 YYYY-MM-DD"
        )


def render_report(tmp_dir: str | os.PathLike[str], report_date: str) -> str:
    """读取固定 8 个 chunk，包裹静态阅读器外壳并返回 HTML 字符串。"""

    _validate_date(report_date)
    tmp_path = Path(tmp_dir)
    parts: list[str] = []
    missing: list[str] = []
    for chunk in CHUNK_ORDER:
        path = _chunk_path(tmp_path, report_date, chunk)
        if not path.is_file():
            missing.append(path.name)
            continue
        parts.append(
            f'<div class="report-chunk" data-report-chunk="{chunk}">\n'
            f"{path.read_text(encoding='utf-8')}\n"
            "</div>"
        )
    if missing:
        raise ReportValidationError(
            "missing_chunk", f"缺少 chunk：{', '.join(missing)}"
        )

    css_path = Path(__file__).with_name("review_style.css")
    css = css_path.read_text(encoding="utf-8")
    body = "\n\n".join(parts)
    side_nav = "\n".join(f'      <a href="#{item}">{title}</a>' for item, title in NAV)
    mobile_nav = "\n".join(f'    <a href="#{item}">{title}</a>' for item, title in NAV)
    year_month, day = report_date[:7].replace("-", " · "), report_date[8:]
    safe_date = escape(report_date)

    html = f'''<!doctype html>
<html class="no-js" lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>盘后复盘 · {safe_date}</title>
<meta name="description" content="{safe_date} 盘后复盘：市场、板块、情绪、风格、龙头与次日观察。">
<style>
{css}
</style>
</head>
<body>
<div class="reader-app" id="top">
<div aria-hidden="true" class="reading-progress"></div>
<header class="reader-toolbar">
  <a class="reader-brand" href="#top"><span>MARKET REVIEW</span><strong>盘后复盘档案</strong></a>
  <div class="reader-search">
    <input aria-label="搜索整份复盘" placeholder="搜索板块、老师或指标…" type="search">
    <div class="search-controls">
      <span></span>
      <button type="button" data-dir="prev" aria-label="上一个匹配">↑</button>
      <button type="button" data-dir="next" aria-label="下一个匹配">↓</button>
    </div>
  </div>
  <button class="evidence-toggle" type="button" aria-controls="report-document" aria-expanded="false">展开证据</button>
  <nav aria-label="移动章节导航" class="mobile-chapters">
{mobile_nav}
  </nav>
</header>
<div class="reader-layout">
  <aside class="reader-sidebar">
    <p>{year_month} · {day}</p>
    <nav aria-label="章节导航">
{side_nav}
    </nav>
    <small class="sidebar-note">八步复盘法 v1.5 · 9 路多 Agent 完整采集 · compact-v1 只读产物</small>
  </aside>
  <main class="reader-main">
    <article class="report-document" id="report-document" data-report-schema="{REPORT_SCHEMA}" data-report-date="{safe_date}">
{body}
    </article>
  </main>
</div>
<button class="back-to-top" type="button" aria-label="回到顶部">↑</button>
</div>
<script>
{JS}
</script>
</body>
</html>
'''
    return html


def _atomic_write_report(html: str, output_path: str | os.PathLike[str]) -> Path:
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(html)
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def write_report(
    html: str,
    output_path: str | os.PathLike[str],
    *,
    capacity_manifest: dict | None = None,
    new_high_manifest: dict | None = None,
) -> Path:
    """带容量和滚动新高 sidecar 校验后原子落盘；失败时不写文件。"""

    if capacity_manifest is None:
        raise ReportValidationError(
            "missing_capacity_manifest",
            "write_report 落盘必须显式提供容量排名 sidecar",
            section="s5",
        )
    if new_high_manifest is None:
        raise ReportValidationError(
            "missing_new_high_manifest",
            "write_report 落盘必须显式提供滚动新高 sidecar",
            section="s5",
        )
    validate_report(
        html,
        capacity_manifest=capacity_manifest,
        new_high_manifest=new_high_manifest,
    )
    return _atomic_write_report(html, output_path)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        raise ReportValidationError("usage", message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("tmp_dir", help="包含 8 个 HTML chunk 的临时目录")
    parser.add_argument("date", help="复盘交易日，YYYY-MM-DD")
    parser.add_argument(
        "--output",
        help="显式输出路径；省略时写入 data/reports/复盘_<DATE>.html",
    )
    parser.add_argument(
        "--capacity-manifest",
        help="容量排名 sidecar；省略时读取 TMP/capacity_<DATE>.json",
    )
    parser.add_argument(
        "--new-high-manifest",
        help="滚动新高 sidecar；省略时读取 TMP/new_high_<DATE>.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        html = render_report(args.tmp_dir, args.date)
        manifest_path = args.capacity_manifest or (
            Path(args.tmp_dir) / f"capacity_{args.date}.json"
        )
        capacity_manifest = load_capacity_manifest(manifest_path, args.date)
        new_high_manifest_path = args.new_high_manifest or (
            Path(args.tmp_dir) / f"new_high_{args.date}.json"
        )
        new_high_manifest = load_new_high_structure_manifest(
            new_high_manifest_path, args.date
        )
        metrics = validate_report(
            html,
            capacity_manifest=capacity_manifest,
            new_high_manifest=new_high_manifest,
        )
        output = args.output or (_repo_root() / "data" / "reports" / f"复盘_{args.date}.html")
        path = _atomic_write_report(html, output)
    except ReportValidationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"FAIL [io_error] {exc}", file=sys.stderr)
        return 1

    target_note = "（超过 6,000 目标）" if metrics.visible_target_exceeded else ""
    print(
        "OK 已落盘:",
        path,
        f"正文={metrics.visible_chars}字/{metrics.visible_tables}表/{metrics.visible_rows}行{target_note}",
        f"证据={metrics.evidence_chars}字/{metrics.evidence_tables}表/{metrics.evidence_rows}行",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
