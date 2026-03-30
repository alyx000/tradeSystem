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
        """
        发送 Markdown 格式消息到 Discord
        
        Discord 支持的 Markdown 格式：
        - **粗体**、*斜体*、***粗斜体***
        - __下划线__、~~删除线~~
        - > 引用
        - ```代码块```（支持语法高亮）
        - [链接](url)
        - 无序列表 - / * 、有序列表 1. 2.
        - 表格需用代码块包裹（Discord 不原生支持表格）
        """
        url = self.webhooks.get(channel, self.webhooks.get("default", ""))
        
        # 格式化标题
        formatted = f"**📌 {title}**\n\n"
        
        # 处理内容中的表格（用代码块包裹）
        lines = content.split("\n")
        in_table = False
        table_lines = []
        consecutive_table_rows = 0  # 连续表格行计数器
        
        def is_table_separator(line: str) -> bool:
            """检测是否为表格分隔行（如 |---|---|）"""
            stripped = line.strip()
            return stripped.startswith("|") and "---" in stripped and stripped.endswith("|")
        
        def is_table_row(line: str) -> bool:
            """检测是否为表格行（包含 | 符号且行首为 |）"""
            return "|" in line and line.strip().startswith("|")
        
        def flush_table():
            """将收集的表格行用代码块包裹并添加到 formatted"""
            nonlocal in_table, table_lines, consecutive_table_rows
            if table_lines:
                formatted_lines = ["```\n" + "\n".join(table_lines) + "\n```\n\n"]
                # 使用 nonlocal 修改外部变量需要在返回时处理
                return "".join(formatted_lines)
            return ""
        
        for line in lines:
            # 检测表格分隔行（如 |---|---|）
            if is_table_separator(line):
                if not in_table:
                    in_table = True
                    table_lines = []
                    consecutive_table_rows = 0
                table_lines.append(line)
                consecutive_table_rows = max(consecutive_table_rows, 1)
            # 检测表格行（包含 | 符号且行首为 |）
            elif is_table_row(line):
                if not in_table:
                    # 首次遇到表格行，先暂存，等待确认是否为连续表格
                    in_table = True
                    table_lines = []
                    consecutive_table_rows = 1
                else:
                    consecutive_table_rows += 1
                table_lines.append(line)
            else:
                if in_table:
                    # 表格结束，只有连续多行（>=2）才用代码块包裹
                    if len(table_lines) >= 2 or any(is_table_separator(l) for l in table_lines):
                        formatted += "```\n" + "\n".join(table_lines) + "\n```\n\n"
                    else:
                        # 单行 | 不视为表格，直接添加
                        formatted += "\n".join(table_lines) + "\n"
                    in_table = False
                    table_lines = []
                    consecutive_table_rows = 0
                formatted += line + "\n"
        
        # 处理剩余的表格
        if in_table and table_lines:
            if len(table_lines) >= 2 or any(is_table_separator(l) for l in table_lines):
                formatted += "```\n" + "\n".join(table_lines) + "\n```\n\n"
            else:
                formatted += "\n".join(table_lines) + "\n"
        
        return self._send(url, formatted)
