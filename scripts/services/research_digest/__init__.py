"""研报速读服务包：A股/美股结构化评级事实 + 受控 LLM 叙事 → Top3 → MD。

职责反转（v6）：事实源（akshare 研报评级 / yfinance 美股评级）负责"发现"，
LLM 仅对真实条目做 theme/one_liner 受控叙事（可关），任何地方不让 LLM 发现研报。
"""
from __future__ import annotations

from .service import run_daily_digest

__all__ = ["run_daily_digest"]
