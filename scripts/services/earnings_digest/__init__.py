"""业绩预告/快报每日速报：采集存档 + 标准化 + 缺口验证 + 钉钉推送。

管线（service.run_daily_digest 编排）：
    IngestService.execute_interface（权威取数+落库）
    → collector（读回落库 payload + 水位线过滤 + 持仓/关注命中）
    → normalize（DTO：多版本修正/单位/口径二偏离）
    → gap_check（预告次日缺口 + 市场投票 2×2）
    → renderer（五段 markdown，空日返回 None）
"""
from .service import run_daily_digest

__all__ = ["run_daily_digest"]
