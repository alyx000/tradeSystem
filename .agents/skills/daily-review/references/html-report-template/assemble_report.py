#!/usr/bin/env python3
"""组装并校验每日多 Agent 盘后复盘 HTML（compact-v2）。

用法：
    python3 assemble_report.py <TMP目录> <YYYY-MM-DD> [--output PATH]

TMP 必须包含 7 个正式 chunk：
    b<DATE>_{head,s0,s1,s2,s456,s7t,s8ops}.html

新版正式报告不再生成「仓位环境与纪律参考（影子）」和「次日推演」。
组装器仍可以校验已归档的旧版 8-chunk HTML，但新生成默认走 7-chunk 布局。

默认输出 ``data/reports/复盘_<DATE>.html``；``--output`` 可用于生成不覆盖正式
档案的验收样例。组装器只做确定性校验，不会在超限时自动删字或截表。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timedelta
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse


REPORT_SCHEMA = "compact-v2"
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
    "s7t": ("s7", "teachers", "industry", "cognition", "exposure"),
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
    "exposure": "仓位",
    "factor": "因子",
    "proj": "推演",
    "s8": "计划",
    "ops": "缺口",
}
NAV = tuple((anchor, NAV_LABELS[anchor]) for anchor in REQUIRED_ANCHORS)

CURRENT_REPORT_LAYOUT = "without-exposure-proj"
CURRENT_CHUNK_ORDER = ("head", "s0", "s1", "s2", "s456", "s7t", "s8ops")
CURRENT_ANCHOR_MAP = {
    "head": ("tldr", "factor"),
    "s0": ("s0",),
    "s1": ("s1",),
    "s2": ("s2",),
    "s456": ("s3", "s4", "s5", "s6"),
    "s7t": ("s7", "teachers", "industry", "cognition"),
    "s8ops": ("s8", "ops"),
}
CURRENT_REQUIRED_ANCHORS = tuple(
    anchor
    for chunk in CURRENT_CHUNK_ORDER
    for anchor in CURRENT_ANCHOR_MAP[chunk]
)
CURRENT_NAV = tuple(
    (anchor, NAV_LABELS[anchor]) for anchor in CURRENT_REQUIRED_ANCHORS
)

TLDR_CHAR_LIMIT = 500
VISIBLE_CHAR_TARGET = 6_000
VISIBLE_CHAR_LIMIT = 10_500
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
EXPOSURE_MODES = frozenset({"shadow", "fallback", "conflicted", "no_data"})
EXPOSURE_TIERS = {
    "defensive": "防守档",
    "cautious": "谨慎档",
    "neutral": "中性档",
    "constructive": "偏积极档",
    "undetermined": "不可判",
}
EXPOSURE_SOURCES = ("market", "cognition", "teacher")
EXPOSURE_EVIDENCE_ITEMS = (*EXPOSURE_SOURCES, "portfolio")
EXPOSURE_SOURCE_STATUSES = frozenset(
    {"complete", "conflicted", "missing", "stale"}
)
EXPOSURE_BOUNDARY = "read-only-environment-rating"
EXPOSURE_CONDITION_TEXTS = {
    "confirm-if": {
        "market-structure-holds": "[判断]上行门:次日指数结构与市场节点维持。",
        "volume-breadth-improves": "[判断]上行门:次日量能与涨跌家数改善。",
        "risk-signals-ease": "[判断]上行门:次日跌停与高位负反馈缓和。",
        "sources-remain-aligned": (
            "[判断]上行门:次日市场事实继续与认知、老师观点一致。"
        ),
    },
    "invalidate-if": {
        "market-structure-weakens": (
            "[判断]下行门:次日指数结构或市场节点转弱。"
        ),
        "volume-breadth-deteriorates": (
            "[判断]下行门:次日缩量且下跌家数扩散。"
        ),
        "risk-signals-worsen": (
            "[判断]下行门:次日跌停或高位负反馈扩散。"
        ),
        "source-conflict-emerges": (
            "[判断]下行门:次日市场事实与认知、老师观点出现实质冲突。"
        ),
    },
}
EXPOSURE_PORTFOLIO_EVIDENCE_NOT_READ_TEXT = (
    "[事实]对账留痕:本次未读取组合事实层。"
)
EXPOSURE_REVIEW_DATE_MAX_DAYS = 10
EXPOSURE_RETRY_REVIEW_DATE_MAX_DAYS = 20
EXPOSURE_COGNITION_STATUSES = frozenset(
    {"active", "candidate", "none"}
)
EXPOSURE_COGNITION_AVAILABILITY = frozenset(
    {"active", "candidate_only", "none"}
)
EXPOSURE_TIER_TEXTS = frozenset(EXPOSURE_TIERS.values())
EXPOSURE_FALLBACK_TIERS = frozenset({"defensive", "cautious", "neutral"})
EXPOSURE_FALLBACK_MARKET_STATE_KEYS = (
    "data-market-breadth-state",
    "data-market-volume-state",
    "data-market-structure-state",
)
EXPOSURE_FALLBACK_MARKET_STATES = {
    "data-market-breadth-state": frozenset({"weak", "improving", "stable"}),
    "data-market-volume-state": frozenset({"weak", "stable", "improving"}),
    "data-market-structure-state": frozenset(
        {"weak", "unconfirmed", "stable"}
    ),
}
EXPOSURE_FALLBACK_GENERIC_CONDITIONS = {
    "confirm-if": frozenset(
        {
            "market-structure-holds",
            "volume-breadth-improves",
            "risk-signals-ease",
        }
    ),
    "invalidate-if": frozenset(
        {
            "market-structure-weakens",
            "volume-breadth-deteriorates",
            "risk-signals-worsen",
        }
    ),
}
EXPOSURE_VISIBLE_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "br",
        "code",
        "details",
        "em",
        "h2",
        "i",
        "li",
        "p",
        "section",
        "span",
        "strong",
        "ul",
    }
)
EXPOSURE_FORBIDDEN_TAGS = frozenset(
    {"button", "form", "input", "option", "select", "textarea"}
)
EXPOSURE_EMPTY_EVIDENCE_VALUES = frozenset(
    {"", "none", "missing", "unknown", "placeholder", "占位"}
)
EXPOSURE_EVIDENCE_ID_RES = {
    "market": re.compile(
        r"^(?:market_facts|market_timing_signal):\d{4}-\d{2}-\d{2}$"
    ),
    "cognition": re.compile(
        r"^trading_cognitions:cog_[0-9a-f]{8}"
        r"(?:,cog_[0-9a-f]{8})*$"
    ),
    "teacher": re.compile(r"^teacher_notes:[1-9]\d*(?:,[1-9]\d*)*$"),
    "portfolio": re.compile(r"^portfolio_reconciliation:\d{4}-\d{2}-\d{2}$"),
}
EXPOSURE_EVIDENCE_LOOKUP_RES = {
    "market": re.compile(r"^lookup:market:\d{4}-\d{2}-\d{2}$"),
    "cognition": re.compile(
        r"^lookup:trading_cognitions:\d{4}-\d{2}-\d{2}$"
    ),
    "teacher": re.compile(r"^lookup:teacher_notes:\d{4}-\d{2}-\d{2}$"),
    "portfolio": re.compile(r"^lookup:portfolio:\d{4}-\d{2}-\d{2}$"),
}
# 2026-07-25 用户裁定：**「不给仓位建议」红线全系统删除**，`exposure` 可以直接给档位与比例
# （五成 / 七成 / 动态满仓 / 加仓 / 减仓 / 风险预算升降档 …）。
#
# 但顶层红线清单里「不做具体买卖建议」用户未要求删除，故本门**只收窄不拆除**：仓位族全部移出，
# 买卖动作族（买入 / 卖出 / 增持 / 减持 / 清仓 / 建仓 …）保留。两条红线原先混在同一个正则里，
# 整个删掉会连买卖门一起失守——这是收窄而非拆除的唯一理由，改动此处前请先确认是哪条红线要动。
EXPOSURE_DISALLOWED_VISIBLE_RE = re.compile(
    r"(?:买入|卖出|买进|抛出|持有|增持|减持|增配|减配|降配|"
    r"加码|减码|进场|离场|介入|退出|回避|"
    r"清仓|建仓|补仓)"
)
EXPOSURE_FALLBACK_CARD_VISIBLE_MARKERS = (
    "[判断]上行门(六项全满足):",
    "[判断]下行门(任一成立):",
    "[事实]缺口/复核:",
)
EXPOSURE_HEADING_TEXTS = frozenset(
    {
        "仓位环境与纪律参考(影子)",
        "🧭仓位建议与收盘验证门(影子)",
    }
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
    "data-big-picture",
    "data-cross-asset-context",
    "data-rmb-fx-observation",
    "data-rmb-fx-chart",
    "data-emotion-leader",
    "data-emotion-height-chart",
    "data-emotion-node",
    "data-sector-concentration",
    "data-sector-labels",
    "data-rising-recognition",
    "data-falling-recognition",
    "data-new-high-structure",
    "data-event-window",
)
BIG_PICTURE_REQUIRED_TERM_GROUPS = (
    ("大势",),
    ("大类资产", "跨资产"),
    ("外汇", "人民币即期", "USD/CNY"),
    ("掉期", "C-Swap"),
)
CROSS_ASSET_MISSING_TEXT = "[事实]大类资产数据不完整，本日无法判定"
CROSS_ASSET_SOURCE_STATUSES = frozenset({"complete", "partial"})
CROSS_ASSET_ROW_STATUSES = frozenset({"ok", "latest_available"})
CROSS_ASSET_CLASSES = frozenset(
    {
        "global-equity",
        "china-risk",
        "commodity",
        "volatility",
        "rates",
    }
)
RMB_FX_MISSING_TEXT = "[事实]人民币即期与1YC-Swap数据不完整，本日无法判定"
RMB_FX_SOURCE_STATUSES = frozenset({"complete", "partial"})
RMB_FX_AVAILABLE_ROW_STATUSES = frozenset({"ok", "latest_available"})
RMB_FX_ROW_STATUSES = frozenset(
    {*RMB_FX_AVAILABLE_ROW_STATUSES, "missing"}
)
RMB_FX_CHART_MISSING_TEXT = (
    "[事实]USD/CNY即期与1YC-Swap可用同日历史不足8个工作日，暂不绘制趋势图"
)
RMB_FX_CHART_MIN_POINTS = 8
RMB_FX_CHART_MAX_POINTS = 15
EMOTION_LEADER_MISSING_TEXT = (
    "[事实]情绪核心生命周期报告缺失或不可解析，本日无法完成该模块"
)
EMOTION_LEADER_NONE_TEXT = "[事实]本日情绪核心生命周期活跃池为空"
EMOTION_LEADER_MAX_ROWS = 12
EMOTION_LEADER_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
EMOTION_LEADER_STATUSES = frozenset({"ok", "partial", "source_failed"})
EMOTION_LEADER_WAVE_LABELS = frozenset(
    {"单波", "二波", "多波", "二波候选", "多波候选", "未计算"}
)
EMOTION_HEIGHT_CHART_MISSING_TEXT = (
    "[事实]非ST最高连板高度可用历史不足2个交易日，暂不绘制趋势图"
)
EMOTION_HEIGHT_CHART_MIN_POINTS = 2
EMOTION_HEIGHT_CHART_MAX_SAMPLES = 20
EMOTION_NODE_NONE_TEXT = (
    "[事实]今日非ST连板最高高度未超过近20个开放日高度,"
    "未触发情绪启动日节点联动"
)
EMOTION_NODE_MISSING_TEXT = (
    "[事实]情绪高度节点证据不完整,本日无法判定启动日节点联动"
)
EMOTION_NODE_LOOKBACK_OPEN_DAYS = 20
EMOTION_NODE_LAUNCH_METHODS = frozenset({"limit_chain", "calendar_inferred"})
SECTOR_CONCENTRATION_NONE_TEXT = "[事实]本日无可用板块集中度数据"
SECTOR_CONCENTRATION_MISSING_TEXT = "[事实]板块集中度数据不完整，本日无法判定"
SECTOR_LABELS_NONE_TEXT = "[事实]本日半年线、年线与近期价量共振标签均无命中板块"
SECTOR_LABELS_MISSING_TEXT = "[事实]板块趋势标签数据不完整，本日无法判定"
SECTOR_LABELS_CODE_RE = re.compile(r"^\d{6}\.SI$")
SECTOR_LABELS_WINDOW_ATTRS = {
    "data-half-year-window": "144",
    "data-year-window": "233",
    "data-resonance-lookback": "10",
    "data-resonance-breakout-window": "20",
}
SECTOR_LABELS_VERDICT_RE = re.compile(
    r"\A\[事实\]半年线上(?P<half>\d+)、年线上(?P<year>\d+)；"
    r"\[判断\]近期价量共振(?P<resonance>\d+)，"
    r"年线\+共振(?P<year_resonance>\d+)"
    r"(?P<coverage>；(?:当前为)?部分覆盖|；板块趋势标签数据不完整)?[。.;；]?\Z"
)
SECTOR_LABELS_MISSING_VERDICT_RE = re.compile(
    r"\A\[事实\].*(?:半年线|年线).*数据不完整；"
    r"\[判断\].*近期价量共振.*无法判定[。.;；]?\Z"
)
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


@dataclass(frozen=True)
class ExposureValidationContext:
    """来自只读事实库的仓位建议外部校验上下文。"""

    report_date: str
    market_turnover_yiyuan: str
    trade_calendar: Mapping[str, bool]
    active_holdings: int | None = None
    unlinked_holdings: int | None = None
    open_theses: int | None = None
    linked_executions: int | None = None
    latest_broker_biz_date: str | None = None


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
    explicit_hidden: bool = False
    summary_count: int = 0
    summary_text: list[str] = field(default_factory=list)
    summary_visible_text: list[str] = field(default_factory=list)
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
class _ExposureSource:
    source: str
    attrs: dict[str, str]
    default_hidden: bool
    explicit_hidden: bool
    in_evidence_body: bool
    text: list[str] = field(default_factory=list)
    visible_text: list[str] = field(default_factory=list)


@dataclass
class _ExposureEvidence:
    attrs: dict[str, str]
    explicit_hidden: bool
    text: list[str] = field(default_factory=list)
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
    rendered_text: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class _FxChartPoint:
    source_date: str
    spot_mid: float
    forward_rate: float
    swap_point_pips: float


@dataclass(frozen=True)
class _EmotionHeightPoint:
    source_date: str
    height: int | None
    source_status: str


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
    exposure_role: str | None = None
    exposure_source: _ExposureSource | None = None
    exposure_evidence: _ExposureEvidence | None = None
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


def _normalize_guardrail_text(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKC", value)
        .replace("⁄", "/")
        .replace("∕", "/")
    )
    without_format_chars = "".join(
        char
        for char in normalized
        if unicodedata.category(char) not in {"Cf", "Mc", "Me", "Mn"}
    )
    return re.sub(r"\s+", "", without_format_chars)


def _fallback_tier_from_market_state(attrs: Mapping[str, str]) -> str | None:
    states = {
        key: attrs.get(key, "") for key in EXPOSURE_FALLBACK_MARKET_STATE_KEYS
    }
    if any(
        value not in EXPOSURE_FALLBACK_MARKET_STATES[key]
        for key, value in states.items()
    ):
        return None
    breadth = states["data-market-breadth-state"]
    volume = states["data-market-volume-state"]
    structure = states["data-market-structure-state"]
    if breadth == "weak" and structure == "weak":
        return "defensive"
    if breadth != "weak" and volume != "weak" and structure == "stable":
        return "neutral"
    return "cautious"


def _expected_exposure_claim_text(mode: str, tier: str) -> str:
    tier_text = EXPOSURE_TIERS.get(tier, "")
    if not tier_text:
        return ""
    if mode == "fallback" and tier == "cautious":
        return (
            "[判断]结论:谨慎档(低置信);"
            "单日修复不足,上行门全过前不升级。"
        )
    if mode == "fallback":
        return f"[判断]结论:{tier_text}(低置信);按收盘验证门复核。"
    if mode == "shadow":
        return f"[判断]结论:{tier_text};按收盘验证门复核。"
    if mode == "conflicted":
        return "[判断]结论:不可判;证据存在冲突。"
    if mode == "no_data":
        return "[判断]结论:不可判;证据不足。"
    return ""


def _bounded_int_attr(
    attrs: Mapping[str, str],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    raw_value = attrs.get(key, "")
    if not re.fullmatch(r"(?:0|[1-9]\d{0,5})", raw_value):
        return None
    value = int(raw_value)
    if value < minimum or value > maximum:
        return None
    return value


def _expected_exposure_condition_text(
    role: str,
    attrs: Mapping[str, str],
) -> str:
    condition_key = attrs.get("data-exposure-condition", "")
    static_text = EXPOSURE_CONDITION_TEXTS.get(role, {}).get(
        condition_key, ""
    )
    if static_text:
        return static_text

    if role == "confirm-if" and condition_key == "full-close-upside-gate":
        turnover_floor = attrs.get("data-turnover-floor-yiyuan", "")
        universe_size = _bounded_int_attr(
            attrs,
            "data-index-universe-size",
            minimum=3,
            maximum=12,
        )
        ma20_recovery_min = _bounded_int_attr(
            attrs,
            "data-ma20-recovery-min",
            minimum=1,
            maximum=12,
        )
        ma5_hold_min = _bounded_int_attr(
            attrs,
            "data-ma5-hold-min",
            minimum=1,
            maximum=12,
        )
        limit_down_max = _bounded_int_attr(
            attrs,
            "data-limit-down-max",
            minimum=0,
            maximum=100,
        )
        if (
            not re.fullmatch(r"[1-9]\d{3,5}(?:\.\d{1,2})?", turnover_floor)
            or universe_size is None
            or ma20_recovery_min is None
            or ma5_hold_min is None
            or limit_down_max is None
            or ma20_recovery_min > universe_size
            or ma5_hold_min > universe_size
            or attrs.get("data-require-portfolio-reconciled") != "true"
        ):
            return ""
        return (
            f"[判断]上行门(六项全满足):成交额≥{turnover_floor}亿元、"
            "上涨家数占优、"
            f"至少{ma20_recovery_min}个核心宽基收回MA20、"
            f"{universe_size}个观察指数中至少{ma5_hold_min}个站上MA5、"
            f"跌停≤{limit_down_max}、组合完成对账。"
        )

    if role == "invalidate-if" and condition_key == "full-close-downside-gate":
        universe_size = _bounded_int_attr(
            attrs,
            "data-index-universe-size",
            minimum=3,
            maximum=12,
        )
        ma5_break_min = _bounded_int_attr(
            attrs,
            "data-ma5-break-min",
            minimum=1,
            maximum=12,
        )
        limit_down_min = _bounded_int_attr(
            attrs,
            "data-limit-down-min",
            minimum=1,
            maximum=100,
        )
        if (
            universe_size is None
            or ma5_break_min is None
            or limit_down_min is None
            or ma5_break_min > universe_size
        ):
            return ""
        return (
            "[判断]下行门(任一成立):上涨家数不多于下跌家数且"
            f"跌停≥{limit_down_min},或{universe_size}个观察指数中"
            f"至少{ma5_break_min}个收盘低于MA5。"
        )

    return ""


def _expected_exposure_portfolio_evidence_text(
    attrs: Mapping[str, str],
    report_date: date_type,
) -> str:
    status = attrs.get("data-portfolio-evidence-status", "")
    if status == "not-read":
        return EXPOSURE_PORTFOLIO_EVIDENCE_NOT_READ_TEXT
    if status != "unreconciled":
        return ""
    active_holdings = _bounded_int_attr(
        attrs,
        "data-active-holdings",
        minimum=0,
        maximum=100_000,
    )
    unlinked_holdings = _bounded_int_attr(
        attrs,
        "data-unlinked-holdings",
        minimum=0,
        maximum=100_000,
    )
    open_theses = _bounded_int_attr(
        attrs,
        "data-open-theses",
        minimum=0,
        maximum=100_000,
    )
    linked_executions = _bounded_int_attr(
        attrs,
        "data-linked-executions",
        minimum=0,
        maximum=1_000_000,
    )
    latest_biz_date = attrs.get("data-latest-broker-biz-date", "")
    if (
        active_holdings is None
        or unlinked_holdings is None
        or open_theses is None
        or linked_executions is None
        or unlinked_holdings > active_holdings
        or not _valid_date(latest_biz_date)
        or date_type.fromisoformat(latest_biz_date) > report_date
    ):
        return ""
    return (
        f"[事实]对账留痕:{active_holdings}条activeholdings中"
        f"{unlinked_holdings}条未关联thesis;"
        f"{open_theses}条openthesis截至{report_date.isoformat()}关联"
        f"{linked_executions}条非作废券商成交;"
        f"券商事实层最新业务日为{latest_biz_date}。"
    )


def _strict_next_open_from_calendar_span(
    value: str,
    *,
    start_date: date_type,
    max_days: int,
) -> date_type | None:
    parts = value.split(",")
    if not parts or len(parts) > max_days:
        return None
    expected_date = start_date
    parsed: list[tuple[date_type, str]] = []
    for part in parts:
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(open|closed)", part)
        if not match:
            return None
        try:
            current_date = date_type.fromisoformat(match.group(1))
        except ValueError:
            return None
        status = match.group(2)
        if current_date != expected_date:
            return None
        parsed.append((current_date, status))
        expected_date += timedelta(days=1)
    if any(status != "closed" for _, status in parsed[:-1]):
        return None
    if parsed[-1][1] != "open":
        return None
    return parsed[-1][0]


def _expected_exposure_review_text(
    attrs: Mapping[str, str],
    report_date: date_type,
    portfolio_attrs: Mapping[str, str],
) -> str:
    review_date_raw = attrs.get("data-review-date", "")
    retry_date_raw = attrs.get("data-retry-review-date", "")
    if not _valid_date(review_date_raw) or not _valid_date(retry_date_raw):
        return ""
    review_date = date_type.fromisoformat(review_date_raw)
    retry_date = date_type.fromisoformat(retry_date_raw)
    calendar_review_date = _strict_next_open_from_calendar_span(
        attrs.get("data-calendar-span", ""),
        start_date=report_date + timedelta(days=1),
        max_days=EXPOSURE_REVIEW_DATE_MAX_DAYS,
    )
    calendar_retry_date = _strict_next_open_from_calendar_span(
        attrs.get("data-retry-calendar-span", ""),
        start_date=review_date + timedelta(days=1),
        max_days=EXPOSURE_RETRY_REVIEW_DATE_MAX_DAYS,
    )
    if (
        attrs.get("data-calendar-status") != "complete"
        or attrs.get("data-calendar-source") != "trade_calendar"
        or attrs.get("data-calendar-as-of") != report_date.isoformat()
        or calendar_review_date != review_date
        or calendar_retry_date != retry_date
    ):
        return ""
    portfolio_status = portfolio_attrs.get(
        "data-portfolio-evidence-status", ""
    )
    if portfolio_status == "not-read":
        gap_text = "组合事实未读取"
    elif portfolio_status == "unreconciled":
        active_holdings = _bounded_int_attr(
            portfolio_attrs,
            "data-active-holdings",
            minimum=0,
            maximum=100_000,
        )
        unlinked_holdings = _bounded_int_attr(
            portfolio_attrs,
            "data-unlinked-holdings",
            minimum=0,
            maximum=100_000,
        )
        open_theses = _bounded_int_attr(
            portfolio_attrs,
            "data-open-theses",
            minimum=0,
            maximum=100_000,
        )
        linked_executions = _bounded_int_attr(
            portfolio_attrs,
            "data-linked-executions",
            minimum=0,
            maximum=1_000_000,
        )
        latest_biz_date = portfolio_attrs.get(
            "data-latest-broker-biz-date", ""
        )
        if (
            active_holdings is None
            or unlinked_holdings is None
            or open_theses is None
            or linked_executions is None
            or unlinked_holdings > active_holdings
            or not _valid_date(latest_biz_date)
            or date_type.fromisoformat(latest_biz_date) > report_date
        ):
            return ""
        gap_text = (
            f"{active_holdings}条activeholdings中{unlinked_holdings}条"
            f"未关联thesis,{open_theses}条openthesis关联"
            f"{linked_executions}条非作废券商成交;券商事实至{latest_biz_date}"
        )
    else:
        return ""
    return (
        f"[事实]缺口/复核:{gap_text};{review_date_raw}收盘复核,"
        f"数据或对账未齐则顺延至{retry_date_raw};盘中不判。"
    )


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


def _parse_local_datetime(value: str) -> datetime | None:
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?",
        value,
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


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
        self.schema_hosts: list[tuple[str, str, str | None, str, str]] = []
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
        self.exposure_hosts: list[tuple[str, frozenset[str]]] = []
        self.exposure_modes: list[str] = []
        self.exposure_tiers: list[str] = []
        self.exposure_boundaries: list[str] = []
        self.exposure_sources: list[_ExposureSource] = []
        self.exposure_roles: dict[str, list[list[str]]] = {
            "confirm-if": [],
            "invalidate-if": [],
            "review-rule": [],
        }
        self.exposure_role_attrs: dict[str, list[dict[str, str]]] = {
            "confirm-if": [],
            "invalidate-if": [],
            "review-rule": [],
        }
        self.exposure_evidence_sources: list[_ExposureEvidence] = []
        self.exposure_heading_text: list[str] = []
        self.exposure_section_hidden = False
        self.exposure_hidden_contract_elements = 0
        self.exposure_all_text: list[str] = []
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

    def _current_exposure_role(self) -> str | None:
        for frame in reversed(self.stack):
            if frame.exposure_role:
                return frame.exposure_role
        return None

    def _current_exposure_source(self) -> _ExposureSource | None:
        for frame in reversed(self.stack):
            if frame.exposure_source:
                return frame.exposure_source
        return None

    def _current_exposure_evidence(self) -> _ExposureEvidence | None:
        for frame in reversed(self.stack):
            if frame.exposure_evidence:
                return frame.exposure_evidence
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
        explicit_hidden_path = explicit_hidden or any(
            frame.explicit_hidden for frame in self.stack
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
        if (
            section_for_element == "exposure"
            and tag in EXPOSURE_FORBIDDEN_TAGS
        ):
            self._error(
                "invalid_exposure_contract",
                f"exposure 不允许交互式 <{tag}> 标签",
                section="exposure",
            )
        if section_for_element == "exposure" and "style" in attrs:
            self._error(
                "invalid_exposure_contract",
                "exposure 内禁止内联 style",
                section="exposure",
            )
        if (
            section_for_element == "exposure"
            and tag == "details"
            and (
                "evidence" not in classes
                or attrs.get("data-evidence-kind") != "exposure-detail"
            )
        ):
            self._error(
                "invalid_exposure_contract",
                "exposure 只允许唯一 exposure-detail 折叠块",
                section="exposure",
            )
        if (
            section_for_element == "exposure"
            and not self._in_evidence_body()
            and tag not in EXPOSURE_VISIBLE_ALLOWED_TAGS
        ):
            self._error(
                "invalid_exposure_contract",
                f"exposure 默认可见内容不允许 <{tag}> 标签",
                section="exposure",
            )
        if section_for_element == "exposure":
            rendered_attrs = "".join(
                attrs.get(name, "")
                for name in (
                    "alt",
                    "aria-label",
                    "data-evidence-boundary",
                    "placeholder",
                    "title",
                    "value",
                )
            )
            if EXPOSURE_DISALLOWED_VISIBLE_RE.search(
                _normalize_guardrail_text(rendered_attrs)
            ):
                self._error(
                    "invalid_exposure_contract",
                    "exposure 全部属性不得携带买卖动作指令（仓位比例已于 2026-07-25 解禁）",
                    section="exposure",
                )

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
        if element_id == "exposure":
            self.exposure_hosts.append((tag, frozenset(classes)))
            self.exposure_modes.append(attrs.get("data-exposure-mode", ""))
            self.exposure_tiers.append(attrs.get("data-exposure-tier", ""))
            self.exposure_boundaries.append(
                attrs.get("data-exposure-boundary", "")
            )
            self.exposure_section_hidden = default_hidden
        exposure_role = attrs.get("data-exposure-role") or None
        exposure_source: _ExposureSource | None = None
        exposure_source_name = attrs.get("data-exposure-source")
        if exposure_source_name is not None:
            current_exposure_evidence = self._current_evidence()
            if (
                tag != "p"
                or self._current_section() != "exposure"
                or not self._in_evidence_body()
                or current_exposure_evidence is None
                or current_exposure_evidence.kind != "exposure-detail"
                or attrs.get("data-exposure-evidence")
                != exposure_source_name
            ):
                self._error(
                    "invalid_exposure_contract",
                    "仓位来源必须与同名 evidence 合并并置于 exposure-detail 正文",
                    section="exposure",
                )
            else:
                exposure_source = _ExposureSource(
                    source=exposure_source_name,
                    attrs=attrs,
                    default_hidden=default_hidden,
                    explicit_hidden=explicit_hidden_path,
                    in_evidence_body=True,
                )
                self.exposure_sources.append(exposure_source)

        exposure_evidence: _ExposureEvidence | None = None
        exposure_evidence_source = attrs.get("data-exposure-evidence")
        if exposure_evidence_source is not None:
            current_exposure_evidence = self._current_evidence()
            if (
                tag != "p"
                or self._current_section() != "exposure"
                or not self._in_evidence_body()
                or current_exposure_evidence is None
                or current_exposure_evidence.kind != "exposure-detail"
            ):
                self._error(
                    "invalid_exposure_contract",
                    "data-exposure-evidence 必须位于 exposure-detail 证据正文",
                    section="exposure",
                )
            exposure_evidence = _ExposureEvidence(
                attrs=attrs,
                explicit_hidden=explicit_hidden_path,
            )
            self.exposure_evidence_sources.append(exposure_evidence)

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
                (
                    schema,
                    tag,
                    element_id,
                    attrs.get("data-report-date", ""),
                    attrs.get("data-report-layout", ""),
                )
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
                explicit_hidden=explicit_hidden_path,
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
            exposure_role=exposure_role,
            exposure_source=exposure_source,
            exposure_evidence=exposure_evidence,
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
        if section == "exposure" and not self._in_evidence_body():
            if exposure_role and exposure_role not in self.exposure_roles:
                self._error(
                    "invalid_exposure_contract",
                    f"未知 data-exposure-role：{exposure_role}",
                    section="exposure",
                )
            elif exposure_role and not self._in_default_hidden():
                self.exposure_roles[exposure_role].append([])
                self.exposure_role_attrs[exposure_role].append(attrs)
            if self._in_default_hidden() and (
                claim is not None
                or exposure_source is not None
                or exposure_role is not None
            ):
                self.exposure_hidden_contract_elements += 1
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
        exposure_source = self._current_exposure_source()
        if exposure_source:
            exposure_source.text.append(data)
            if not any(frame.explicit_hidden for frame in self.stack):
                exposure_source.visible_text.append(data)
        exposure_evidence = self._current_exposure_evidence()
        if exposure_evidence:
            exposure_evidence.text.append(data)
            if not any(frame.explicit_hidden for frame in self.stack):
                exposure_evidence.visible_text.append(data)
        exposure_role = self._current_exposure_role()
        if (
            exposure_role in self.exposure_roles
            and not self._in_default_hidden()
            and self.exposure_roles[exposure_role]
        ):
            self.exposure_roles[exposure_role][-1].append(data)
        evidence = self._current_evidence()
        if evidence and self._in_evidence_summary():
            evidence.summary_text.append(data)
            if not any(frame.explicit_hidden for frame in self.stack):
                evidence.summary_visible_text.append(data)
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
            if not any(frame.explicit_hidden for frame in self.stack):
                structured_row.rendered_text.append(data)
        structured_contract = self._current_structured_contract()
        if structured_contract:
            structured_contract.text.append(data)
            if not any(frame.explicit_hidden for frame in self.stack):
                structured_contract.rendered_text.append(data)

        section = self._current_section()
        if section == "exposure":
            self.exposure_all_text.append(data)
            if self._inside_tag("h2"):
                self.exposure_heading_text.append(data)
        count = _compact_char_count(data)
        if not count:
            return
        if (
            section == "exposure"
            and not self._in_evidence_body()
            and not self._in_default_hidden()
            and not self._inside_tag("h2")
            and claim is None
            and exposure_source is None
            and exposure_role is None
            and not (evidence and self._in_evidence_summary())
        ):
            self._error(
                "invalid_exposure_contract",
                "exposure 默认可见正文必须归属 Claim、三类来源或受控 role",
                section="exposure",
            )
        if evidence and self._in_evidence_body():
            evidence.body_chars += count
            evidence.body_text.append(data)
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
        if (
            section == "exposure"
            and not self._in_evidence_body()
            and self._in_default_hidden()
            and (
                claim is not None
                or exposure_source is not None
                or exposure_role is not None
            )
        ):
            self.exposure_hidden_contract_elements += 1
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

    def _validate_exposure_contract(self, report_date: date_type) -> None:
        if (
            len(self.exposure_hosts) != 1
            or self.exposure_hosts[0][0] != "section"
            or "blk" not in self.exposure_hosts[0][1]
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 必须使用 section.blk 作为默认可见章节容器",
                section="exposure",
            )

        mode = self.exposure_modes[0] if len(self.exposure_modes) == 1 else ""
        tier = self.exposure_tiers[0] if len(self.exposure_tiers) == 1 else ""
        boundary = (
            self.exposure_boundaries[0]
            if len(self.exposure_boundaries) == 1
            else ""
        )
        if (
            mode not in EXPOSURE_MODES
            or tier not in EXPOSURE_TIERS
            or boundary != EXPOSURE_BOUNDARY
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 必须声明合法 mode、tier 与只读环境评级边界",
                section="exposure",
            )
        if self.exposure_section_hidden or self.exposure_hidden_contract_elements:
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 的裁决与验证条件必须默认可见",
                section="exposure",
            )
        if (
            _normalize_guardrail_text("".join(self.exposure_heading_text))
            not in EXPOSURE_HEADING_TEXTS
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 标题必须使用受控影子参考模板",
                section="exposure",
            )

        visible_claims = [
            claim
            for claim in self.claims.values()
            if claim.section == "exposure"
            and not claim.in_evidence_body
            and not claim.default_hidden
        ]
        if len(visible_claims) != 1 or visible_claims[0].kind != "judgment":
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 必须且只能有 1 条可见 judgment Claim",
                section="exposure",
            )
        if visible_claims[0].as_of != report_date.isoformat():
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure Claim 的 data-as-of 必须严格等于报告日",
                section="exposure",
            )
        claim_text = _normalize_guardrail_text(
            "".join(visible_claims[0].visible_text)
        )
        expected_tier_text = EXPOSURE_TIERS[tier]
        tier_mentions = [
            label for label in EXPOSURE_TIER_TEXTS if label in claim_text
        ]
        expected_claim_text = _expected_exposure_claim_text(mode, tier)
        if (
            claim_text != expected_claim_text
            or tier_mentions != [expected_tier_text]
            or claim_text.count(expected_tier_text) != 1
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure Claim 必须使用报告日唯一规范定性档位模板",
                section="exposure",
            )

        all_exposure_text = _normalize_guardrail_text(
            "".join(self.exposure_all_text)
        )
        if EXPOSURE_DISALLOWED_VISIBLE_RE.search(all_exposure_text):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 全部内容不得给具体买卖指令（仓位比例已于 2026-07-25 解禁）",
                section="exposure",
            )
        if (mode, tier) != ("fallback", "cautious") and any(
            marker in all_exposure_text
            for marker in EXPOSURE_FALLBACK_CARD_VISIBLE_MARKERS
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "谨慎档专用验证文案只允许出现在 fallback+cautious 的受控角色中",
                section="exposure",
            )

        sources_by_name: dict[str, _ExposureSource] = {}
        for source in self.exposure_sources:
            if (
                source.source not in EXPOSURE_SOURCES
                or source.source in sources_by_name
                or not source.default_hidden
                or source.explicit_hidden
                or not source.in_evidence_body
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "exposure 必须在折叠留痕中各保留唯一 market/cognition/teacher 来源",
                    section="exposure",
                )
            sources_by_name[source.source] = source
        if tuple(sources_by_name) != EXPOSURE_SOURCES:
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 来源必须按 market、cognition、teacher 顺序各出现一次",
                section="exposure",
            )

        source_statuses: dict[str, str] = {}
        for source_name, source in sources_by_name.items():
            attrs = source.attrs
            source_status = attrs.get("data-source-status", "")
            as_of = attrs.get("data-as-of", "")
            if (
                source_status not in EXPOSURE_SOURCE_STATUSES
                or not _valid_date(as_of)
                or date_type.fromisoformat(as_of) > report_date
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "exposure 来源必须带合法状态和不晚于报告日的 data-as-of",
                    section="exposure",
                )
            source_statuses[source_name] = source_status
            compact_text = re.sub(
                r"\s+", "", "".join(source.visible_text)
            )
            if source_status in {"complete", "conflicted"}:
                required_label = {
                    "market": "[事实]",
                    "cognition": "[历史认知]",
                    "teacher": "[老师观点]",
                }[source_name]
            else:
                required_label = "[事实]"
            if required_label not in compact_text or compact_text == required_label:
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    f"exposure 的 {source_name} 来源缺少规范标签或实质正文",
                    section="exposure",
                )
            if (
                source_status == "missing"
                and as_of != report_date.isoformat()
            ) or (
                source_name in {"market", "teacher"}
                and (
                    (
                        source_status in {"complete", "conflicted"}
                        and as_of != report_date.isoformat()
                    )
                    or (
                        source_status == "stale"
                        and as_of >= report_date.isoformat()
                    )
                )
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    f"{source_name} 的缺失查询须归属报告日；市场/老师完整来源归属报告日，陈旧来源早于报告日",
                    section="exposure",
                )
            if (
                source_name == "teacher"
                and attrs.get("data-teacher-date-field") != "date"
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "老师观点必须按 teacher_notes.date 归属，不得使用 created_at 替代",
                    section="exposure",
                )
            if source_name == "cognition":
                cognition_availability = attrs.get(
                    "data-cognition-availability", ""
                )
                cognition_status = attrs.get("data-cognition-status", "")
                cognition_category = attrs.get("data-cognition-category", "")
                if (
                    cognition_availability
                    not in EXPOSURE_COGNITION_AVAILABILITY
                    or cognition_status not in EXPOSURE_COGNITION_STATUSES
                ):
                    raise ReportValidationError(
                        "invalid_exposure_contract",
                        "历史认知必须分别声明可用性与真实生命周期",
                        section="exposure",
                    )
                if (
                    source_status in {"complete", "conflicted"}
                    and cognition_availability != "active"
                ) or (
                    source_status == "missing"
                    and cognition_availability == "active"
                ) or (
                    source_status == "stale"
                    and cognition_availability == "none"
                ):
                    raise ReportValidationError(
                        "invalid_exposure_contract",
                        "candidate 或缺失认知不得冒充可用于仓位参考的 active 认知",
                        section="exposure",
                    )
                if (
                    cognition_availability == "active"
                    and (
                        cognition_status != "active"
                        or cognition_category != "sizing"
                    )
                ) or (
                    cognition_availability == "candidate_only"
                    and (
                        cognition_status != "candidate"
                        or cognition_category != "sizing"
                    )
                ) or (
                    cognition_availability == "none"
                    and cognition_status != "none"
                ):
                    raise ReportValidationError(
                        "invalid_exposure_contract",
                        "仓位参考只接受 category=sizing 且 lifecycle 与 availability 一致的历史认知",
                        section="exposure",
                    )

        confirm_blocks = self.exposure_roles["confirm-if"]
        invalidate_blocks = self.exposure_roles["invalidate-if"]
        for role, blocks in (
            ("confirm-if", confirm_blocks),
            ("invalidate-if", invalidate_blocks),
        ):
            role_attrs = self.exposure_role_attrs[role]
            if blocks:
                expected_condition_text = _expected_exposure_condition_text(
                    role,
                    role_attrs[0] if len(role_attrs) == 1 else {},
                )
                actual_condition_text = (
                    _normalize_guardrail_text("".join(blocks[0]))
                    if len(blocks) == 1
                    else ""
                )
                if (
                    not expected_condition_text
                    or actual_condition_text != expected_condition_text
                ):
                    raise ReportValidationError(
                        "invalid_exposure_contract",
                        "exposure 确认/失效条件必须使用受控市场事实模板",
                        section="exposure",
                    )
        expected_visible_blocks = (
            4
            if (mode, tier) == ("fallback", "cautious")
            else 3
            if mode in {"shadow", "fallback"}
            else 1
        )
        actual_visible_blocks = 1 + sum(
            len(blocks) for blocks in self.exposure_roles.values()
        )
        if actual_visible_blocks != expected_visible_blocks:
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 默认可见区只能保留结论、验证门与必要的缺口/复核",
                section="exposure",
            )
        cognition_attrs = sources_by_name["cognition"].attrs
        fallback_inputs_eligible = (
            source_statuses["market"] == "complete"
            and source_statuses["cognition"] == "missing"
            and cognition_attrs.get("data-cognition-availability")
            == "candidate_only"
            and cognition_attrs.get("data-cognition-status") == "candidate"
            and cognition_attrs.get("data-cognition-category") == "sizing"
            and source_statuses["teacher"] in {"complete", "conflicted"}
        )
        portfolio_evidence_items = [
            evidence
            for evidence in self.exposure_evidence_sources
            if evidence.attrs.get("data-exposure-evidence") == "portfolio"
        ]
        portfolio_evidence_attrs = (
            portfolio_evidence_items[0].attrs
            if len(portfolio_evidence_items) == 1
            else {}
        )
        if (
            (mode, tier) != ("fallback", "cautious")
            and self.exposure_roles["review-rule"]
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "缺口/复核行只允许用于满足精确资格的 cautious fallback",
                section="exposure",
            )
        if mode == "shadow":
            if (
                tier == "undetermined"
                or any(status != "complete" for status in source_statuses.values())
                or len(confirm_blocks) != 1
                or len(invalidate_blocks) != 1
                or not _has_labeled_content(confirm_blocks[0])
                or not _has_labeled_content(invalidate_blocks[0])
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "shadow 仓位环境须有三类完整来源、定性档位及各一条确认/失效条件",
                    section="exposure",
                )
        elif mode == "fallback":
            expected_fallback_tier = _fallback_tier_from_market_state(
                sources_by_name["market"].attrs
            )
            confirm_condition = (
                self.exposure_role_attrs["confirm-if"][0].get(
                    "data-exposure-condition", ""
                )
                if len(confirm_blocks) == 1
                else ""
            )
            invalidate_condition = (
                self.exposure_role_attrs["invalidate-if"][0].get(
                    "data-exposure-condition", ""
                )
                if len(invalidate_blocks) == 1
                else ""
            )
            review_blocks = self.exposure_roles["review-rule"]
            confirm_attrs = (
                self.exposure_role_attrs["confirm-if"][0]
                if len(confirm_blocks) == 1
                else {}
            )
            review_attrs = self.exposure_role_attrs["review-rule"]
            portfolio_evidence_text = (
                _normalize_guardrail_text(
                    "".join(portfolio_evidence_items[0].visible_text)
                )
                if len(portfolio_evidence_items) == 1
                else ""
            )
            expected_portfolio_evidence_text = (
                _expected_exposure_portfolio_evidence_text(
                    portfolio_evidence_attrs,
                    report_date,
                )
                if portfolio_evidence_attrs
                else ""
            )
            portfolio_evidence_status = (
                portfolio_evidence_attrs.get(
                    "data-portfolio-evidence-status", ""
                )
                if portfolio_evidence_attrs
                else ""
            )
            review_text = (
                _normalize_guardrail_text("".join(review_blocks[0]))
                if len(review_blocks) == 1
                else ""
            )
            expected_review_text = (
                _expected_exposure_review_text(
                    review_attrs[0],
                    report_date,
                    portfolio_evidence_attrs,
                )
                if len(review_attrs) == 1
                else ""
            )
            cautious_card_invalid = tier == "cautious" and (
                confirm_condition != "full-close-upside-gate"
                or invalidate_condition != "full-close-downside-gate"
                or confirm_attrs.get("data-turnover-floor-yiyuan", "")
                != sources_by_name["market"].attrs.get(
                    "data-market-turnover-yiyuan", ""
                )
                or portfolio_evidence_status
                not in {"unreconciled", "not-read"}
                or not expected_portfolio_evidence_text
                or portfolio_evidence_text
                != expected_portfolio_evidence_text
                or not expected_review_text
                or review_text != expected_review_text
            )
            generic_condition_invalid = tier != "cautious" and (
                confirm_condition
                not in EXPOSURE_FALLBACK_GENERIC_CONDITIONS["confirm-if"]
                or invalidate_condition
                not in EXPOSURE_FALLBACK_GENERIC_CONDITIONS["invalidate-if"]
            )
            if (
                not fallback_inputs_eligible
                or tier not in EXPOSURE_FALLBACK_TIERS
                or tier != expected_fallback_tier
                or len(confirm_blocks) != 1
                or len(invalidate_blocks) != 1
                or not _has_labeled_content(confirm_blocks[0])
                or not _has_labeled_content(invalidate_blocks[0])
                or cautious_card_invalid
                or generic_condition_invalid
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "fallback 须满足精确来源资格、受控市场状态到档位映射及精简收盘验证契约",
                    section="exposure",
                )
            ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
            if not all(
                marker in ops_text
                for marker in (
                    "仓位建议置信度较低",
                    "市场事实主导",
                    "候选认知只作约束",
                )
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "fallback 必须披露低置信原因，并声明市场事实主导、候选认知只作约束",
                    section="ops",
                )
        elif mode == "conflicted":
            if (
                tier != "undetermined"
                or "conflicted" not in source_statuses.values()
                or any(
                    status in {"missing", "stale"}
                    for status in source_statuses.values()
                )
                or confirm_blocks
                or invalidate_blocks
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "conflicted 模式只能并列冲突，不得给档位或验证指令",
                    section="exposure",
                )
            ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
            if "仓位参考存在冲突" not in ops_text:
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "conflicted 仓位参考必须在数据缺口章节保持可见",
                    section="ops",
                )
        else:
            if (
                tier != "undetermined"
                or not any(
                    status in {"missing", "stale"}
                    for status in source_statuses.values()
                )
                or confirm_blocks
                or invalidate_blocks
                or fallback_inputs_eligible
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "no_data 模式必须有无法降级的缺失或陈旧来源，且不得给档位或验证指令",
                    section="exposure",
                )
            ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
            if "仓位参考证据不足" not in ops_text:
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "no_data 仓位参考必须在数据缺口章节保持可见",
                    section="ops",
                )

        detail_evidence = [
            evidence
            for evidence in self.evidences
            if evidence.section == "exposure"
            and evidence.kind == "exposure-detail"
        ]
        evidence_source_names = tuple(
            evidence.attrs.get("data-exposure-evidence", "")
            for evidence in self.exposure_evidence_sources
        )
        if (
            len(detail_evidence) != 1
            or detail_evidence[0].explicit_hidden
            or detail_evidence[0].as_of != report_date.isoformat()
            or detail_evidence[0].items != "4"
            or _normalize_guardrail_text(
                "".join(detail_evidence[0].summary_visible_text)
            )
            != (
                "仓位证据与对账留痕·"
                f"{report_date.isoformat()}·4项"
            )
            or evidence_source_names != EXPOSURE_EVIDENCE_ITEMS
        ):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 必须保留报告日唯一 exposure-detail，并按三类来源及组合对账完整留痕",
                section="exposure",
            )
        evidence_ids: list[str] = []
        cognition_availability = sources_by_name["cognition"].attrs.get(
            "data-cognition-availability", ""
        )
        for exposure_evidence in self.exposure_evidence_sources:
            evidence_attrs = exposure_evidence.attrs
            source_name = evidence_attrs["data-exposure-evidence"]
            if exposure_evidence.explicit_hidden:
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "exposure-detail 展开后的四项证据不得再被 hidden",
                    section="exposure",
                )
            evidence_id = evidence_attrs.get("data-evidence-id", "").strip()
            evidence_as_of = evidence_attrs.get("data-as-of", "")
            boundary = evidence_attrs.get("data-evidence-boundary", "").strip()
            evidence_text = _normalize_guardrail_text(
                "".join(exposure_evidence.visible_text)
            )
            if source_name == "portfolio":
                portfolio_status = evidence_attrs.get(
                    "data-portfolio-evidence-status", ""
                )
                typed_id = bool(
                    EXPOSURE_EVIDENCE_ID_RES[source_name].fullmatch(
                        evidence_id
                    )
                    and evidence_id.endswith(f":{evidence_as_of}")
                )
                lookup_id = bool(
                    EXPOSURE_EVIDENCE_LOOKUP_RES[source_name].fullmatch(
                        evidence_id
                    )
                    and evidence_id.endswith(f":{evidence_as_of}")
                )
                expected_portfolio_text = (
                    _expected_exposure_portfolio_evidence_text(
                        evidence_attrs,
                        report_date,
                    )
                )
                if (
                    evidence_id.lower() in EXPOSURE_EMPTY_EVIDENCE_VALUES
                    or boundary.lower() in EXPOSURE_EMPTY_EVIDENCE_VALUES
                    or len(_normalize_guardrail_text(boundary)) < 4
                    or evidence_as_of != report_date.isoformat()
                    or (
                        portfolio_status == "unreconciled"
                        and not typed_id
                    )
                    or (portfolio_status == "not-read" and not lookup_id)
                    or not expected_portfolio_text
                    or evidence_text != expected_portfolio_text
                ):
                    raise ReportValidationError(
                        "invalid_exposure_contract",
                        "exposure 组合对账必须保留受控状态、日期、正文与类型化留痕",
                        section="exposure",
                    )
                evidence_ids.append(evidence_id)
                continue
            source_status = source_statuses[source_name]
            typed_id = bool(
                EXPOSURE_EVIDENCE_ID_RES[source_name].fullmatch(evidence_id)
            )
            if typed_id and source_name == "market":
                typed_id = evidence_id.endswith(f":{evidence_as_of}")
            if typed_id and source_name in {"cognition", "teacher"}:
                record_ids = evidence_id.split(":", 1)[1].split(",")
                typed_id = len(record_ids) == len(set(record_ids))
            lookup_id = bool(
                EXPOSURE_EVIDENCE_LOOKUP_RES[source_name].fullmatch(evidence_id)
                and evidence_id.endswith(f":{evidence_as_of}")
            )
            if source_name in {"market", "teacher"}:
                id_matches_status = (
                    lookup_id if source_status == "missing" else typed_id
                )
            elif cognition_availability == "none":
                id_matches_status = lookup_id
            else:
                id_matches_status = typed_id
            fallback_market_state_matches = (
                mode != "fallback"
                or source_name != "market"
                or all(
                    evidence_attrs.get(key)
                    == sources_by_name["market"].attrs.get(key)
                    for key in EXPOSURE_FALLBACK_MARKET_STATE_KEYS
                )
            )
            cautious_market_metric_matches = (
                (mode, tier) != ("fallback", "cautious")
                or source_name != "market"
                or evidence_attrs.get("data-market-turnover-yiyuan", "")
                == sources_by_name["market"].attrs.get(
                    "data-market-turnover-yiyuan", ""
                )
            )
            if (
                evidence_id.lower() in EXPOSURE_EMPTY_EVIDENCE_VALUES
                or boundary.lower() in EXPOSURE_EMPTY_EVIDENCE_VALUES
                or len(_normalize_guardrail_text(boundary)) < 4
                or len(evidence_text) < 4
                or not _valid_date(evidence_as_of)
                or evidence_as_of
                != sources_by_name[source_name].attrs.get("data-as-of", "")
                or not id_matches_status
                or not fallback_market_state_matches
                or not cautious_market_metric_matches
            ):
                raise ReportValidationError(
                    "invalid_exposure_contract",
                    "exposure 三类证据必须逐项保留类型化真实/查询 ID、来源日期、正文与适用/失效边界",
                    section="exposure",
                )
            evidence_ids.append(evidence_id)
        if len(set(evidence_ids)) != len(EXPOSURE_EVIDENCE_ITEMS):
            raise ReportValidationError(
                "invalid_exposure_contract",
                "exposure 三类证据 ID 不得重复",
                section="exposure",
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

    def _validate_big_picture_contract(self, report_date: date_type) -> None:
        """确保 s1 不会再静默丢失大类资产、人民币即期与外汇掉期。"""

        verdicts = self.structured_contracts["data-big-picture"]
        cross_asset_blocks = self.structured_contracts[
            "data-cross-asset-context"
        ]
        fx_blocks = self.structured_contracts["data-rmb-fx-observation"]
        if (
            len(verdicts) != 1
            or len(cross_asset_blocks) != 1
            or len(fx_blocks) != 1
        ):
            raise ReportValidationError(
                "invalid_big_picture",
                "s1 必须且只能包含一句大势摘要、一份大类资产证据和一份人民币即期/1Y C-Swap 证据",
                section="s1",
            )

        verdict = verdicts[0]
        verdict_text = re.sub(
            r"\s+", "", "".join(verdict.rendered_text)
        )
        if (
            verdict.value != "verdict"
            or verdict.tag != "p"
            or verdict.section != "s1"
            or verdict.default_hidden
            or not self._valid_contract_as_of(verdict, report_date)
            or "[判断]" not in verdict_text
            or any(
                not any(term in verdict_text for term in group)
                for group in BIG_PICTURE_REQUIRED_TERM_GROUPS
            )
            or verdict.attrs.get("data-reviewed-through")
            != report_date.isoformat()
        ):
            raise ReportValidationError(
                "invalid_big_picture",
                "大势摘要必须是 s1 默认可见、日期有效且同时点明大势/大类资产/外汇/掉期的判断句",
                section="s1",
            )

        cross_asset = cross_asset_blocks[0]
        fx = fx_blocks[0]
        if (
            not cross_asset.default_hidden
            or not fx.default_hidden
            or cross_asset.attrs.get("data-as-of")
            != verdict.attrs.get("data-as-of")
            or fx.attrs.get("data-as-of")
            != verdict.attrs.get("data-as-of")
            or cross_asset.attrs.get("data-reviewed-through")
            != report_date.isoformat()
            or fx.attrs.get("data-reviewed-through")
            != report_date.isoformat()
        ):
            raise ReportValidationError(
                "invalid_big_picture",
                "大类资产与外汇掉期明细必须进入 s1 折叠证据并与可见摘要使用同一观察日",
                section="s1",
            )

        self._validate_cross_asset_context(cross_asset, report_date)
        self._validate_rmb_fx_observation(fx, report_date)

    def _validate_cross_asset_context(
        self,
        contract: _StructuredContract,
        report_date: date_type,
    ) -> None:
        if (
            contract.section != "s1"
            or not self._valid_contract_as_of(contract, report_date)
        ):
            raise ReportValidationError(
                "invalid_cross_asset_context",
                "大类资产证据必须归属 s1 并带不晚于报告日的 data-as-of",
                section="s1",
            )

        source_status = contract.attrs.get("data-source-status", "")
        compact_text = re.sub(
            r"\s+", "", "".join(contract.rendered_text)
        ).rstrip("。.;；")
        ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or source_status not in {"partial", "failed"}
                or compact_text != CROSS_ASSET_MISSING_TEXT
                or "大类资产数据不完整" not in ops_text
            ):
                raise ReportValidationError(
                    "invalid_cross_asset_context",
                    "大类资产缺失态必须显式标记 partial/failed 并在 ops 保持可见",
                    section="s1",
                )
            return

        if (
            contract.value != "v1"
            or contract.tag != "table"
            or source_status not in CROSS_ASSET_SOURCE_STATUSES
            or len(contract.rows) < 4
            or not compact_text
        ):
            raise ReportValidationError(
                "invalid_cross_asset_context",
                "大类资产仅允许至少三项有效资产线索的 v1 表或显式 missing-data",
                section="s1",
            )
        if (
            source_status == "partial"
            and "大类资产数据不完整" not in ops_text
        ):
            raise ReportValidationError(
                "invalid_cross_asset_context",
                "大类资产 partial 状态必须在 ops 披露数据不完整",
                section="s1",
            )

        as_of = date_type.fromisoformat(contract.attrs["data-as-of"])
        seen_instruments: set[str] = set()
        seen_classes: set[str] = set()
        seen_fetch_only = False
        for row in contract.rows[1:]:
            asset_class = row.attrs.get("data-asset-class", "").strip()
            instrument = row.attrs.get("data-instrument", "").strip()
            source = row.attrs.get("data-source", "").strip()
            date_kind = row.attrs.get("data-date-kind", "").strip()
            source_date = row.attrs.get("data-source-date", "").strip()
            observed_at = row.attrs.get("data-observed-at", "").strip()
            status = row.attrs.get("data-status", "").strip()
            primary_value = row.attrs.get("data-primary-value", "").strip()
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            observed_datetime = _parse_local_datetime(observed_at)
            source_day = (
                date_type.fromisoformat(source_date)
                if _valid_date(source_date)
                else None
            )
            valid_observed_at = (
                observed_datetime is not None
                and observed_datetime.date() <= as_of
            )
            valid_source_date = (
                date_kind == "source-date"
                and source_day is not None
                and source_day <= as_of
                and observed_datetime is not None
                and source_day <= observed_datetime.date()
            )
            valid_fetch_only = date_kind == "fetch-only" and not source_date
            valid_status_date = (
                status == "ok"
                and valid_source_date
                and source_day == as_of
            ) or (
                status == "latest_available"
                and (valid_source_date or valid_fetch_only)
            )
            if (
                asset_class not in CROSS_ASSET_CLASSES
                or not instrument
                or not source
                or instrument in seen_instruments
                or status not in CROSS_ASSET_ROW_STATUSES
                or not valid_observed_at
                or not (valid_source_date or valid_fetch_only)
                or not valid_status_date
                or not re.fullmatch(
                    r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|bp)?",
                    primary_value,
                )
                or re.sub(r"\s+", "", instrument) not in visible
                or "[事实]" not in visible
                or primary_value not in visible
                or (valid_source_date and source_date not in visible)
                or (valid_fetch_only and "源交易日缺失" not in visible)
            ):
                raise ReportValidationError(
                    "invalid_cross_asset_context",
                    "大类资产行必须带唯一品种、受控资产类、可见主数值、来源日或 fetch-only 语义及严格时间边界",
                    section="s1",
                )
            seen_instruments.add(instrument)
            seen_classes.add(asset_class)
            seen_fetch_only = seen_fetch_only or valid_fetch_only

        if (
            source_status == "complete"
            and (
                seen_classes != CROSS_ASSET_CLASSES
                or seen_fetch_only
            )
        ) or (
            source_status == "partial"
            and (
                len(seen_classes) < 3
                or (
                    seen_classes == CROSS_ASSET_CLASSES
                    and not seen_fetch_only
                )
            )
        ):
            raise ReportValidationError(
                "invalid_cross_asset_context",
                "大类资产 complete 必须覆盖五类且来源日完整；partial 至少保留三类并真实存在类别或来源日缺口",
                section="s1",
            )

    def _validate_rmb_fx_observation(
        self,
        contract: _StructuredContract,
        report_date: date_type,
    ) -> None:
        if (
            contract.section != "s1"
            or not self._valid_contract_as_of(contract, report_date)
        ):
            raise ReportValidationError(
                "invalid_rmb_fx_observation",
                "人民币即期与外汇掉期证据必须归属 s1 并带有效观察日",
                section="s1",
            )

        source_status = contract.attrs.get("data-source-status", "")
        compact_text = re.sub(
            r"\s+", "", "".join(contract.rendered_text)
        ).rstrip("。.;；")
        ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or source_status not in {"partial", "failed"}
                or compact_text != RMB_FX_MISSING_TEXT
                or "人民币即期与1YC-Swap数据不完整" not in ops_text
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_observation",
                    "外汇掉期缺失态必须显式标记 partial/failed 并在 ops 保持可见",
                    section="s1",
                )
            return

        if (
            contract.value != "v1"
            or contract.tag != "table"
            or source_status not in RMB_FX_SOURCE_STATUSES
            or len(contract.rows) != 3
            or not compact_text
        ):
            raise ReportValidationError(
                "invalid_rmb_fx_observation",
                "人民币外汇 v1 表必须固定包含表头、USD/CNY 即期和 1Y C-Swap 两行",
                section="s1",
            )
        if (
            source_status == "partial"
            and "人民币即期与1YC-Swap数据不完整" not in ops_text
        ):
            raise ReportValidationError(
                "invalid_rmb_fx_observation",
                "人民币外汇 partial 状态必须在 ops 披露数据不完整",
                section="s1",
            )

        rows = {
            row.attrs.get("data-fx-instrument", "").strip(): row
            for row in contract.rows[1:]
        }
        if set(rows) != {"spot", "c-swap-1y"}:
            raise ReportValidationError(
                "invalid_rmb_fx_observation",
                "人民币外汇表必须且只能包含 spot 与 c-swap-1y",
                section="s1",
            )

        as_of = date_type.fromisoformat(contract.attrs["data-as-of"])
        expected_sources = {
            "spot": (
                "chinamoney:rfx-sp-quot",
                "www.chinamoney.com.cn",
                "rfx-sp-quot.json",
            ),
            "c-swap-1y": (
                "chinamoney:fx-c-swap-fixing",
                "www.chinamoney.org.cn",
                "fx-c-sw-curv-USD.CNY.json",
            ),
        }
        missing_instruments: set[str] = set()
        for instrument, row in rows.items():
            attrs = row.attrs
            row_status = attrs.get("data-status", "")
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            if row_status == "missing":
                prohibited_keys = (
                    "data-source",
                    "data-source-url",
                    "data-source-date",
                    "data-observed-at",
                    "data-fetched-at",
                    "data-bid",
                    "data-ask",
                    "data-mid",
                    "data-swap-point-pips",
                    "data-forward-rate",
                    "data-quote-source",
                )
                valid_missing_identity = (
                    "USD/CNY" in visible
                    and "[事实]" in visible
                    and "数据缺失" in visible
                    and (
                        instrument == "spot"
                        and "即期" in visible
                        and not attrs.get("data-tenor")
                        or instrument == "c-swap-1y"
                        and "C-Swap" in visible
                        and attrs.get("data-tenor") == "1Y"
                    )
                )
                if (
                    source_status != "partial"
                    or attrs.get("data-pair") != "USD/CNY"
                    or attrs.get("data-price-kind") != "missing"
                    or not valid_missing_identity
                    or any(attrs.get(key, "").strip() for key in prohibited_keys)
                ):
                    raise ReportValidationError(
                        "invalid_rmb_fx_observation",
                        "人民币外汇 partial 缺失腿必须显式可见且不得伪造来源、日期或数值",
                        section="s1",
                    )
                missing_instruments.add(instrument)
                continue

            source_date = attrs.get("data-source-date", "")
            observed_at = attrs.get("data-observed-at", "")
            fetched_at = attrs.get("data-fetched-at", "")
            source, expected_host, url_suffix = expected_sources[instrument]
            source_url = urlparse(attrs.get("data-source-url", ""))
            observed_datetime = _parse_local_datetime(observed_at)
            fetched_datetime = _parse_local_datetime(fetched_at)
            source_day = (
                date_type.fromisoformat(source_date)
                if _valid_date(source_date)
                else None
            )
            if (
                attrs.get("data-pair") != "USD/CNY"
                or row_status not in RMB_FX_AVAILABLE_ROW_STATUSES
                or attrs.get("data-source") != source
                or source_url.scheme != "https"
                or source_url.hostname != expected_host
                or not source_url.path.endswith(url_suffix)
                or source_day is None
                or source_day > as_of
                or observed_datetime is None
                or observed_datetime.date() != source_day
                or fetched_datetime is None
                or fetched_datetime.date() != source_day
                or fetched_datetime < observed_datetime
                or (
                    row_status == "ok"
                    and source_day != as_of
                )
                or source_date not in visible
                or "中国货币网" not in visible
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_observation",
                    "人民币即期/掉期行的品种、来源、日期或观察时间不合法",
                    section="s1",
                )

        if (
            source_status == "complete"
            and missing_instruments
        ) or (
            source_status == "partial"
            and len(missing_instruments) != 1
        ):
            raise ReportValidationError(
                "invalid_rmb_fx_observation",
                "人民币外汇 complete 必须两腿完整；partial 必须保留一腿有效事实并显式标记另一腿缺失",
                section="s1",
            )

        if "spot" not in missing_instruments:
            spot_attrs = rows["spot"].attrs
            try:
                bid = float(spot_attrs.get("data-bid", ""))
                ask = float(spot_attrs.get("data-ask", ""))
                mid = float(spot_attrs.get("data-mid", ""))
            except ValueError:
                bid = ask = mid = math.nan
            spot_visible = re.sub(
                r"\s+", "", "".join(rows["spot"].rendered_text)
            )
            if (
                spot_attrs.get("data-price-kind") != "computed_bid_ask_mid"
                or not all(
                    math.isfinite(value) and value > 0
                    for value in (bid, ask, mid)
                )
                or bid > ask
                or not bid <= mid <= ask
                or not math.isclose(mid, (bid + ask) / 2, abs_tol=1e-8)
                or "USD/CNY" not in spot_visible
                or "即期" not in spot_visible
                or any(
                    term not in spot_visible
                    for term in ("买", "卖", "算术中值")
                )
                or any(
                    spot_attrs[key] not in spot_visible
                    for key in ("data-bid", "data-ask", "data-mid")
                )
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_observation",
                    "USD/CNY 即期必须使用有效买卖报价的算术中值并在表内可见",
                    section="s1",
                )

        if "c-swap-1y" not in missing_instruments:
            swap_attrs = rows["c-swap-1y"].attrs
            try:
                swap_points = float(
                    swap_attrs.get("data-swap-point-pips", "")
                )
                forward_rate = float(
                    swap_attrs.get("data-forward-rate", "")
                )
            except ValueError:
                swap_points = forward_rate = math.nan
            swap_visible = re.sub(
                r"\s+", "", "".join(rows["c-swap-1y"].rendered_text)
            )
            if (
                swap_attrs.get("data-price-kind") != "c_swap_fixing"
                or swap_attrs.get("data-tenor") != "1Y"
                or swap_attrs.get("data-quote-source") != "报价数据"
                or not math.isfinite(swap_points)
                or not math.isfinite(forward_rate)
                or forward_rate <= 0
                or "C-Swap" not in swap_visible
                or "Pips" not in swap_visible
                or "定盘" not in swap_visible
                or "报价数据" not in swap_visible
                or swap_attrs.get("data-swap-point-pips", "")
                not in swap_visible
                or swap_attrs.get("data-forward-rate", "")
                not in swap_visible
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_observation",
                    "1Y C-Swap 必须保留有效掉期点、全价、定盘语义和可见单位",
                    section="s1",
                )

    def _validate_rmb_fx_chart_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-rmb-fx-chart"]
        if len(contracts) != 1:
            raise ReportValidationError(
                "invalid_rmb_fx_chart",
                "s1 必须且只能包含一份人民币即期与外汇掉期趋势图状态",
                section="s1",
            )
        contract = contracts[0]
        compact_text = re.sub(
            r"\s+", "", "".join(contract.rendered_text)
        ).rstrip("。.;；")
        if contract.section != "s1":
            raise ReportValidationError(
                "invalid_rmb_fx_chart", "人民币外汇趋势图只能位于 s1", section="s1"
            )
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or contract.attrs.get("data-as-of") != report_date.isoformat()
                or contract.attrs.get("data-source-status") != "insufficient-history"
                or compact_text != RMB_FX_CHART_MISSING_TEXT
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_chart",
                    "趋势图历史不足态必须显式说明少于 8 个同日工作日数据",
                    section="s1",
                )
            return
        point_count = _bounded_int_attr(
            contract.attrs,
            "data-point-count",
            minimum=RMB_FX_CHART_MIN_POINTS,
            maximum=RMB_FX_CHART_MAX_POINTS,
        )
        start_date = contract.attrs.get("data-start-date", "")
        end_date = contract.attrs.get("data-end-date", "")
        if (
            contract.value != "v1"
            or contract.tag != "figure"
            or contract.default_hidden
            or contract.attrs.get("data-source-status") not in {"complete", "partial"}
            or contract.attrs.get("data-source") != "chinamoney:validated-review-archive"
            or contract.attrs.get("data-reviewed-through") != report_date.isoformat()
            or contract.attrs.get("data-as-of") != end_date
            or not _valid_date(start_date)
            or not _valid_date(end_date)
            or not start_date < end_date <= report_date.isoformat()
            or point_count is None
            or len(contract.rows) != point_count + 1
            or "[事实]" not in compact_text
            or "即期买卖算术中值" not in compact_text
            or "1YC-Swap全价" not in compact_text
            or "掉期点" not in compact_text
        ):
            raise ReportValidationError(
                "invalid_rmb_fx_chart",
                "完整趋势图必须可见、含 8-15 个同日点、三条序列、来源与价格语义",
                section="s1",
            )
        seen_dates: list[str] = []
        for row in contract.rows[1:]:
            attrs = row.attrs
            source_date = attrs.get("data-source-date", "")
            spot = _finite_float(attrs.get("data-spot-mid"))
            forward = _finite_float(attrs.get("data-forward-rate"))
            swap = _finite_float(attrs.get("data-swap-point-pips"))
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            if (
                not _valid_date(source_date)
                or source_date in seen_dates
                or spot is None
                or forward is None
                or swap is None
                or not 5 < spot < 9
                or not 5 < forward < 9
                or abs(swap) > 10_000
                or source_date not in visible
            ):
                raise ReportValidationError(
                    "invalid_rmb_fx_chart",
                    "趋势图数据行必须使用唯一来源日和有效的即期、全价、掉期点",
                    section="s1",
                )
            seen_dates.append(source_date)
        if seen_dates != sorted(seen_dates) or seen_dates[0] != start_date or seen_dates[-1] != end_date:
            raise ReportValidationError(
                "invalid_rmb_fx_chart",
                "趋势图日期必须严格升序并与起止元数据一致",
                section="s1",
            )

    def _validate_emotion_leader_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-emotion-leader"]
        if len(contracts) != 1:
            raise ReportValidationError(
                "invalid_emotion_leader",
                "s3 必须且只能包含一份情绪核心生命周期模块",
                section="s3",
            )
        contract = contracts[0]
        compact_text = re.sub(
            r"\s+", "", "".join(contract.rendered_text)
        ).rstrip("。.;；")
        if (
            contract.section != "s3"
            or contract.attrs.get("data-as-of") != report_date.isoformat()
        ):
            raise ReportValidationError(
                "invalid_emotion_leader",
                "情绪核心生命周期模块必须位于 s3 且与报告日同日",
                section="s3",
            )
        source_status = contract.attrs.get("data-source-status", "")
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or source_status != "failed"
                or compact_text != EMOTION_LEADER_MISSING_TEXT
            ):
                raise ReportValidationError(
                    "invalid_emotion_leader",
                    "情绪核心缺失态必须显式标记 failed，不能伪装为空池",
                    section="s3",
                )
            return
        if contract.value == "none":
            if (
                contract.tag != "p"
                or source_status != "ok"
                or compact_text != EMOTION_LEADER_NONE_TEXT
            ):
                raise ReportValidationError(
                    "invalid_emotion_leader",
                    "情绪核心空池仅允许由 ok 状态给出",
                    section="s3",
                )
            return

        int_keys = (
            "data-active-count",
            "data-archived-count",
            "data-today-limit-up-count",
            "data-new-peak-count",
            "data-promoted-count",
            "data-candidate-count",
            "data-displayed-count",
            "data-error-count",
            "data-coverage-loaded",
            "data-coverage-expected",
            "data-refreshed-count",
        )
        counts = {
            key: _bounded_int_attr(contract.attrs, key, minimum=0, maximum=100_000)
            for key in int_keys
        }
        displayed = counts["data-displayed-count"]
        if (
            contract.value != "v1"
            or contract.tag != "div"
            or contract.default_hidden
            or source_status not in {"ok", "partial"}
            or any(value is None for value in counts.values())
            or displayed is None
            or not 1 <= displayed <= EMOTION_LEADER_MAX_ROWS
            or counts["data-active-count"] < displayed
            or counts["data-coverage-loaded"] > counts["data-coverage-expected"]
            or len(contract.rows) != displayed + 1
            or not contract.attrs.get("data-refresh-mode")
            or f"状态{source_status}" not in compact_text
            or f"活跃{counts['data-active-count']}" not in compact_text
            or f"归档{counts['data-archived-count']}" not in compact_text
            or "今日晋级核心" not in compact_text
            or "新增二连板候选" not in compact_text
        ):
            raise ReportValidationError(
                "invalid_emotion_leader",
                "情绪核心完整态必须包含可见状态、覆盖、刷新、计数与前 12 只明细",
                section="s3",
            )
        seen_codes: set[str] = set()
        for row in contract.rows[1:]:
            code = row.attrs.get("data-code", "").upper()
            wave = row.attrs.get("data-wave-label", "")
            metric_status = row.attrs.get("data-metric-status", "")
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            if (
                not EMOTION_LEADER_CODE_RE.fullmatch(code)
                or code in seen_codes
                or wave not in EMOTION_LEADER_WAVE_LABELS
                or metric_status not in {"ok", "source_failed"}
                or code not in visible
                or f"[判断]{wave}" not in visible
            ):
                raise ReportValidationError(
                    "invalid_emotion_leader",
                    "情绪核心行必须带唯一代码、波段判断标签和指标状态",
                    section="s3",
                )
            seen_codes.add(code)

    def _validate_emotion_height_chart_contract(
        self, report_date: date_type
    ) -> None:
        contracts = self.structured_contracts["data-emotion-height-chart"]
        if len(contracts) != 1:
            raise ReportValidationError(
                "invalid_emotion_height_chart",
                "s3 必须且只能包含一份最近连板高度趋势图状态",
                section="s3",
            )
        contract = contracts[0]
        compact_text = re.sub(r"\s+", "", "".join(contract.rendered_text)).rstrip(
            "。.;；"
        )
        if contract.section != "s3" or contract.default_hidden:
            raise ReportValidationError(
                "invalid_emotion_height_chart",
                "连板高度趋势图必须默认可见且只能位于 s3",
                section="s3",
            )
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or contract.attrs.get("data-as-of") != report_date.isoformat()
                or contract.attrs.get("data-source-status")
                != "insufficient-history"
                or compact_text != EMOTION_HEIGHT_CHART_MISSING_TEXT
            ):
                raise ReportValidationError(
                    "invalid_emotion_height_chart",
                    "连板高度历史不足态必须显式说明少于 2 个有效交易日",
                    section="s3",
                )
            return

        point_count = _bounded_int_attr(
            contract.attrs,
            "data-point-count",
            minimum=EMOTION_HEIGHT_CHART_MIN_POINTS,
            maximum=EMOTION_HEIGHT_CHART_MAX_SAMPLES,
        )
        sample_count = _bounded_int_attr(
            contract.attrs,
            "data-sample-count",
            minimum=EMOTION_HEIGHT_CHART_MIN_POINTS,
            maximum=EMOTION_HEIGHT_CHART_MAX_SAMPLES,
        )
        lookback = _bounded_int_attr(
            contract.attrs,
            "data-lookback-open-days",
            minimum=EMOTION_HEIGHT_CHART_MAX_SAMPLES,
            maximum=EMOTION_HEIGHT_CHART_MAX_SAMPLES,
        )
        start_date = contract.attrs.get("data-start-date", "")
        end_date = contract.attrs.get("data-end-date", "")
        as_of = contract.attrs.get("data-as-of", "")
        source_status = contract.attrs.get("data-source-status", "")
        if (
            contract.value != "v1"
            or contract.tag != "figure"
            or source_status not in {"complete", "partial"}
            or contract.attrs.get("data-source")
            != "emotion-leader:daily-json-archive"
            or contract.attrs.get("data-reviewed-through")
            != report_date.isoformat()
            or point_count is None
            or sample_count is None
            or point_count > sample_count
            or lookback != EMOTION_HEIGHT_CHART_MAX_SAMPLES
            or len(contract.rows) != sample_count + 1
            or not _valid_date(start_date)
            or not _valid_date(end_date)
            or not _valid_date(as_of)
            or not start_date < end_date <= report_date.isoformat()
            or not start_date <= as_of <= end_date
            or "[事实]" not in compact_text
            or "非ST" not in compact_text
            or "最高连板" not in compact_text
            or "缺失数据不按0补齐" not in compact_text
        ):
            raise ReportValidationError(
                "invalid_emotion_height_chart",
                "完整连板高度趋势图必须可见、含 2-20 个有效点及缺失语义",
                section="s3",
            )

        seen_dates: list[str] = []
        valid_dates: list[str] = []
        missing_count = 0
        for row in contract.rows[1:]:
            source_date = row.attrs.get("data-source-date", "")
            row_status = row.attrs.get("data-point-status", "")
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            height = _bounded_int_attr(
                row.attrs, "data-height", minimum=0, maximum=100
            )
            if (
                not _valid_date(source_date)
                or source_date in seen_dates
                or source_date not in visible
                or row_status not in {"ok", "missing"}
            ):
                raise ReportValidationError(
                    "invalid_emotion_height_chart",
                    "连板高度明细必须按唯一交易日标记 ok/missing",
                    section="s3",
                )
            if row_status == "ok":
                if height is None or f"{height}板" not in visible:
                    raise ReportValidationError(
                        "invalid_emotion_height_chart",
                        "有效连板高度必须是 0-100 的可见板数",
                        section="s3",
                    )
                valid_dates.append(source_date)
            else:
                if row.attrs.get("data-height", "") or "—（缺失）" not in visible:
                    raise ReportValidationError(
                        "invalid_emotion_height_chart",
                        "缺失日期必须保留空高度和可见缺失标记",
                        section="s3",
                    )
                missing_count += 1
            seen_dates.append(source_date)
        if (
            seen_dates != sorted(seen_dates)
            or seen_dates[0] != start_date
            or seen_dates[-1] != end_date
            or len(valid_dates) != point_count
            or valid_dates[-1] != as_of
            or (source_status == "complete" and missing_count)
            or (
                source_status == "partial"
                and not missing_count
                and end_date == report_date.isoformat()
            )
        ):
            raise ReportValidationError(
                "invalid_emotion_height_chart",
                "连板高度日期、点数、截至日与 complete/partial 状态必须一致",
                section="s3",
            )

    def _validate_emotion_node_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-emotion-node"]
        if len(contracts) != 1:
            raise ReportValidationError(
                "invalid_emotion_node",
                "s6 必须且只能包含一份情绪高度节点联动",
                section="s6",
            )
        contract = contracts[0]
        compact_text = re.sub(r"\s+", "", "".join(contract.rendered_text)).rstrip(
            "。.;；"
        )
        if (
            contract.section != "s6"
            or contract.attrs.get("data-as-of") != report_date.isoformat()
            or contract.default_hidden
        ):
            raise ReportValidationError(
                "invalid_emotion_node",
                "情绪高度节点联动必须默认可见、位于 s6 且与报告日同日",
                section="s6",
            )
        source_status = contract.attrs.get("data-source-status", "")
        if contract.value == "missing-data":
            if (
                contract.tag != "p"
                or source_status not in {"partial", "failed"}
                or compact_text != EMOTION_NODE_MISSING_TEXT
            ):
                raise ReportValidationError(
                    "invalid_emotion_node",
                    "情绪高度缺失态必须显式标记 partial/failed",
                    section="s6",
                )
            return

        lookback = _bounded_int_attr(
            contract.attrs,
            "data-lookback-open-days",
            minimum=EMOTION_NODE_LOOKBACK_OPEN_DAYS,
            maximum=EMOTION_NODE_LOOKBACK_OPEN_DAYS,
        )
        current_height = _bounded_int_attr(
            contract.attrs, "data-current-max-height", minimum=0, maximum=100
        )
        previous_height = _bounded_int_attr(
            contract.attrs, "data-previous-max-height", minimum=0, maximum=100
        )
        if (
            source_status != "complete"
            or lookback != EMOTION_NODE_LOOKBACK_OPEN_DAYS
            or current_height is None
            or previous_height is None
        ):
            raise ReportValidationError(
                "invalid_emotion_node",
                "情绪高度完整态必须带近20个开放日及双边最高板数",
                section="s6",
            )
        if contract.value == "none":
            if (
                contract.tag != "p"
                or current_height > previous_height
                or compact_text != EMOTION_NODE_NONE_TEXT
            ):
                raise ReportValidationError(
                    "invalid_emotion_node",
                    "未打开高度时必须使用规范 none 事实句",
                    section="s6",
                )
            return

        leader_count = _bounded_int_attr(
            contract.attrs, "data-leader-count", minimum=1, maximum=20
        )
        window_start = contract.attrs.get("data-window-start", "")
        window_end = contract.attrs.get("data-window-end", "")
        if (
            contract.value != "v1"
            or contract.tag != "div"
            or current_height < 2
            or current_height <= previous_height
            or leader_count is None
            or len(contract.rows) != leader_count + 1
            or not _valid_date(window_start)
            or not _valid_date(window_end)
            or not window_start <= window_end < report_date.isoformat()
            or "[事实]" not in compact_text
            or "[判断]" not in compact_text
            or "打开非ST连板高度" not in compact_text
            or "启动日" not in compact_text
            or "情绪节点日候选" not in compact_text
        ):
            raise ReportValidationError(
                "invalid_emotion_node",
                "打开高度时必须展示客观高度对比和带[判断]的启动日节点候选",
                section="s6",
            )
        seen_codes: set[str] = set()
        for row in contract.rows[1:]:
            attrs = row.attrs
            code = attrs.get("data-code", "").upper()
            launch_date = attrs.get("data-launch-date", "")
            launch_method = attrs.get("data-launch-method", "")
            height = _bounded_int_attr(
                attrs, "data-current-height", minimum=2, maximum=100
            )
            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            if (
                not EMOTION_LEADER_CODE_RE.fullmatch(code)
                or code in seen_codes
                or not _valid_date(launch_date)
                or launch_date > report_date.isoformat()
                or launch_method not in EMOTION_NODE_LAUNCH_METHODS
                or height != current_height
                or code not in visible
                or launch_date not in visible
            ):
                raise ReportValidationError(
                    "invalid_emotion_node",
                    "情绪高度节点明细必须带唯一股票、可核对启动日与当日高度",
                    section="s6",
                )
            seen_codes.add(code)

    def _validate_sector_labels_contract(self, report_date: date_type) -> None:
        contracts = self.structured_contracts["data-sector-labels"]
        verdicts = [item for item in contracts if item.value == "verdict"]
        data_blocks = [item for item in contracts if item.value != "verdict"]
        if len(verdicts) != 1 or len(data_blocks) != 1:
            raise ReportValidationError(
                "invalid_sector_labels",
                "s2 必须且只能包含一句板块趋势标签摘要和一份标签数据块",
                section="s2",
            )

        verdict = verdicts[0]
        block = data_blocks[0]
        verdict_text = re.sub(r"\s+", "", "".join(verdict.rendered_text))
        if (
            verdict.tag != "p"
            or verdict.section != "s2"
            or verdict.default_hidden
            or not self._valid_contract_as_of(verdict, report_date)
            or "[事实]" not in verdict_text
            or "[判断]" not in verdict_text
        ):
            raise ReportValidationError(
                "invalid_sector_labels",
                "板块趋势标签摘要必须是 s2 默认可见、来源日有效且区分事实/判断的 p",
                section="s2",
            )
        if (
            block.section != "s2"
            or not self._valid_contract_as_of(block, report_date)
            or block.attrs.get("data-as-of") != verdict.attrs.get("data-as-of")
            or not block.default_hidden
            or any(
                block.attrs.get(key) != value
                for key, value in SECTOR_LABELS_WINDOW_ATTRS.items()
            )
        ):
            raise ReportValidationError(
                "invalid_sector_labels",
                "板块趋势标签必须归属 s2 折叠证据、与摘要同日并固定使用 MA144/MA233 与 10/20 共振窗口",
                section="s2",
            )

        source_status = block.attrs.get("data-source-status", "")
        compact_text = re.sub(
            r"\s+", "", "".join(block.rendered_text)
        ).rstrip("。.;；")
        ops_text = re.sub(r"\s+", "", "".join(self.section_text["ops"]))
        if block.value == "missing-data":
            if (
                block.tag != "p"
                or source_status not in {"partial", "failed"}
                or compact_text != SECTOR_LABELS_MISSING_TEXT
                or "板块趋势标签数据不完整" not in ops_text
                or not SECTOR_LABELS_MISSING_VERDICT_RE.fullmatch(verdict_text)
            ):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "标签缺失态必须显式标记 partial/failed，并在摘要和 ops 披露无法判定",
                    section="s2",
                )
            return

        count_keys = (
            "data-total-l2",
            "data-missing-l2-count",
            "data-half-year-count",
            "data-year-count",
            "data-resonance-count",
            "data-year-resonance-count",
            "data-half-year-insufficient-count",
            "data-year-insufficient-count",
            "data-resonance-insufficient-count",
        )
        count_values = {
            key: block.attrs.get(key, "").strip() for key in count_keys
        }
        if any(not value.isdigit() for value in count_values.values()):
            raise ReportValidationError(
                "invalid_sector_labels",
                "板块趋势标签汇总字段必须是非负整数",
                section="s2",
            )
        counts = {key: int(value) for key, value in count_values.items()}
        total_l2 = counts["data-total-l2"]
        missing_l2 = counts["data-missing-l2-count"]
        half_count = counts["data-half-year-count"]
        year_count = counts["data-year-count"]
        resonance_count = counts["data-resonance-count"]
        year_resonance_count = counts["data-year-resonance-count"]
        half_insufficient = counts["data-half-year-insufficient-count"]
        year_insufficient = counts["data-year-insufficient-count"]
        resonance_insufficient = counts["data-resonance-insufficient-count"]
        insufficiencies = (
            half_insufficient,
            year_insufficient,
            resonance_insufficient,
        )
        has_gap = missing_l2 > 0 or any(value > 0 for value in insufficiencies)
        verdict_match = SECTOR_LABELS_VERDICT_RE.fullmatch(verdict_text)
        coverage_disclosure = (
            verdict_match.group("coverage") if verdict_match else None
        )
        count_shape_valid = (
            total_l2 > 0
            and missing_l2 <= total_l2
            and all(missing_l2 <= value <= total_l2 for value in insufficiencies)
            and half_count <= total_l2 - half_insufficient
            and year_count <= total_l2 - year_insufficient
            and resonance_count <= total_l2 - resonance_insufficient
            and year_resonance_count <= min(year_count, resonance_count)
        )
        status_valid = (
            source_status == "complete"
            and not has_gap
            and not coverage_disclosure
        ) or (
            source_status == "partial"
            and has_gap
            and bool(coverage_disclosure)
            and "板块趋势标签数据不完整" in ops_text
        )
        if not count_shape_valid or not status_valid:
            raise ReportValidationError(
                "invalid_sector_labels",
                "标签覆盖数、数据不足数或 complete/partial 状态不一致",
                section="s2",
            )

        visible_counts = (
            tuple(
                int(verdict_match.group(name))
                for name in ("half", "year", "resonance", "year_resonance")
            )
            if verdict_match
            else ()
        )
        if visible_counts != (
            half_count,
            year_count,
            resonance_count,
            year_resonance_count,
        ):
            raise ReportValidationError(
                "invalid_sector_labels",
                "板块趋势标签摘要必须按固定事实/判断句式精确展示表内四项计数",
                section="s2",
            )

        if block.value == "none":
            if (
                block.tag != "p"
                or source_status != "complete"
                or compact_text != SECTOR_LABELS_NONE_TEXT
                or any(
                    (
                        half_count,
                        year_count,
                        resonance_count,
                        year_resonance_count,
                    )
                )
            ):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "标签 none 态仅允许完整覆盖且四项命中均为零",
                    section="s2",
                )
            return

        if (
            block.value != "v1"
            or block.tag != "table"
            or len(block.rows) < 2
            or not compact_text
        ):
            raise ReportValidationError(
                "invalid_sector_labels",
                "标签数据块仅允许非空 v1 表、完整 none 或显式 missing-data",
                section="s2",
            )

        seen_codes: set[str] = set()
        derived_half = 0
        derived_year = 0
        derived_resonance = 0
        derived_year_resonance = 0
        as_of = date_type.fromisoformat(block.attrs["data-as-of"])
        for row in block.rows[1:]:
            code = row.attrs.get("data-code", "").strip().upper()
            half_value = row.attrs.get("data-above-half-year-ma", "")
            year_value = row.attrs.get("data-above-year-ma", "")
            resonance_value = row.attrs.get("data-recent-resonance", "")
            if (
                not SECTOR_LABELS_CODE_RE.fullmatch(code)
                or code in seen_codes
                or any(
                    value not in {"true", "false"}
                    for value in (half_value, year_value, resonance_value)
                )
            ):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "标签命中行必须带唯一申万代码和明确 true/false 三标签",
                    section="s2",
                )
            seen_codes.add(code)
            half = half_value == "true"
            year = year_value == "true"
            resonance = resonance_value == "true"
            if not any((half, year, resonance)):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "v1 表只承载命中并集，每行至少命中一个标签",
                    section="s2",
                )

            last_resonance = row.attrs.get(
                "data-last-resonance-date", ""
            ).strip()
            valid_resonance_date = (
                bool(last_resonance)
                and _valid_date(last_resonance)
                and date_type.fromisoformat(last_resonance) <= as_of
            )
            if (resonance and not valid_resonance_date) or (
                not resonance and last_resonance
            ):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "近期共振命中必须带不晚于来源日的最近事件日期，未命中不得伪造日期",
                    section="s2",
                )

            visible = re.sub(r"\s+", "", "".join(row.rendered_text))
            tokens = visible.split("/")
            visible_half = any(
                "半年线上[事实]" in token for token in tokens
            )
            visible_year = any(
                "年线上[事实]" in token and "半年线上[事实]" not in token
                for token in tokens
            )
            visible_resonance = any(
                "最近共振" in token and "[判断]" in token for token in tokens
            )
            if (
                code not in visible
                or visible_half != half
                or visible_year != year
                or visible_resonance != resonance
                or (resonance and last_resonance not in visible)
            ):
                raise ReportValidationError(
                    "invalid_sector_labels",
                    "标签行可见代码、事实标签、判断标签与结构化元数据必须一致",
                    section="s2",
                )

            derived_half += int(half)
            derived_year += int(year)
            derived_resonance += int(resonance)
            derived_year_resonance += int(year and resonance)

        if (
            len(seen_codes) > total_l2 - missing_l2
            or (
                derived_half,
                derived_year,
                derived_resonance,
                derived_year_resonance,
            )
            != (
                half_count,
                year_count,
                resonance_count,
                year_resonance_count,
            )
        ):
            raise ReportValidationError(
                "invalid_sector_labels",
                "标签命中并集、四项汇总计数与逐行布尔值必须完全对账",
                section="s2",
            )

    def _validate_sector_contracts(self, report_date: date_type) -> None:
        self._validate_sector_labels_contract(report_date)
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
        schema, schema_tag, schema_host, report_date_value, report_layout = (
            self.schema_hosts[0]
        )
        if (
            schema != REPORT_SCHEMA
            or schema_tag != "article"
            or schema_host != "report-document"
            or not _valid_date(report_date_value)
            or report_layout not in {"", CURRENT_REPORT_LAYOUT}
        ):
            raise ReportValidationError(
                "invalid_schema",
                "article#report-document 必须带 compact-v2 schema、有效 data-report-date 和受控 layout",
            )
        report_date = date_type.fromisoformat(report_date_value)

        current_layout = report_layout == CURRENT_REPORT_LAYOUT
        expected_chunks = CURRENT_CHUNK_ORDER if current_layout else CHUNK_ORDER
        expected_anchor_map = CURRENT_ANCHOR_MAP if current_layout else ANCHOR_MAP
        expected_anchors = (
            CURRENT_REQUIRED_ANCHORS if current_layout else REQUIRED_ANCHORS
        )

        if tuple(self.chunks) != expected_chunks:
            raise ReportValidationError(
                "invalid_chunks",
                f"chunk 必须按顺序且各出现一次：{', '.join(expected_chunks)}；实际：{', '.join(self.chunks)}",
            )

        for anchor in expected_anchors:
            count = self.ids.get(anchor, 0)
            if count != 1:
                raise ReportValidationError(
                    "invalid_anchor",
                    f"锚点 {anchor} 必须出现一次，实际 {count}",
                    section=anchor,
                )
            expected_chunk = next(
                chunk
                for chunk, anchors in expected_anchor_map.items()
                if anchor in anchors
            )
            actual_chunks = self.anchor_chunks[anchor]
            if actual_chunks != [expected_chunk]:
                raise ReportValidationError(
                    "anchor_chunk_mismatch",
                    f"锚点 {anchor} 必须归属 chunk {expected_chunk}，实际 {actual_chunks}",
                    section=anchor,
                )

        if tuple(self.anchors_seen) != expected_anchors:
            raise ReportValidationError(
                "invalid_anchor_order",
                "章节锚点必须按固定顺序出现：" + ", ".join(expected_anchors),
            )

        self._validate_factor_contract()
        if not current_layout:
            self._validate_exposure_contract(report_date)
        self._validate_big_picture_contract(report_date)
        self._validate_rmb_fx_chart_contract(report_date)
        self._validate_emotion_leader_contract(report_date)
        self._validate_emotion_height_chart_contract(report_date)
        self._validate_emotion_node_contract(report_date)
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
            if evidence.section not in expected_anchors:
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
            if claim.section not in expected_anchors:
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


def _requires_exposure_validation_context(parser: _ReportParser) -> bool:
    cautious_fallback = (
        parser.exposure_modes == ["fallback"]
        and parser.exposure_tiers == ["cautious"]
    )
    unreconciled_portfolio = any(
        evidence.attrs.get("data-exposure-evidence") == "portfolio"
        and evidence.attrs.get("data-portfolio-evidence-status")
        == "unreconciled"
        for evidence in parser.exposure_evidence_sources
    )
    return cautious_fallback or unreconciled_portfolio


def load_exposure_validation_context(
    db_path: str | os.PathLike[str],
    report_date: str,
) -> ExposureValidationContext:
    """从 canonical SQLite 事实层只读加载市场、日历与组合对账事实。"""

    _validate_date(report_date)
    source = Path(db_path).expanduser().resolve()
    if not source.is_file():
        raise ReportValidationError(
            "invalid_exposure_context",
            f"仓位建议外部事实库不存在：{source}",
            section="exposure",
        )
    report_day = date_type.fromisoformat(report_date)
    calendar_end = report_day + timedelta(
        days=EXPOSURE_REVIEW_DATE_MAX_DAYS
        + EXPOSURE_RETRY_REVIEW_DATE_MAX_DAYS
    )
    try:
        connection = sqlite3.connect(
            f"{source.as_uri()}?mode=ro",
            uri=True,
        )
        try:
            amount_row = connection.execute(
                "SELECT total_amount FROM daily_market WHERE date = ?",
                (report_date,),
            ).fetchone()
            calendar_rows = connection.execute(
                """
                SELECT date, is_open
                FROM trade_calendar
                WHERE date BETWEEN ? AND ?
                ORDER BY date
                """,
                (report_date, calendar_end.isoformat()),
            ).fetchall()
            portfolio_row = connection.execute(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM holdings
                        WHERE status = 'active'
                    ) AS active_holdings,
                    (
                        SELECT COUNT(*)
                        FROM holdings
                        WHERE status = 'active'
                          AND thesis_id IS NULL
                    ) AS unlinked_holdings,
                    (
                        SELECT COUNT(*)
                        FROM trade_thesis
                        WHERE status = 'open'
                          AND opened_at <= ?
                    ) AS open_theses,
                    (
                        SELECT COUNT(*)
                        FROM broker_executions AS execution
                        JOIN trade_thesis AS thesis
                          ON thesis.id = execution.thesis_id
                        WHERE COALESCE(execution.is_void, 0) = 0
                          AND execution.biz_date <= ?
                          AND thesis.status = 'open'
                          AND thesis.opened_at <= ?
                    ) AS linked_executions,
                    (
                        SELECT MAX(biz_date)
                        FROM broker_executions
                        WHERE biz_date <= ?
                    ) AS latest_broker_biz_date
                """,
                (
                    report_date,
                    report_date,
                    report_date,
                    report_date,
                ),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ReportValidationError(
            "invalid_exposure_context",
            f"仓位建议外部事实读取失败：{exc}",
            section="exposure",
        ) from exc

    amount = amount_row[0] if amount_row else None
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not 1_000 <= float(amount) <= 999_999
    ):
        raise ReportValidationError(
            "invalid_exposure_context",
            "报告日 daily_market.total_amount 缺失或非法",
            section="exposure",
        )
    trade_calendar: dict[str, bool] = {}
    for raw_date, raw_is_open in calendar_rows:
        if (
            not isinstance(raw_date, str)
            or not _valid_date(raw_date)
            or raw_is_open not in (0, 1)
            or raw_date in trade_calendar
        ):
            raise ReportValidationError(
                "invalid_exposure_context",
                "trade_calendar 含非法或重复日期状态",
                section="exposure",
            )
        trade_calendar[raw_date] = bool(raw_is_open)
    if trade_calendar.get(report_date) is not True:
        raise ReportValidationError(
            "invalid_exposure_context",
            "报告日必须是 trade_calendar 中的开放日",
            section="exposure",
        )
    if portfolio_row is None:
        raise ReportValidationError(
            "invalid_exposure_context",
            "组合事实层查询未返回结果",
            section="exposure",
        )
    (
        active_holdings,
        unlinked_holdings,
        open_theses,
        linked_executions,
        latest_broker_biz_date,
    ) = portfolio_row
    portfolio_counts = (
        active_holdings,
        unlinked_holdings,
        open_theses,
        linked_executions,
    )
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in portfolio_counts
        )
        or unlinked_holdings > active_holdings
        or not isinstance(latest_broker_biz_date, str)
        or not _valid_date(latest_broker_biz_date)
        or latest_broker_biz_date > report_date
    ):
        raise ReportValidationError(
            "invalid_exposure_context",
            "组合事实层计数或券商最新业务日缺失或非法",
            section="exposure",
        )
    return ExposureValidationContext(
        report_date=report_date,
        market_turnover_yiyuan=f"{float(amount):.2f}",
        trade_calendar=trade_calendar,
        active_holdings=active_holdings,
        unlinked_holdings=unlinked_holdings,
        open_theses=open_theses,
        linked_executions=linked_executions,
        latest_broker_biz_date=latest_broker_biz_date,
    )


def _validate_exposure_context(
    parser: _ReportParser,
    report_date: str,
    context: ExposureValidationContext | None,
) -> None:
    if not _requires_exposure_validation_context(parser):
        return
    cautious_fallback = (
        parser.exposure_modes == ["fallback"]
        and parser.exposure_tiers == ["cautious"]
    )
    if (
        context is None
        or context.report_date != report_date
        or context.trade_calendar.get(report_date) is not True
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in (
                context.active_holdings,
                context.unlinked_holdings,
                context.open_theses,
                context.linked_executions,
            )
        )
        or context.unlinked_holdings > context.active_holdings
        or not isinstance(context.latest_broker_biz_date, str)
        or not _valid_date(context.latest_broker_biz_date)
        or context.latest_broker_biz_date > report_date
    ):
        raise ReportValidationError(
            "invalid_exposure_context",
            "需外部验真的 exposure 必须提供与报告日一致的只读市场、日历与组合事实",
            section="exposure",
        )

    if cautious_fallback:
        source_market = parser.exposure_sources[0].attrs
        confirm_attrs = parser.exposure_role_attrs["confirm-if"][0]
        if (
            context.market_turnover_yiyuan
            != source_market.get("data-market-turnover-yiyuan", "")
            or context.market_turnover_yiyuan
            != confirm_attrs.get("data-turnover-floor-yiyuan", "")
        ):
            raise ReportValidationError(
                "invalid_exposure_context",
                "成交额验证门必须等于报告日 daily_market.total_amount",
                section="exposure",
            )

    portfolio_items = [
        evidence
        for evidence in parser.exposure_evidence_sources
        if evidence.attrs.get("data-exposure-evidence") == "portfolio"
    ]
    portfolio_attrs = (
        portfolio_items[0].attrs if len(portfolio_items) == 1 else {}
    )
    portfolio_status = portfolio_attrs.get(
        "data-portfolio-evidence-status", ""
    )
    if cautious_fallback and portfolio_status != "unreconciled":
        raise ReportValidationError(
            "invalid_exposure_context",
            "正式 fallback+cautious 已读取组合事实，不得降级标成 not-read",
            section="exposure",
        )
    if portfolio_status == "unreconciled":
        expected_portfolio_attrs = {
            "data-active-holdings": str(context.active_holdings),
            "data-unlinked-holdings": str(context.unlinked_holdings),
            "data-open-theses": str(context.open_theses),
            "data-linked-executions": str(context.linked_executions),
            "data-latest-broker-biz-date": context.latest_broker_biz_date,
        }
        if any(
            portfolio_attrs.get(key, "") != value
            for key, value in expected_portfolio_attrs.items()
        ):
            raise ReportValidationError(
                "invalid_exposure_context",
                "组合对账留痕必须逐字段等于只读 holdings/trade_thesis/broker_executions 事实",
                section="exposure",
            )

    if cautious_fallback:
        review_attrs = parser.exposure_role_attrs["review-rule"][0]
        for span_key in ("data-calendar-span", "data-retry-calendar-span"):
            for item in review_attrs.get(span_key, "").split(","):
                match = re.fullmatch(
                    r"(\d{4}-\d{2}-\d{2}):(open|closed)",
                    item,
                )
                if not match:
                    raise ReportValidationError(
                        "invalid_exposure_context",
                        "复核交易日历声明格式非法",
                        section="exposure",
                    )
                expected_open = match.group(2) == "open"
                if (
                    context.trade_calendar.get(match.group(1))
                    is not expected_open
                ):
                    raise ReportValidationError(
                        "invalid_exposure_context",
                        "复核日与只读 trade_calendar 事实不一致",
                        section="exposure",
                    )


def collect_metrics(html: str) -> ReportMetrics:
    """解析完整 HTML，并按 compact-v2 的唯一口径返回正文/证据预算。"""

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
    exposure_context: ExposureValidationContext | None = None,
) -> ReportMetrics:
    """校验结构、Claim、折叠证据、边界声明及双层预算。"""

    parser = _parse_report(html)
    report_date = next(item[3] for item in parser.schema_hosts)
    _validate_exposure_context(parser, report_date, exposure_context)
    metrics = parser.metrics()
    if capacity_manifest is not None:
        _validate_capacity_manifest_payload(capacity_manifest, report_date)
        _validate_capacity_manifest_match(parser, capacity_manifest)
    if new_high_manifest is not None:
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


class _FxHistoryParser(HTMLParser):
    """只提取已落盘复盘中的人民币即期/掉期结构化属性。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_fx_table = False
        self.current: dict[str, object] | None = None
        self.tables: list[dict[str, object]] = []

    def handle_starttag(
        self, tag: str, attrs_list: list[tuple[str, str | None]]
    ) -> None:
        attrs = {key.lower(): (value or "") for key, value in attrs_list}
        if tag.lower() == "table" and attrs.get("data-rmb-fx-observation") == "v1":
            self.in_fx_table = True
            self.current = {"attrs": attrs, "rows": []}
            self.tables.append(self.current)
            return
        if tag.lower() == "tr" and self.in_fx_table and self.current is not None:
            rows = self.current["rows"]
            assert isinstance(rows, list)
            rows.append(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "table" and self.in_fx_table:
            self.in_fx_table = False
            self.current = None


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _extract_fx_legs(html: str, report_date: str) -> tuple[dict[str, dict[str, float]], str, str]:
    parser = _FxHistoryParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return {}, "partial", ""
    if len(parser.tables) != 1:
        return {}, "partial", ""
    table = parser.tables[0]
    attrs = table["attrs"]
    rows = table["rows"]
    assert isinstance(attrs, dict) and isinstance(rows, list)
    table_as_of = str(attrs.get("data-as-of", ""))
    if not _valid_date(table_as_of) or table_as_of > report_date:
        return {}, "partial", ""
    source_status = str(attrs.get("data-source-status", "partial"))
    legs: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        instrument = str(row.get("data-fx-instrument", ""))
        source_date = str(row.get("data-source-date", ""))
        if (
            str(row.get("data-status", "")) not in RMB_FX_AVAILABLE_ROW_STATUSES
            or not _valid_date(source_date)
            or source_date > report_date
        ):
            continue
        if instrument == "spot":
            bid = _finite_float(row.get("data-bid"))
            ask = _finite_float(row.get("data-ask"))
            mid = _finite_float(row.get("data-mid"))
            source_url = urlparse(str(row.get("data-source-url", "")))
            observed_at = _parse_local_datetime(str(row.get("data-observed-at", "")))
            fetched_at = _parse_local_datetime(str(row.get("data-fetched-at", "")))
            if (
                bid is not None
                and ask is not None
                and mid is not None
                and 5.0 < mid < 9.0
                and bid <= mid <= ask
                and math.isclose(mid, (bid + ask) / 2, abs_tol=1e-8)
                and row.get("data-source") == "chinamoney:rfx-sp-quot"
                and row.get("data-price-kind") == "computed_bid_ask_mid"
                and source_url.scheme == "https"
                and source_url.hostname == "www.chinamoney.com.cn"
                and source_url.path.endswith("rfx-sp-quot.json")
                and observed_at is not None
                and fetched_at is not None
                and observed_at.date().isoformat() == source_date
                and fetched_at.date().isoformat() == source_date
                and fetched_at >= observed_at
            ):
                legs.setdefault(source_date, {})["spot_mid"] = mid
        elif instrument == "c-swap-1y":
            forward = _finite_float(row.get("data-forward-rate"))
            swap = _finite_float(row.get("data-swap-point-pips"))
            source_url = urlparse(str(row.get("data-source-url", "")))
            observed_at = _parse_local_datetime(str(row.get("data-observed-at", "")))
            fetched_at = _parse_local_datetime(str(row.get("data-fetched-at", "")))
            if (
                forward is not None
                and swap is not None
                and 5.0 < forward < 9.0
                and abs(swap) <= 10_000
                and row.get("data-source") == "chinamoney:fx-c-swap-fixing"
                and row.get("data-tenor") == "1Y"
                and row.get("data-price-kind") == "c_swap_fixing"
                and row.get("data-quote-source") == "报价数据"
                and source_url.scheme == "https"
                and source_url.hostname == "www.chinamoney.org.cn"
                and source_url.path.endswith("fx-c-sw-curv-USD.CNY.json")
                and observed_at is not None
                and fetched_at is not None
                and observed_at.date().isoformat() == source_date
                and fetched_at.date().isoformat() == source_date
                and fetched_at >= observed_at
            ):
                target = legs.setdefault(source_date, {})
                target["forward_rate"] = forward
                target["swap_point_pips"] = swap
    return legs, source_status, table_as_of


def _load_fx_chart_points(
    current_s1_html: str,
    report_date: str,
    archive_dir: str | os.PathLike[str] | None,
) -> tuple[list[_FxChartPoint], str]:
    merged: dict[str, dict[str, float]] = {}
    if archive_dir is not None:
        root = Path(archive_dir)
        if root.is_dir():
            exact_name = re.compile(r"^复盘_(\d{4}-\d{2}-\d{2})\.html$")
            for path in sorted(root.glob("复盘_*.html")):
                match = exact_name.fullmatch(path.name)
                if not match or match.group(1) >= report_date:
                    continue
                try:
                    prior_html = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                legs, _, _ = _extract_fx_legs(prior_html, report_date)
                for source_date, values in legs.items():
                    merged.setdefault(source_date, {}).update(values)

    current_legs, current_status, current_as_of = _extract_fx_legs(
        current_s1_html, report_date
    )
    for source_date, values in current_legs.items():
        merged.setdefault(source_date, {}).update(values)

    points = [
        _FxChartPoint(
            source_date=source_date,
            spot_mid=values["spot_mid"],
            forward_rate=values["forward_rate"],
            swap_point_pips=values["swap_point_pips"],
        )
        for source_date, values in sorted(merged.items())
        if {"spot_mid", "forward_rate", "swap_point_pips"} <= values.keys()
    ][-RMB_FX_CHART_MAX_POINTS:]
    status = "complete"
    if (
        current_status != "complete"
        or not points
        or not current_as_of
        or points[-1].source_date != current_as_of
    ):
        status = "partial"
    return points, status


def _svg_points(
    values: Sequence[float], *, left: float, top: float, width: float, height: float
) -> tuple[str, list[tuple[float, float]], list[float]]:
    low, high = min(values), max(values)
    padding = max((high - low) * 0.12, max(abs(high), 1.0) * 0.002)
    low -= padding
    high += padding
    span = high - low
    coords: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = left + (width * index / max(len(values) - 1, 1))
        y = top + height - ((value - low) / span * height)
        coords.append((x, y))
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in coords), coords, [low + span * i / 4 for i in range(5)]


def _render_fx_chart(
    points: Sequence[_FxChartPoint], report_date: str, source_status: str
) -> str:
    if len(points) < RMB_FX_CHART_MIN_POINTS:
        return (
            f'<p data-rmb-fx-chart="missing-data" data-as-of="{escape(report_date)}" '
            'data-source-status="insufficient-history">'
            f"{RMB_FX_CHART_MISSING_TEXT}</p>"
        )

    left, width, panel_height = 78.0, 810.0, 190.0
    top_one, top_two = 72.0, 356.0
    # 即期与全价必须共享同一汇率坐标轴，重新映射到两组值的共同范围。
    all_rates = [point.spot_mid for point in points] + [
        point.forward_rate for point in points
    ]
    _, _, rate_ticks = _svg_points(
        all_rates, left=left, top=top_one, width=width, height=panel_height
    )
    rate_low, rate_high = rate_ticks[0], rate_ticks[-1]
    rate_span = rate_high - rate_low
    spot_coords = [
        (
            left + width * index / max(len(points) - 1, 1),
            top_one + panel_height - (point.spot_mid - rate_low) / rate_span * panel_height,
        )
        for index, point in enumerate(points)
    ]
    forward_coords = [
        (
            left + width * index / max(len(points) - 1, 1),
            top_one + panel_height - (point.forward_rate - rate_low) / rate_span * panel_height,
        )
        for index, point in enumerate(points)
    ]
    spot_path = " ".join(f"{x:.1f},{y:.1f}" for x, y in spot_coords)
    forward_path = " ".join(f"{x:.1f},{y:.1f}" for x, y in forward_coords)
    swap_path, swap_coords, swap_ticks = _svg_points(
        [point.swap_point_pips for point in points],
        left=left,
        top=top_two,
        width=width,
        height=panel_height,
    )

    rate_grid = "".join(
        f'<line class="fx-grid" x1="{left:.1f}" x2="{left + width:.1f}" y1="{top_one + panel_height - i * panel_height / 4:.1f}" y2="{top_one + panel_height - i * panel_height / 4:.1f}"/>'
        f'<text class="fx-axis-label" x="{left - 12:.1f}" y="{top_one + panel_height - i * panel_height / 4 + 4:.1f}" text-anchor="end">{tick:.3f}</text>'
        for i, tick in enumerate(rate_ticks)
    )
    swap_grid = "".join(
        f'<line class="fx-grid" x1="{left:.1f}" x2="{left + width:.1f}" y1="{top_two + panel_height - i * panel_height / 4:.1f}" y2="{top_two + panel_height - i * panel_height / 4:.1f}"/>'
        f'<text class="fx-axis-label" x="{left - 12:.1f}" y="{top_two + panel_height - i * panel_height / 4 + 4:.1f}" text-anchor="end">{tick:,.0f}</text>'
        for i, tick in enumerate(swap_ticks)
    )
    label_indexes = sorted({0, len(points) // 3, len(points) * 2 // 3, len(points) - 1})
    x_labels = "".join(
        f'<text class="fx-axis-label" x="{spot_coords[index][0]:.1f}" y="286" text-anchor="middle">{escape(points[index].source_date[5:])}</text>'
        f'<text class="fx-axis-label" x="{spot_coords[index][0]:.1f}" y="580" text-anchor="middle">{escape(points[index].source_date[5:])}</text>'
        for index in label_indexes
    )
    spot_dots = "".join(
        f'<circle class="fx-dot fx-spot" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>'
        for x, y in spot_coords
    )
    forward_dots = "".join(
        f'<circle class="fx-dot fx-forward" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>'
        for x, y in forward_coords
    )
    swap_dots = "".join(
        f'<circle class="fx-dot fx-swap" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>'
        for x, y in swap_coords
    )
    latest = points[-1]
    rows = "".join(
        '<tr '
        f'data-source-date="{escape(point.source_date)}" '
        f'data-spot-mid="{point.spot_mid:.5f}" '
        f'data-forward-rate="{point.forward_rate:.4f}" '
        f'data-swap-point-pips="{point.swap_point_pips:.2f}">'
        f'<td>{escape(point.source_date)}</td><td>{point.spot_mid:.5f}</td>'
        f'<td>{point.forward_rate:.4f}</td><td>{point.swap_point_pips:.2f}</td></tr>'
        for point in points
    )
    safe_start = escape(points[0].source_date)
    safe_end = escape(points[-1].source_date)
    return f'''<figure class="fx-chart" data-rmb-fx-chart="v1" data-as-of="{safe_end}"
        data-reviewed-through="{escape(report_date)}" data-source-status="{escape(source_status)}"
        data-start-date="{safe_start}" data-end-date="{safe_end}" data-point-count="{len(points)}"
        data-source="chinamoney:validated-review-archive">
  <figcaption><strong>USD/CNY 即期与 1Y 外汇掉期</strong><span>{safe_start} 至 {safe_end} · {len(points)} 个工作日 · 中国货币网 · 数据状态：{escape(source_status)}</span></figcaption>
  <p>[事实] 上图共用“人民币/美元”坐标比较即期买卖算术中值与 1Y C-Swap 全价；下图单列 1Y 掉期点，三者不混算涨跌。</p>
  <svg viewBox="0 0 960 600" role="img" aria-labelledby="fx-chart-title fx-chart-desc">
    <title id="fx-chart-title">USD/CNY 即期与 1Y 外汇掉期趋势</title>
    <desc id="fx-chart-desc">{safe_start} 至 {safe_end}，在岸即期中值、1Y C-Swap 全价与掉期点的工作日折线图。</desc>
    <text class="fx-panel-title" x="78" y="38">汇率</text>
    <line class="fx-legend fx-spot" x1="78" x2="106" y1="54" y2="54"/><text class="fx-legend-text" x="114" y="59">在岸即期中值</text>
    <line class="fx-legend fx-forward" x1="252" x2="280" y1="54" y2="54"/><text class="fx-legend-text" x="288" y="59">1Y C-Swap 全价</text>
    {rate_grid}
    <polyline class="fx-line fx-spot" points="{spot_path}"/>{spot_dots}
    <polyline class="fx-line fx-forward" points="{forward_path}"/>{forward_dots}
    <text class="fx-latest fx-spot-text" x="900" y="{spot_coords[-1][1] + 4:.1f}">{latest.spot_mid:.5f}</text>
    <text class="fx-latest fx-forward-text" x="900" y="{forward_coords[-1][1] + 4:.1f}">{latest.forward_rate:.4f}</text>
    <text class="fx-panel-title" x="78" y="324">掉期点（Pips）</text>
    {swap_grid}
    <polyline class="fx-line fx-swap" points="{swap_path}"/>{swap_dots}
    <text class="fx-latest fx-swap-text" x="900" y="{swap_coords[-1][1] + 4:.1f}">{latest.swap_point_pips:.2f}</text>
    {x_labels}
  </svg>
  <details class="evidence chart-data" data-as-of="{safe_end}" data-items="{len(points)}" data-evidence-kind="rmb-fx-chart-data"><summary>查看图表数据（{len(points)} 项）</summary>
    <div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr><th>来源日</th><th>即期中值</th><th>1Y 全价</th><th>掉期点 Pips</th></tr></thead><tbody>{rows}</tbody></table></div></div>
  </details>
</figure>'''


def _emotion_height_point(
    payload: Mapping[str, object] | None,
    source_date: str,
) -> _EmotionHeightPoint:
    if payload is None or payload.get("date") != source_date:
        return _EmotionHeightPoint(source_date, None, "missing")
    event = payload.get("height_breakthrough")
    if not isinstance(event, Mapping):
        return _EmotionHeightPoint(source_date, None, "missing")
    height = _nonnegative_int(event.get("current_max_height"))
    if (
        event.get("as_of") != source_date
        or event.get("source_status") != "complete"
        or event.get("status") not in {"triggered", "none"}
        or _nonnegative_int(event.get("lookback_open_days"))
        != EMOTION_NODE_LOOKBACK_OPEN_DAYS
        or height is None
        or height > 100
    ):
        return _EmotionHeightPoint(source_date, None, "missing")
    return _EmotionHeightPoint(source_date, height, "complete")


def _load_emotion_height_points(
    current_payload: Mapping[str, object] | None,
    report_date: str,
    archive_dir: str | os.PathLike[str] | None,
    open_dates: Sequence[str] | None = None,
) -> tuple[list[_EmotionHeightPoint], str]:
    """按最近 20 个开放日汇总情绪日报；不可判日期保留为空点。"""

    payloads: dict[str, Mapping[str, object] | None] = {}
    exact_name = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
    if archive_dir is not None:
        root = Path(archive_dir)
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                match = exact_name.fullmatch(path.name)
                if not match:
                    continue
                source_date = match.group(1)
                if not _valid_date(source_date) or source_date > report_date:
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = None
                payloads[source_date] = raw if isinstance(raw, Mapping) else None

    if current_payload is not None and current_payload.get("date") == report_date:
        payloads[report_date] = current_payload

    sample_dates = sorted(payloads)[-EMOTION_HEIGHT_CHART_MAX_SAMPLES:]
    if open_dates is not None:
        normalized_open_dates = sorted(
            {
                value
                for value in open_dates
                if isinstance(value, str)
                and _valid_date(value)
                and value <= report_date
            }
        )[-EMOTION_HEIGHT_CHART_MAX_SAMPLES:]
        if normalized_open_dates:
            sample_dates = normalized_open_dates
    points = [
        _emotion_height_point(payloads.get(source_date), source_date)
        for source_date in sample_dates
    ]
    source_status = "complete"
    if (
        not points
        or points[-1].source_date != report_date
        or any(point.source_status != "complete" for point in points)
    ):
        source_status = "partial"
    return points, source_status


def load_emotion_open_dates(
    db_path: str | os.PathLike[str],
    report_date: str,
) -> tuple[str, ...] | None:
    """尽力读取 canonical 开放日脊柱；不可用时返回 None，由图表显式降级。"""

    source = Path(db_path).expanduser().resolve()
    if not source.is_file():
        return None
    try:
        connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                """
                SELECT date
                FROM trade_calendar
                WHERE date <= ? AND is_open = 1
                ORDER BY date DESC
                LIMIT ?
                """,
                (report_date, EMOTION_HEIGHT_CHART_MAX_SAMPLES),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    dates = [row[0] for row in rows]
    if (
        len(dates) < EMOTION_HEIGHT_CHART_MIN_POINTS
        or any(not isinstance(value, str) or not _valid_date(value) for value in dates)
        or len(dates) != len(set(dates))
    ):
        return None
    return tuple(reversed(dates))


def _render_emotion_height_chart(
    points: Sequence[_EmotionHeightPoint],
    report_date: str,
    source_status: str,
) -> str:
    valid_points = [point for point in points if point.height is not None]
    if len(valid_points) < EMOTION_HEIGHT_CHART_MIN_POINTS:
        return (
            f'<p data-emotion-height-chart="missing-data" data-as-of="{escape(report_date)}" '
            'data-source-status="insufficient-history">'
            f"{EMOTION_HEIGHT_CHART_MISSING_TEXT}</p>"
        )

    left, top, width, height = 72.0, 58.0, 820.0, 230.0
    axis_max = max(2, max(point.height or 0 for point in valid_points))
    coords: list[tuple[float, float] | None] = []
    for index, point in enumerate(points):
        x = left + width * index / max(len(points) - 1, 1)
        if point.height is None:
            coords.append(None)
            continue
        y = top + height - point.height / axis_max * height
        coords.append((x, y))

    segments: list[list[tuple[float, float]]] = []
    current_segment: list[tuple[float, float]] = []
    for coord in coords:
        if coord is None:
            if current_segment:
                segments.append(current_segment)
                current_segment = []
            continue
        current_segment.append(coord)
    if current_segment:
        segments.append(current_segment)
    paths = "".join(
        '<polyline class="emotion-height-line" points="'
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in segment)
        + '"/>'
        for segment in segments
    )
    dots = "".join(
        f'<circle class="emotion-height-dot" cx="{coord[0]:.1f}" cy="{coord[1]:.1f}" r="4"/>'
        f'<text class="emotion-height-value" x="{coord[0]:.1f}" y="{coord[1] - 10:.1f}" text-anchor="middle">{point.height}</text>'
        for point, coord in zip(points, coords)
        if coord is not None
    )
    gaps = "".join(
        f'<text class="emotion-height-gap" x="{left + width * index / max(len(points) - 1, 1):.1f}" '
        f'y="{top + height / 2:.1f}" text-anchor="middle">缺</text>'
        for index, point in enumerate(points)
        if point.height is None
    )
    tick_values = sorted({0, math.ceil(axis_max / 2), axis_max})
    grid = "".join(
        f'<line class="emotion-height-grid" x1="{left:.1f}" x2="{left + width:.1f}" '
        f'y1="{top + height - tick / axis_max * height:.1f}" y2="{top + height - tick / axis_max * height:.1f}"/>'
        f'<text class="emotion-height-axis" x="{left - 12:.1f}" '
        f'y="{top + height - tick / axis_max * height + 4:.1f}" text-anchor="end">{tick}板</text>'
        for tick in tick_values
    )
    label_indexes = sorted(
        {0, len(points) // 3, len(points) * 2 // 3, len(points) - 1}
    )
    x_labels = "".join(
        f'<text class="emotion-height-axis" '
        f'x="{left + width * index / max(len(points) - 1, 1):.1f}" y="318" '
        f'text-anchor="middle">{escape(points[index].source_date[5:])}</text>'
        for index in label_indexes
    )
    rows = "".join(
        '<tr '
        f'data-source-date="{escape(point.source_date)}" '
        f'data-point-status="{"ok" if point.height is not None else "missing"}" '
        f'data-height="{"" if point.height is None else point.height}">'
        f'<td>{escape(point.source_date)}</td><td>{"—（缺失）" if point.height is None else f"{point.height}板"}</td>'
        f'<td>{"完整" if point.height is not None else "不可判"}</td></tr>'
        for point in points
    )
    start_date = points[0].source_date
    end_date = points[-1].source_date
    as_of = valid_points[-1].source_date
    missing_count = len(points) - len(valid_points)
    gap_text = "无缺失点" if not missing_count else f"{missing_count} 个日期不可判，折线已断开"
    return f'''<figure class="emotion-height-chart" data-emotion-height-chart="v1"
    data-as-of="{escape(as_of)}" data-reviewed-through="{escape(report_date)}"
    data-source-status="{escape(source_status)}" data-start-date="{escape(start_date)}"
    data-end-date="{escape(end_date)}" data-point-count="{len(valid_points)}"
    data-sample-count="{len(points)}" data-lookback-open-days="{EMOTION_HEIGHT_CHART_MAX_SAMPLES}"
    data-source="emotion-leader:daily-json-archive">
  <figcaption><strong>最近非 ST 最高连板高度</strong><span>{escape(start_date)} 至 {escape(end_date)} · {len(valid_points)}/{len(points)} 个有效交易日 · 数据状态：{escape(source_status)}</span></figcaption>
  <p>[事实] 每点取当日非 ST 二板及以上股票的最高连板数；确认无符合项时 0 才是 0，缺失数据不按 0 补齐。{escape(gap_text)}。</p>
  <svg viewBox="0 0 960 340" role="img" aria-labelledby="emotion-height-chart-title emotion-height-chart-desc">
    <title id="emotion-height-chart-title">最近非 ST 最高连板高度趋势</title>
    <desc id="emotion-height-chart-desc">{escape(start_date)} 至 {escape(end_date)}，最近最多 20 个开放日的最高连板高度；缺失日期以断线显示。</desc>
    {grid}{paths}{dots}{gaps}{x_labels}
  </svg>
  <details class="evidence chart-data" data-as-of="{escape(as_of)}" data-items="{len(points)}" data-evidence-kind="emotion-height-chart-data"><summary>查看连板高度数据（{len(points)} 项）</summary>
    <div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr><th>交易日</th><th>最高连板</th><th>状态</th></tr></thead><tbody>{rows}</tbody></table></div></div>
  </details>
</figure>'''


def load_emotion_leader_report(
    path: str | os.PathLike[str], report_date: str
) -> dict[str, object]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "date": report_date,
            "status": "source_failed",
            "_load_error": f"{source.name}: {type(exc).__name__}",
        }
    if not isinstance(payload, dict):
        return {
            "date": report_date,
            "status": "source_failed",
            "_load_error": f"{source.name}: 顶层不是对象",
        }
    return payload


def _format_optional_pct(value: object) -> str:
    number = _finite_float(value)
    return "未计算" if number is None else f"{number:.2f}%"


def _emotion_missing_fragment(report_date: str, reason: str = "") -> str:
    note = ""
    if reason:
        note = f'<p class="note">[事实] 缺口原因：{escape(reason[:240])}</p>'
    return (
        f'<p data-emotion-leader="missing-data" data-as-of="{escape(report_date)}" '
        f'data-source-status="failed">{EMOTION_LEADER_MISSING_TEXT}</p>{note}'
    )


def _render_emotion_leader(payload: Mapping[str, object] | None, report_date: str) -> str:
    if payload is None or payload.get("date") != report_date:
        return _emotion_missing_fragment(report_date, "同日 JSON 不存在或日期不匹配")
    status = str(payload.get("status", ""))
    if status not in EMOTION_LEADER_STATUSES or status == "source_failed":
        return _emotion_missing_fragment(
            report_date, str(payload.get("_load_error") or "报告状态为 source_failed")
        )
    active = payload.get("active")
    archived = payload.get("archived")
    summary = payload.get("summary")
    coverage = payload.get("coverage")
    refresh = payload.get("refresh")
    source_errors = payload.get("source_errors")
    promoted = payload.get("promoted_today")
    candidates = payload.get("new_candidates")
    if not (
        isinstance(active, list)
        and isinstance(archived, list)
        and isinstance(summary, dict)
        and isinstance(coverage, dict)
        and isinstance(refresh, dict)
        and isinstance(source_errors, list)
        and isinstance(promoted, list)
        and isinstance(candidates, list)
    ):
        return _emotion_missing_fragment(report_date, "JSON 结构不完整")

    count_keys = {
        "active": _nonnegative_int(summary.get("active_count")),
        "archived": _nonnegative_int(summary.get("archived_count")),
        "limit_up": _nonnegative_int(summary.get("today_limit_up_count")),
        "new_peak": _nonnegative_int(summary.get("new_peak_count")),
        "expected": _nonnegative_int(coverage.get("expected_open_days")),
        "loaded": _nonnegative_int(coverage.get("loaded_limit_days")),
        "refreshed": _nonnegative_int(refresh.get("metric_refresh_count")),
    }
    if (
        any(value is None for value in count_keys.values())
        or count_keys["active"] != len(active)
        or count_keys["archived"] != len(archived)
        or count_keys["loaded"] > count_keys["expected"]
    ):
        return _emotion_missing_fragment(report_date, "汇总计数无法与明细对账")
    if not active:
        if status != "ok":
            return _emotion_missing_fragment(report_date, "partial 状态不能解释为空池")
        return (
            f'<p data-emotion-leader="none" data-as-of="{escape(report_date)}" '
            f'data-source-status="ok">{EMOTION_LEADER_NONE_TEXT}</p>'
        )

    displayed: list[Mapping[str, object]] = []
    for item in active[:EMOTION_LEADER_MAX_ROWS]:
        if not isinstance(item, Mapping):
            return _emotion_missing_fragment(report_date, "活跃核心明细格式无效")
        code = str(item.get("code", "")).upper()
        name = str(item.get("name", "")).strip()
        wave = str(item.get("wave_label") or "未计算")
        if not EMOTION_LEADER_CODE_RE.fullmatch(code) or not name or wave not in EMOTION_LEADER_WAVE_LABELS:
            return _emotion_missing_fragment(report_date, "活跃核心身份或波段标签无效")
        displayed.append(item)

    promoted_names = "、".join(
        escape(str(item.get("name", "")))
        for item in promoted[:5]
        if isinstance(item, Mapping) and item.get("name")
    ) or "无"
    candidate_names = "、".join(
        escape(str(item.get("name", "")))
        for item in candidates[:10]
        if isinstance(item, Mapping) and item.get("name")
    ) or "无"
    rows = []
    for item in displayed:
        code = str(item.get("code", "")).upper()
        name = str(item.get("name", "")).strip()
        wave = str(item.get("wave_label") or "未计算")
        metric_status = str(item.get("metric_status") or "source_failed")
        if metric_status not in {"ok", "source_failed"}:
            metric_status = "source_failed"
        industry = str(item.get("industry") or item.get("limit_industry") or "未分类")
        rows.append(
            '<tr '
            f'data-code="{escape(code)}" data-wave-label="{escape(wave)}" '
            f'data-metric-status="{escape(metric_status)}">'
            f'<td>{escape(name)}<br><small>{escape(code)}</small></td>'
            f'<td>{escape(str(item.get("board_type") or "—"))}</td>'
            f'<td>[判断] {escape(wave)}</td><td>{escape(industry)}</td>'
            f'<td>{_format_optional_pct(item.get("max_gain_pct"))}</td>'
            f'<td>{_format_optional_pct(item.get("interval_gain_pct"))}</td>'
            f'<td>{_format_optional_pct(item.get("distance_from_peak_pct"))}</td>'
            f'<td>{escape(str(item.get("launch_date") or "—"))} / {escape(str(item.get("max_height") or "—"))}板</td>'
            f'<td>{escape(str(item.get("current_state") or "未计算"))}</td></tr>'
        )
    error_items = "".join(
        f"<li>{escape(str(error)[:240])}</li>" for error in source_errors[:3]
    )
    errors_block = (
        f'<p class="note">[事实] source_errors 共 {len(source_errors)} 条，前 3 条：</p><ul>{error_items}</ul>'
        if source_errors
        else '<p class="note">[事实] source_errors：0 条。</p>'
    )
    refresh_mode = escape(str(refresh.get("mode") or "unknown"))
    return f'''<div class="emotion-leader" data-emotion-leader="v1" data-as-of="{escape(report_date)}"
    data-source-status="{escape(status)}" data-active-count="{count_keys['active']}"
    data-archived-count="{count_keys['archived']}" data-today-limit-up-count="{count_keys['limit_up']}"
    data-new-peak-count="{count_keys['new_peak']}" data-promoted-count="{len(promoted)}"
    data-candidate-count="{len(candidates)}" data-displayed-count="{len(displayed)}"
    data-error-count="{len(source_errors)}" data-coverage-loaded="{count_keys['loaded']}"
    data-coverage-expected="{count_keys['expected']}" data-refresh-mode="{refresh_mode}"
    data-refreshed-count="{count_keys['refreshed']}">
  <p><strong>[事实] 情绪核心生命周期：</strong>状态 {escape(status)}；历史覆盖 {count_keys['loaded']}/{count_keys['expected']}；刷新 {refresh_mode} / {count_keys['refreshed']} 只；活跃 {count_keys['active']} / 归档 {count_keys['archived']}；今日涨停 {count_keys['limit_up']} / 创新高 {count_keys['new_peak']}。</p>
  <p>[事实] 今日晋级核心：{promoted_names}；新增二连板候选：{candidate_names}。</p>
  <details class="evidence" data-as-of="{escape(report_date)}" data-items="{len(displayed)}" data-evidence-kind="emotion-leader">
    <summary>活跃核心前 {len(displayed)} 只（{len(displayed)} 项）</summary>
    <div class="evidence-body"><div class="table-scroll-shell"><table>
      <thead><tr><th>核心</th><th>板型</th><th>波段</th><th>行业</th><th>最大涨幅</th><th>区间涨幅</th><th>距峰值</th><th>启动/高度</th><th>今日状态</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div>{errors_block}</div>
  </details>
</div>'''


def _emotion_node_missing_fragment(
    report_date: str,
    *,
    source_status: str = "failed",
) -> str:
    normalized_status = source_status if source_status in {"partial", "failed"} else "failed"
    return (
        f'<p data-emotion-node="missing-data" data-as-of="{escape(report_date)}" '
        f'data-source-status="{normalized_status}">{EMOTION_NODE_MISSING_TEXT}</p>'
    )


def _render_emotion_node(
    payload: Mapping[str, object] | None,
    report_date: str,
) -> str:
    """将「打开连板高度 → 启动日节点候选」自动注入 s6。"""

    if payload is None or payload.get("date") != report_date:
        return _emotion_node_missing_fragment(report_date)
    payload_status = str(payload.get("status") or "")
    if payload_status == "source_failed":
        return _emotion_node_missing_fragment(report_date)
    event = payload.get("height_breakthrough")
    if not isinstance(event, Mapping):
        return _emotion_node_missing_fragment(report_date)

    status = str(event.get("status") or "")
    source_status = str(event.get("source_status") or "")
    if status == "missing_data":
        return _emotion_node_missing_fragment(
            report_date,
            source_status=source_status,
        )
    lookback = _nonnegative_int(event.get("lookback_open_days"))
    current_height = _nonnegative_int(event.get("current_max_height"))
    previous_height = _nonnegative_int(event.get("previous_max_height"))
    if (
        source_status != "complete"
        or lookback != EMOTION_NODE_LOOKBACK_OPEN_DAYS
        or current_height is None
        or previous_height is None
    ):
        return _emotion_node_missing_fragment(report_date)
    common_attrs = (
        f'data-as-of="{escape(report_date)}" data-source-status="complete" '
        f'data-lookback-open-days="{lookback}" '
        f'data-current-max-height="{current_height}" '
        f'data-previous-max-height="{previous_height}"'
    )
    if status == "none":
        if current_height > previous_height:
            return _emotion_node_missing_fragment(report_date)
        return (
            f'<p data-emotion-node="none" {common_attrs}>'
            f"{EMOTION_NODE_NONE_TEXT}</p>"
        )
    if status != "triggered" or current_height < 2 or current_height <= previous_height:
        return _emotion_node_missing_fragment(report_date)

    window_start = str(event.get("previous_window_start") or "")
    window_end = str(event.get("previous_window_end") or "")
    leaders = event.get("leaders")
    if (
        not _valid_date(window_start)
        or not _valid_date(window_end)
        or not window_start <= window_end < report_date
        or not isinstance(leaders, list)
        or not leaders
        or len(leaders) > 20
    ):
        return _emotion_node_missing_fragment(report_date)

    rows: list[str] = []
    fact_names: list[str] = []
    judgment_names: list[str] = []
    for item in leaders:
        if not isinstance(item, Mapping):
            return _emotion_node_missing_fragment(report_date)
        code = str(item.get("code") or "").upper()
        name = str(item.get("name") or "").strip()
        launch_date = str(item.get("launch_date") or "")
        launch_method = str(item.get("launch_method") or "")
        leader_height = _nonnegative_int(item.get("current_height"))
        if (
            not EMOTION_LEADER_CODE_RE.fullmatch(code)
            or not name
            or not _valid_date(launch_date)
            or launch_date > report_date
            or launch_method not in EMOTION_NODE_LAUNCH_METHODS
            or leader_height != current_height
        ):
            return _emotion_node_missing_fragment(report_date)
        method_label = "连板链直接确认" if launch_method == "limit_chain" else "日历保守推定"
        fact_names.append(f"{escape(name)} {current_height}板")
        judgment_names.append(f"{escape(name)}启动日{escape(launch_date)}")
        rows.append(
            f'<tr data-code="{escape(code)}" data-launch-date="{escape(launch_date)}" '
            f'data-launch-method="{escape(launch_method)}" '
            f'data-current-height="{current_height}">'
            f"<td>{escape(name)}<br><small>{escape(code)}</small></td>"
            f"<td>{current_height}板</td><td>{escape(launch_date)}</td>"
            f"<td>{method_label}</td></tr>"
        )

    return f'''<div class="emotion-node" data-emotion-node="v1" {common_attrs}
    data-window-start="{escape(window_start)}" data-window-end="{escape(window_end)}"
    data-leader-count="{len(rows)}">
  <p><strong>[事实] 打开非ST连板高度：</strong>{'、'.join(fact_names)}；此前{lookback}个开放日最高{previous_height}板。
  [判断] {'、'.join(judgment_names)}列为情绪节点日候选；该线索不替代事件日历或市场/板块结构确认。</p>
  <details class="evidence" data-as-of="{escape(report_date)}" data-items="{len(rows)}" data-evidence-kind="emotion-node">
    <summary>打开高度与启动日对账（{len(rows)} 项）</summary>
    <div class="evidence-body"><div class="table-scroll-shell"><table>
      <thead><tr><th>情绪核心</th><th>当日高度</th><th>启动日</th><th>启动日来源</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table></div></div>
  </details>
</div>'''


def _inject_section_fragment(html: str, section_id: str, fragment: str) -> str:
    pattern = re.compile(
        rf'(<section\b[^>]*\bid=["\']{re.escape(section_id)}["\'][^>]*>)(.*?)(</section>)',
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(html))
    if len(matches) != 1:
        raise ReportValidationError(
            "invalid_injection_target",
            f"自动模块要求唯一 section#{section_id}，实际 {len(matches)}",
            section=section_id,
        )
    match = matches[0]
    replacement = f"{match.group(1)}{match.group(2)}\n{fragment}\n{match.group(3)}"
    return html[: match.start()] + replacement + html[match.end() :]


def _validate_date(report_date: str) -> None:
    if not _valid_date(report_date):
        raise ReportValidationError(
            "invalid_date", "日期必须是有效的 YYYY-MM-DD"
        )


def _strip_legacy_section(html: str, section_id: str) -> str:
    """从 chunk 中移除一个旧版顶层 section。

    旧 scratchpad 可能仍带 exposure；新版正式组装在输出前确定性剔除。
    同名 section 重复时 fail-closed，不猜测应删哪一个。
    """

    pattern = re.compile(
        rf"<section\b(?=[^>]*\bid=[\"']{re.escape(section_id)}[\"'])[^>]*>.*?</section\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(html))
    if len(matches) > 1:
        raise ReportValidationError(
            "duplicate_removed_section",
            f"旧版 section#{section_id} 出现 {len(matches)} 次，拒绝自动删除",
            section=section_id,
        )
    return pattern.sub("", html, count=1)


def render_report(
    tmp_dir: str | os.PathLike[str],
    report_date: str,
    *,
    emotion_leader_report: Mapping[str, object] | None = None,
    emotion_history_dir: str | os.PathLike[str] | None = None,
    emotion_open_dates: Sequence[str] | None = None,
    fx_history_dir: str | os.PathLike[str] | None = None,
    include_legacy_sections: bool = False,
) -> str:
    """读取固定 chunk，包裹静态阅读器外壳并返回 HTML 字符串。

    默认使用不含 exposure/proj 的新版 7-chunk 布局。
    ``include_legacy_sections`` 仅用于旧归档迁移与回归校验。
    """

    _validate_date(report_date)
    tmp_path = Path(tmp_dir)
    chunk_order = CHUNK_ORDER if include_legacy_sections else CURRENT_CHUNK_ORDER
    chunks: dict[str, str] = {}
    missing: list[str] = []
    for chunk in chunk_order:
        path = _chunk_path(tmp_path, report_date, chunk)
        if not path.is_file():
            missing.append(path.name)
            continue
        chunks[chunk] = path.read_text(encoding="utf-8")
    if missing:
        raise ReportValidationError(
            "missing_chunk", f"缺少 chunk：{', '.join(missing)}"
        )
    if "data-rmb-fx-chart=" in chunks["s1"]:
        raise ReportValidationError(
            "duplicate_rmb_fx_chart",
            "s1 的人民币外汇图由组装器统一生成，chunk 不得自行注入",
            section="s1",
        )
    if "data-emotion-leader=" in chunks["s456"]:
        raise ReportValidationError(
            "duplicate_emotion_leader",
            "s3 的情绪核心模块由组装器统一生成，chunk 不得自行注入",
            section="s3",
        )
    if "data-emotion-height-chart=" in chunks["s456"]:
        raise ReportValidationError(
            "duplicate_emotion_height_chart",
            "s3 的最近连板高度趋势图由组装器统一生成，chunk 不得自行注入",
            section="s3",
        )
    if "data-emotion-node=" in chunks["s456"]:
        raise ReportValidationError(
            "duplicate_emotion_node",
            "s6 的情绪高度节点联动由组装器统一生成，chunk 不得自行注入",
            section="s6",
        )

    fx_points, fx_status = _load_fx_chart_points(
        chunks["s1"], report_date, fx_history_dir
    )
    chunks["s1"] = _inject_section_fragment(
        chunks["s1"],
        "s1",
        _render_fx_chart(fx_points, report_date, fx_status),
    )
    chunks["s456"] = _inject_section_fragment(
        chunks["s456"],
        "s3",
        _render_emotion_leader(emotion_leader_report, report_date),
    )
    emotion_height_points, emotion_height_status = _load_emotion_height_points(
        emotion_leader_report,
        report_date,
        emotion_history_dir,
        emotion_open_dates,
    )
    chunks["s456"] = _inject_section_fragment(
        chunks["s456"],
        "s3",
        _render_emotion_height_chart(
            emotion_height_points,
            report_date,
            emotion_height_status,
        ),
    )
    chunks["s456"] = _inject_section_fragment(
        chunks["s456"],
        "s6",
        _render_emotion_node(emotion_leader_report, report_date),
    )
    if not include_legacy_sections:
        chunks["s7t"] = _strip_legacy_section(chunks["s7t"], "exposure")

    parts: list[str] = []
    for chunk in chunk_order:
        parts.append(
            f'<div class="report-chunk" data-report-chunk="{chunk}">\n'
            f"{chunks[chunk]}\n"
            "</div>"
        )

    css_path = Path(__file__).with_name("review_style.css")
    css = css_path.read_text(encoding="utf-8")
    body = "\n\n".join(parts)
    nav = NAV if include_legacy_sections else CURRENT_NAV
    side_nav = "\n".join(f'      <a href="#{item}">{title}</a>' for item, title in nav)
    mobile_nav = "\n".join(f'    <a href="#{item}">{title}</a>' for item, title in nav)
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
    <small class="sidebar-note">八步复盘法 v1.5 · 多 Agent 完整采集 · compact-v2 只读产物</small>
  </aside>
  <main class="reader-main">
    <article class="report-document" id="report-document" data-report-schema="{REPORT_SCHEMA}" data-report-date="{safe_date}"{'' if include_legacy_sections else f' data-report-layout="{CURRENT_REPORT_LAYOUT}"'}>
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
    exposure_context: ExposureValidationContext | None = None,
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
        exposure_context=exposure_context,
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
    parser.add_argument("tmp_dir", help="包含 7 个正式 HTML chunk 的临时目录")
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
    parser.add_argument(
        "--trade-db",
        help="只读市场/交易日历/组合事实库；省略时读取 data/trade.db",
    )
    parser.add_argument(
        "--emotion-leader-report",
        help="情绪核心生命周期 JSON；省略时读取 data/reports/emotion-leader/<DATE>.json",
    )
    parser.add_argument(
        "--emotion-history-dir",
        help="历史情绪核心 JSON 目录；省略时使用情绪核心日报所在目录，用于绘制最近连板高度趋势图",
    )
    parser.add_argument(
        "--fx-history-dir",
        help="历史复盘 HTML 目录；省略时读取 data/reports，用于绘制人民币外汇趋势图",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        emotion_path = args.emotion_leader_report or (
            _repo_root() / "data" / "reports" / "emotion-leader" / f"{args.date}.json"
        )
        emotion_report = load_emotion_leader_report(emotion_path, args.date)
        emotion_history_dir = args.emotion_history_dir or Path(emotion_path).parent
        trade_db_path = args.trade_db or (_repo_root() / "data" / "trade.db")
        emotion_open_dates = load_emotion_open_dates(trade_db_path, args.date)
        fx_history_dir = args.fx_history_dir or (
            _repo_root() / "data" / "reports"
        )
        html = render_report(
            args.tmp_dir,
            args.date,
            emotion_leader_report=emotion_report,
            emotion_history_dir=emotion_history_dir,
            emotion_open_dates=emotion_open_dates,
            fx_history_dir=fx_history_dir,
        )
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
        exposure_context = None
        parsed = _parse_report(html)
        if _requires_exposure_validation_context(parsed):
            exposure_context = load_exposure_validation_context(
                trade_db_path,
                args.date,
            )
        metrics = validate_report(
            html,
            capacity_manifest=capacity_manifest,
            new_high_manifest=new_high_manifest,
            exposure_context=exposure_context,
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
