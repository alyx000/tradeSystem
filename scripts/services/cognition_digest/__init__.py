"""交易认知沉淀定时汇总（只读）：近3日/近1周/近1月 → 钉钉。"""
from __future__ import annotations

from .service import RenderedCognitionDigest, run_window_digest
from .windows import WINDOWS

__all__ = ["run_window_digest", "RenderedCognitionDigest", "WINDOWS"]
