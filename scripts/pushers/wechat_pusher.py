"""
企业微信机器人推送
也可扩展为个人微信推送（通过第三方库）
"""
from __future__ import annotations

import os
import logging
import requests
from .base import MessagePusher

logger = logging.getLogger(__name__)


class WechatPusher(MessagePusher):
    name = "wechat"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.webhook_url = ""

    def initialize(self) -> bool:
        self.webhook_url = self.config.get("webhook_url") or os.getenv("WECHAT_WEBHOOK", "")
        self.enabled = bool(self.webhook_url)
        if not self.enabled:
            logger.warning("企业微信: 未配置 webhook")
        return self.enabled

    def send_text(self, text: str, channel: str = "default") -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                self.webhook_url,
                json={
                    "msgtype": "text",
                    "text": {"content": text[:4096]},
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False

    def send_markdown(self, title: str, content: str, channel: str = "default") -> bool:
        if not self.enabled:
            return False
        try:
            # 企业微信 markdown 格式
            md_content = f"## {title}\n\n{content}"
            resp = requests.post(
                self.webhook_url,
                json={
                    "msgtype": "markdown",
                    "markdown": {"content": md_content[:4096]},
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"企业微信发送异常: {e}")
            return False
