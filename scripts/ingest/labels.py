"""采集标签：为 API/CLI/Web 提供一致的中文可读说明。"""
from __future__ import annotations

STAGE_LABELS: dict[str, str] = {
    "pre_core": "盘前核心",
    "post_core": "盘后核心",
    "post_extended": "盘后扩展",
    "watchlist": "关注池",
    "backfill": "低频回填",
}

STATUS_LABELS: dict[str, str] = {
    "success": "成功",
    "failed": "失败",
    "empty": "空结果",
    "partial": "部分成功",
    "running": "执行中",
}

ERROR_TYPE_LABELS: dict[str, str] = {
    "provider": "数据源失败",
    "network": "网络异常",
    "validation": "参数校验",
    "storage": "写库失败",
}

HEALTH_STATUS_LABELS = {"stable": "稳定", "fluctuating": "有波动", "stressed": "承压", "action": "需处理"}

INTERFACE_FALLBACK_LABELS: dict[str, str] = {
    "daily_basic": "日线基础指标",
    "adj_factor": "复权因子",
    "moneyflow_hsgt": "北向资金",
    "daily_info": "市场交易统计",
    "limit_step": "连板天梯",
    "limit_cpt_list": "最强板块统计",
    "moneyflow_ind_ths": "同花顺行业资金流",
    "moneyflow_ind_dc": "东财板块资金流",
    "moneyflow_mkt_dc": "大盘资金流向",
    "margin": "融资融券汇总",
    "margin_detail": "融资融券明细",
    "block_trade": "大宗交易",
    "top_inst": "龙虎榜机构席位",
    "stock_st": "ST股票名单",
    "anns_d": "全市场公告",
    "disclosure_date": "财报披露计划",
    "stk_limit": "涨跌停价格",
    "ths_index": "同花顺板块主数据",
    "ths_member": "同花顺板块成分",
    "index_classify": "申万行业分类",
    "stock_basic": "A股主数据",
    "trade_cal": "交易日历",
}


def provider_label(provider: str | None) -> str:
    if not provider:
        return "未知来源"
    if provider == "tushare":
        return "Tushare"
    if provider == "akshare":
        return "AkShare"
    if provider.startswith("tushare:"):
        return f"Tushare · {provider.split(':', 1)[1]}"
    if provider.startswith("akshare:"):
        return f"AkShare · {provider.split(':', 1)[1]}"
    if provider == "registry":
        return "自动降级链路"
    if provider.startswith("get_"):
        return f"Provider 方法 · {provider}"
    return provider


def fallback_interface_meaning(interface_name: str | None) -> str:
    raw = str(interface_name or "").strip()
    if not raw:
        return "暂无中文说明"
    if raw in INTERFACE_FALLBACK_LABELS:
        return INTERFACE_FALLBACK_LABELS[raw]
    return " / ".join(part.upper() for part in raw.split("_") if part)


def short_interface_meaning(interface_name: str | None, note: str | None) -> str:
    raw = str(note or "").strip()
    if not raw:
        return fallback_interface_meaning(interface_name)
    cut = raw.split("，", 1)[0].split("。", 1)[0].strip()
    return cut or raw


def stage_label(stage: str | None) -> str:
    if not stage:
        return "未知阶段"
    return STAGE_LABELS.get(stage, stage)


def status_label(status: str | None) -> str:
    if not status:
        return "未知状态"
    return STATUS_LABELS.get(status, status)


def error_type_label(error_type: str | None) -> str:
    if not error_type:
        return "错误"
    return ERROR_TYPE_LABELS.get(error_type, error_type)


def bool_label(value: bool | None) -> str:
    if value is None:
        return "未知"
    return "是" if value else "否"


def retryable_label(value: int | bool | None) -> str:
    if value is None:
        return "未知"
    return "可重试" if bool(value) else "不可重试"


def restriction_label(error_message: str | None) -> str | None:
    msg = str(error_message or "")
    if any(token in msg for token in ("权限不足", "积分不足")):
        return "权限受限"
    if any(token in msg for token in ("token不对", "未配置 token", "未配置")):
        return "配置缺失"
    return None


def restriction_reason(error_message: str | None) -> str | None:
    label = restriction_label(error_message)
    if label == "权限受限":
        return "当前账号对该接口没有调用权限，或积分不足。"
    if label == "配置缺失":
        return "当前环境未正确配置数据源 Token 或相关凭据。"
    return None


def remediation_hint(
    *,
    error_type: str | None = None,
    error_message: str | None = None,
    retryable: int | bool | None = None,
) -> str:
    msg = str(error_message or "")
    if any(token in msg for token in ("权限不足", "积分不足", "token不对", "未配置 token", "未配置")):
        return "检查 Tushare Token、接口权限或积分；确认当前账号已开通该接口。"
    if any(token in msg.lower() for token in ("timeout", "timed out", "connection", "reset", "proxy")):
        return "优先重试一次；若连续失败，检查网络、代理配置和数据源可达性。"
    if "未实现" in msg or "not implemented" in msg.lower():
        return "当前 provider 尚未实现该接口；优先检查注册表映射和 provider 方法。"
    if error_type == "validation":
        return "检查日期、参数策略和调用入参，确认格式与接口要求一致。"
    if error_type == "storage":
        return "检查 SQLite 写入、迁移版本和表结构；必要时先执行 db sync 或 reconcile。"
    if retryable is not None and not bool(retryable):
        return "该错误当前不建议自动重试；应先修正权限或配置问题。"
    if error_type == "network":
        return "建议稍后重试；若反复出现，排查网络、DNS、代理和限流。"
    if error_type == "provider":
        return "先看原始错误，再决定是重试、切换数据源还是修正接口实现。"
    return "先查看原始错误与接口说明，再决定重试或修正配置。"


def health_status_label(
    *,
    unresolved_failures: int | None = None,
    failed_interface_count: int | None = None,
    never_succeeded_count: int | None = None,
    consecutive_failure_days: int | None = None,
    total_failures: int | None = None,
) -> str:
    unresolved = int(unresolved_failures or 0)
    failed_interfaces = int(failed_interface_count or 0)
    never_succeeded = int(never_succeeded_count or 0)
    top_streak = int(consecutive_failure_days or 0)
    failures = int(total_failures or 0)

    if unresolved == 0 and failed_interfaces == 0:
        return HEALTH_STATUS_LABELS["stable"]
    if never_succeeded > 0 or unresolved >= 2:
        return HEALTH_STATUS_LABELS["action"]
    if top_streak >= 3 or unresolved > 0:
        return HEALTH_STATUS_LABELS["stressed"]
    if failures > 0:
        return HEALTH_STATUS_LABELS["fluctuating"]
    return HEALTH_STATUS_LABELS["stable"]


def health_status_reason(
    *,
    unresolved_failures: int | None = None,
    failed_interface_count: int | None = None,
    never_succeeded_count: int | None = None,
    consecutive_failure_days: int | None = None,
    total_failures: int | None = None,
) -> str:
    unresolved = int(unresolved_failures or 0)
    failed_interfaces = int(failed_interface_count or 0)
    never_succeeded = int(never_succeeded_count or 0)
    top_streak = int(consecutive_failure_days or 0)
    failures = int(total_failures or 0)

    if unresolved == 0 and failed_interfaces == 0:
        return "近 7 天没有未解决失败，当前阶段采集链路稳定。"
    if never_succeeded > 0:
        return "存在从未成功过的接口，建议优先排查权限、配置或实现缺口。"
    if unresolved >= 2:
        return f"当前仍有 {unresolved} 条未解决失败，建议优先处理主链路异常。"
    if top_streak >= 3:
        return f"存在连续失败 {top_streak} 天的接口，阶段稳定性已明显承压。"
    if unresolved > 0:
        return "当前仍有未解决失败，建议尽快重试或排查数据源状态。"
    if failures > 0:
        return "近 7 天出现过失败，但暂未形成持续性异常。"
    return "近 7 天没有未解决失败，当前阶段采集链路稳定。"
