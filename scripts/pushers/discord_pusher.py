"""
Discord Webhook 推送
"""
from __future__ import annotations

import os
import logging
import requests
from .base import MessagePusher

logger = logging.getLogger(__name__)

# Discord 单条消息上限 2000 字符
MAX_LENGTH = 1900


class DiscordPusher(MessagePusher):
    name = "discord"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.webhooks = {}

    def initialize(self) -> bool:
        self.webhooks = {
            "pre_market": self.config.get("webhook_pre") or os.getenv("DISCORD_WEBHOOK_PRE", ""),
            "post_market": self.config.get("webhook_post") or os.getenv("DISCORD_WEBHOOK_POST", ""),
            "alert": self.config.get("webhook_alert") or os.getenv("DISCORD_WEBHOOK_ALERT", ""),
            "default": self.config.get("webhook_pre") or os.getenv("DISCORD_WEBHOOK_PRE", ""),
        }
        self.enabled = any(v for v in self.webhooks.values())
        if not self.enabled:
            logger.warning("Discord: 未配置任何 webhook")
        return self.enabled

    def _send(self, webhook_url: str, content: str) -> bool:
        if not webhook_url:
            return False
        try:
            # 如果内容过长，分段发送
            chunks = self._split(content, MAX_LENGTH)
            for chunk in chunks:
                resp = requests.post(
                    webhook_url,
                    json={"content": chunk},
                    timeout=10,
                )
                if resp.status_code not in (200, 204):
                    logger.error(f"Discord 发送失败: {resp.status_code} {resp.text}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Discord 发送异常: {e}")
            return False

    def _split(self, text: str, max_len: int) -> list[str]:
        """按行分割长文本"""
        if len(text) <= max_len:
            return [text]
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    def send_text(self, text: str, channel: str = "default") -> bool:
        url = self.webhooks.get(channel, self.webhooks.get("default", ""))
        return self._send(url, text)

    def send_markdown(self, title: str, content: str, channel: str = "default") -> bool:
        # Discord 不原生支持完整 Markdown，但支持代码块
        # 对于表格等用代码块包裹
        url = self.webhooks.get(channel, self.webhooks.get("default", ""))
        formatted = f"**{title}**\n\n{content}"
        return self._send(url, formatted)
