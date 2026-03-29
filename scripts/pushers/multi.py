"""
多渠道推送管理器
同时向多个渠道推送消息
"""
from __future__ import annotations

import logging
from .base import MessagePusher

logger = logging.getLogger(__name__)


class MultiPusher:
    """管理多个推送渠道，统一推送"""

    def __init__(self):
        self._pushers: list[MessagePusher] = []

    def register(self, pusher: MessagePusher) -> None:
        if pusher.enabled:
            self._pushers.append(pusher)
            logger.info(f"注册推送渠道: {pusher.name}")

    def send_report(self, report_type: str, title: str, content: str) -> dict[str, bool]:
        """向所有渠道推送报告，返回各渠道结果"""
        results = {}
        for pusher in self._pushers:
            ok = pusher.send_report(report_type, title, content)
            results[pusher.name] = ok
            if ok:
                logger.info(f"  推送成功: {pusher.name}")
            else:
                logger.warning(f"  推送失败: {pusher.name}")
        return results

    def send_alert(self, message: str) -> dict[str, bool]:
        """发送告警消息到所有渠道"""
        results = {}
        for pusher in self._pushers:
            ok = pusher.send_text(message, channel="alert")
            results[pusher.name] = ok
        return results
