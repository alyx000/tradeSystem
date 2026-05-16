"""
DingTalk Webhook 推送
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import time
import urllib.parse

import requests

from .base import MessagePusher

logger = logging.getLogger(__name__)

_CREDENTIAL_RE = re.compile(r"(access_token|sign)=[^&\s'\"]+", re.IGNORECASE)


def _redact(text: str) -> str:
    """脱敏 access_token / sign 查询参数，防止异常日志泄漏凭据。"""
    return _CREDENTIAL_RE.sub(r"\1=<redacted>", text)


class DingTalkPusher(MessagePusher):
    name = "dingtalk"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.webhook_token = ""
        self.webhook_secret = ""

    def initialize(self) -> bool:
        self.webhook_token = self.config.get("webhook_token") or os.getenv(
            "DINGTALK_WEBHOOK_TOKEN", ""
        )
        self.webhook_secret = self.config.get("webhook_secret") or os.getenv(
            "DINGTALK_WEBHOOK_SECRET", ""
        )
        self.enabled = bool(self.webhook_token and self.webhook_secret)
        if not self.enabled:
            logger.warning("DingTalk: 未配置 webhook token 或 secret")
        return self.enabled

    @staticmethod
    def _sign(timestamp: str, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}".encode()
        digest = hmac.new(secret.encode(), string_to_sign, hashlib.sha256).digest()
        return urllib.parse.quote_plus(base64.b64encode(digest))

    def send_text(self, text: str, channel: str = "default") -> bool:
        return self.send_markdown("消息", text, channel)

    def _post(self, url: str, payload: dict) -> bool:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(
                    f"DingTalk 发送失败: {resp.status_code} {getattr(resp, 'text', '')}"
                )
                return False
            data = resp.json()
            if data.get("errcode") != 0:
                logger.error(
                    f"DingTalk 发送失败: {data.get('errcode')} {data.get('errmsg', '')}"
                )
                return False
            return True
        except Exception as e:
            # 脱敏：requests 异常 message 常含完整 URL（带 access_token / sign）
            logger.error("DingTalk 发送异常: %s: %s", type(e).__name__, _redact(str(e)))
            return False

    def send_markdown(self, title: str, content: str, channel: str = "default") -> bool:
        if not self.enabled:
            return False

        timestamp = str(int(time.time() * 1000))
        sign = self._sign(timestamp, self.webhook_secret)
        url = (
            "https://oapi.dingtalk.com/robot/send"
            f"?access_token={self.webhook_token}&timestamp={timestamp}&sign={sign}"
        )
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content,
            },
        }
        return self._post(url, payload)
