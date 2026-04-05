"""接口注册表：定义原始事实层的接口元数据。"""
from __future__ import annotations

from typing import TypedDict


class InterfaceConfig(TypedDict, total=False):
    interface_name: str
    provider_method: str
    stage: str
    use_cases: list[str]
    params_policy: str
    dedupe_keys: list[str]
    raw_table: str
    enabled_by_default: bool
    notes: str


INTERFACE_REGISTRY: dict[str, InterfaceConfig] = {
    "daily_basic": {
        "interface_name": "daily_basic",
        "provider_method": "get_daily_basic",
        "stage": "post_core",
        "use_cases": ["post_report", "plan_diagnostics", "history_compare"],
        "params_policy": "trade_date",
        "dedupe_keys": ["ts_code", "trade_date"],
        "raw_table": "raw_daily_basic",
        "enabled_by_default": True,
        "notes": "盘后核心基础面快照，用于日级指标与计划诊断。",
    },
    "adj_factor": {
        "interface_name": "adj_factor",
        "provider_method": "get_adj_factor",
        "stage": "post_core",
        "use_cases": ["post_report", "history_compare"],
        "params_policy": "trade_date",
        "dedupe_keys": ["ts_code", "trade_date"],
        "raw_table": "raw_adj_factor",
        "enabled_by_default": True,
        "notes": "复权因子，供历史走势和后续诊断使用。",
    },
    "moneyflow_hsgt": {
        "interface_name": "moneyflow_hsgt",
        "provider_method": "get_northbound",
        "stage": "post_core",
        "use_cases": ["post_report", "plan_diagnostics"],
        "params_policy": "trade_date",
        "dedupe_keys": ["trade_date"],
        "raw_table": "raw_moneyflow_hsgt",
        "enabled_by_default": True,
        "notes": "北向资金核心数据，属于盘后主流程硬依赖。",
    },
    "margin": {
        "interface_name": "margin",
        "provider_method": "get_margin_data",
        "stage": "post_extended",
        "use_cases": ["post_report", "plan_diagnostics", "research"],
        "params_policy": "trade_date",
        "dedupe_keys": ["trade_date", "exchange_id"],
        "raw_table": "raw_margin",
        "enabled_by_default": True,
        "notes": "融资融券数据，属于扩展事实层，可支持计划诊断。",
    },
    "block_trade": {
        "interface_name": "block_trade",
        "provider_method": "get_block_trade",
        "stage": "post_extended",
        "use_cases": ["watchlist_context", "research", "history_compare"],
        "params_policy": "trade_date",
        "dedupe_keys": ["ts_code", "trade_date", "price", "vol"],
        "raw_table": "raw_block_trade",
        "enabled_by_default": True,
        "notes": "盘后扩展接口，用于观察机构大宗交易和关注池上下文。",
    },
    "top_inst": {
        "interface_name": "top_inst",
        "provider_method": "get_dragon_tiger",
        "stage": "post_extended",
        "use_cases": ["post_report", "watchlist_context", "research"],
        "params_policy": "trade_date",
        "dedupe_keys": ["ts_code", "trade_date", "exalter"],
        "raw_table": "raw_top_inst",
        "enabled_by_default": True,
        "notes": "龙虎榜机构席位数据，扩展观察，不作为主流程硬依赖。",
    },
    "share_float": {
        "interface_name": "share_float",
        "provider_method": "get_share_float",
        "stage": "backfill",
        "use_cases": ["research", "history_compare"],
        "params_policy": "explicit_only",
        "dedupe_keys": ["ts_code", "ann_date", "float_date"],
        "raw_table": "raw_share_float",
        "enabled_by_default": False,
        "notes": "解禁数据以历史回填和研究为主，默认不参与每日任务。",
    },
}


def list_interfaces() -> list[InterfaceConfig]:
    """按 stage 和 interface_name 排序返回注册表。"""
    return sorted(
        INTERFACE_REGISTRY.values(),
        key=lambda item: (item["stage"], item["interface_name"]),
    )


def get_interface(name: str) -> InterfaceConfig | None:
    return INTERFACE_REGISTRY.get(name)
