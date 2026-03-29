"""
消息推送基类
所有推送渠道（Discord/QQ/微信等）继承此基类
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class MessagePusher(ABC):
    """消息推送基类"""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.enabled = False

    @abstractmethod
    def initialize(self) -> bool:
        """初始化，返回是否成功"""
        ...

    @abstractmethod
    def send_text(self, text: str, channel: str = "default") -> bool:
        """发送文本消息"""
        ...

    @abstractmethod
    def send_markdown(self, title: str, content: str, channel: str = "default") -> bool:
        """发送 Markdown 格式消息"""
        ...

    def send_report(self, report_type: str, title: str, content: str) -> bool:
        """
        发送报告，根据类型选择频道。
        report_type: pre_market / post_market / alert
        """
        channel = self.config.get("channels", {}).get(report_type, "default")
        return self.send_markdown(title, content, channel)
