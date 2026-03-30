"""
QQ Bot 推送
通过 OpenClaw message 工具实现 QQ 消息推送

注意：此推送器依赖 OpenClaw 的 message 命令行工具或 API。
如果 message 命令不可用，推送将失败但不影响其他渠道。
"""
from __future__ import annotations

import os
import logging
import subprocess
from .base import MessagePusher

logger = logging.getLogger(__name__)


class QQBotPusher(MessagePusher):
    name = "qqbot"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.channels = {}
        # 优先使用 openclaw 命令，其次 message 命令
        self._openclaw_cmd = os.getenv("OPENCLAW_CMD", "openclaw")

    def initialize(self) -> bool:
        """
        初始化 QQ Bot 推送渠道
        
        配置格式：
        {
            "channels": {
                "pre_market": "user:openid_xxx" 或 "group:group_xxx",
                "post_market": "user:openid_xxx",
                "alerts": "user:openid_xxx"
            }
        }
        
        注意：QQ Bot 推送是可选的，如果 message 命令不可用，会记录警告但不阻止系统运行
        """
        self.channels = self.config.get("channels", {})
        
        # 检查是否配置了至少一个频道
        if not self.channels:
            logger.info("QQ Bot: 未配置推送频道，跳过初始化")
            return False
        
        # 检查 message 命令是否可用
        try:
            result = subprocess.run(
                ["which", self._openclaw_cmd],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                logger.info(f"QQ Bot: 未找到 '{self._openclaw_cmd}' 命令，跳过 QQ Bot 推送")
                logger.info("提示：如需启用 QQ Bot 推送，请确保 OpenClaw message 工具已安装")
                return False
            self.enabled = True
            logger.info(f"QQ Bot: 已初始化，频道：{list(self.channels.keys())}")
            return True
        except Exception as e:
            logger.info(f"QQ Bot: 初始化检查失败 - {e}，跳过 QQ Bot 推送")
            return False

    def _send_message(self, target: str, content: str) -> bool:
        """
        通过 OpenClaw 发送消息到 QQ Bot
        
        使用 openclaw message send 命令：
        openclaw message send --channel=qqbot --target=<target> -m "<content>"
        
        Args:
            target: 目标用户或群聊，格式 "user:openid" 或 "group:group_id"
                   内部会自动转换为 qqbot:c2c:openid 或 qqbot:group:groupid
            content: 消息内容
        
        Returns:
            bool: 发送是否成功
        """
        if not target:
            logger.error("QQ Bot: 未指定推送目标")
            return False
        
        if not self.enabled:
            logger.warning("QQ Bot: 未启用，跳过发送")
            return False
        
        # 转换目标格式：user:openid → qqbot:c2c:openid, group:id → qqbot:group:id
        qq_target = target
        if target.startswith("user:"):
            qq_target = "qqbot:c2c:" + target[5:]
        elif target.startswith("group:"):
            qq_target = "qqbot:group:" + target[6:]
        
        try:
            # 构建 openclaw message send 命令
            cmd = [
                self._openclaw_cmd,
                "message",
                "send",
                "--channel=qqbot",
                "--target=" + qq_target,
                "-m",
                content
            ]
            
            logger.debug(f"执行命令：{' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(f"QQ Bot 发送失败：{result.stderr}")
                return False
            
            logger.info(f"QQ Bot 发送成功到 {target}")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("QQ Bot 发送超时")
            return False
        except Exception as e:
            logger.error(f"QQ Bot 发送异常：{e}")
            return False

    def send_text(self, text: str, channel: str = "default") -> bool:
        """发送文本消息"""
        target = self.channels.get(channel, self.channels.get("default"))
        if not target:
            logger.warning(f"QQ Bot: 频道 '{channel}' 未配置目标")
            return False
        return self._send_message(target, text)

    def send_markdown(self, title: str, content: str, channel: str = "default") -> bool:
        """
        发送 Markdown 格式消息
        
        QQ Bot 通过 OpenClaw 的 message 工具发送，支持基础 Markdown 格式
        """
        target = self.channels.get(channel, self.channels.get("default"))
        if not target:
            logger.warning(f"QQ Bot: 频道 '{channel}' 未配置目标")
            return False
        
        # 格式化消息（QQ Bot 支持基础 Markdown）
        formatted = f"**📌 {title}**\n\n{content}"
        return self._send_message(target, formatted)

    def send_report(self, report_type: str, title: str, content: str) -> bool:
        """
        发送报告，根据类型选择频道
        
        Args:
            report_type: pre_market / post_market / alert
            title: 报告标题
            content: 报告内容
        
        Returns:
            bool: 发送是否成功
        """
        # 从 channels 配置中获取目标（pre_market/post_market/alerts）
        target = self.channels.get(report_type, self.channels.get("default"))
        if not target:
            logger.warning(f"QQ Bot: 报告类型 '{report_type}' 未配置目标")
            return False
        
        # 格式化消息并发送
        formatted = f"**📌 {title}**\n\n{content}"
        return self._send_message(target, formatted)
