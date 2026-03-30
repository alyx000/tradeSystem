#!/usr/bin/env python3
"""
推送渠道测试脚本

用法:
    python test_pushers.py
    
功能:
    - 测试 Discord 推送
    - 测试 QQ Bot 推送
    - 测试企业微信推送
    - 测试多渠道并行推送

配置加载:
    - 优先从 ../.env 加载 Discord/企业微信 Webhook URL
    - 从 ../config.yaml 加载 QQ Bot 频道配置
"""

import os
import sys
from pathlib import Path

# 添加脚本目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR / ".env")

# 加载 config.yaml
import yaml
config_path = SCRIPT_DIR / "config.yaml"
test_config = {}
if config_path.exists():
    with open(config_path, "r", encoding="utf-8") as f:
        test_config = yaml.safe_load(f)

from pushers import DiscordPusher, QQBotPusher, WechatPusher, MultiPusher


def test_discord():
    """测试 Discord 推送"""
    print("\n=== 测试 Discord 推送 ===")
    
    # 从 .env 加载配置
    webhook_pre = os.getenv("DISCORD_WEBHOOK_PRE", "")
    webhook_post = os.getenv("DISCORD_WEBHOOK_POST", "")
    webhook_alert = os.getenv("DISCORD_WEBHOOK_ALERT", "")
    
    # 检查是否配置了占位符
    if "your_discord_webhook" in webhook_pre or not webhook_pre:
        print("⚠️  未配置 DISCORD_WEBHOOK_PRE，使用占位符或空值")
    
    pusher = DiscordPusher({
        "webhook_pre": webhook_pre,
        "webhook_post": webhook_post,
        "webhook_alert": webhook_alert,
    })
    
    if not pusher.initialize():
        print("❌ Discord 初始化失败（可能未配置 Webhook）")
        return False
    
    # 测试文本
    ok = pusher.send_text("这是一条测试消息", channel="default")
    print(f"文本推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    # 测试 Markdown（包含表格）
    md_content = """
这是测试表格：

| 项目 | 值 |
|------|-----|
| 测试 1 | 100 |
| 测试 2 | 200 |

这是**粗体**和*斜体*。
"""
    ok = pusher.send_markdown("📊 推送测试", md_content, channel="default")
    print(f"Markdown 推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    return True


def test_qqbot():
    """测试 QQ Bot 推送"""
    print("\n=== 测试 QQ Bot 推送 ===")
    
    # 从 config.yaml 加载 QQ Bot 配置
    qq_config = test_config.get("push", {}).get("qq", {})
    qq_channels = qq_config.get("channels", {})
    
    # 如果没有配置，使用默认测试值
    if not qq_channels:
        print("⚠️  config.yaml 中未配置 QQ Bot 频道，使用默认测试值")
        qq_channels = {"default": "user:openid_xxx"}
    
    pusher = QQBotPusher({
        "channels": qq_channels,
    })
    
    if not pusher.initialize():
        print("❌ QQ Bot 初始化失败（可能未配置频道或 message 命令不可用）")
        return False
    
    # 测试文本
    ok = pusher.send_text("这是一条 QQ Bot 测试消息", channel="default")
    print(f"文本推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    # 测试 Markdown
    md_content = """
这是一条测试消息。

**重点内容**：系统运行正常
"""
    ok = pusher.send_markdown("📊 推送测试", md_content, channel="default")
    print(f"Markdown 推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    return True


def test_wechat():
    """测试企业微信推送"""
    print("\n=== 测试企业微信推送 ===")
    
    # 从 .env 加载配置
    webhook_url = os.getenv("WECHAT_WEBHOOK", "")
    
    # 检查是否配置了占位符
    if "YOUR_KEY" in webhook_url or not webhook_url:
        print("⚠️  未配置 WECHAT_WEBHOOK，使用占位符或空值")
    
    pusher = WechatPusher({
        "webhook_url": webhook_url,
    })
    
    if not pusher.initialize():
        print("❌ 企业微信初始化失败（可能未配置 Webhook）")
        return False
    
    # 测试文本
    ok = pusher.send_text("这是一条企业微信测试消息")
    print(f"文本推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    # 测试 Markdown
    ok = pusher.send_markdown("📊 推送测试", "这是一条测试消息\n\n**重点内容**：系统运行正常")
    print(f"Markdown 推送：{'✅ 成功' if ok else '❌ 失败'}")
    
    return True


def test_multi():
    """测试多渠道并行推送"""
    print("\n=== 测试多渠道并行推送 ===")
    
    multi = MultiPusher()
    
    # 从 .env 加载 Discord 配置
    webhook_pre = os.getenv("DISCORD_WEBHOOK_PRE", "")
    webhook_post = os.getenv("DISCORD_WEBHOOK_POST", "")
    webhook_alert = os.getenv("DISCORD_WEBHOOK_ALERT", "")
    
    # 从 config.yaml 加载 QQ Bot 配置
    qq_config = test_config.get("push", {}).get("qq", {})
    qq_channels = qq_config.get("channels", {})
    if not qq_channels:
        qq_channels = {"default": "user:openid_xxx"}
    
    # 从 .env 加载企业微信配置
    wechat_webhook = os.getenv("WECHAT_WEBHOOK", "")
    
    # 注册所有可用的推送渠道
    discord = DiscordPusher({
        "webhook_pre": webhook_pre,
        "webhook_post": webhook_post,
        "webhook_alert": webhook_alert,
    })
    if discord.initialize():
        multi.register(discord)
        print("✓ 已注册 Discord")
    
    qqbot = QQBotPusher({
        "channels": qq_channels,
    })
    if qqbot.initialize():
        multi.register(qqbot)
        print("✓ 已注册 QQ Bot")
    
    wechat = WechatPusher({
        "webhook_url": wechat_webhook,
    })
    if wechat.initialize():
        multi.register(wechat)
        print("✓ 已注册 企业微信")
    
    if not multi._pushers:
        print("❌ 未注册任何推送渠道")
        return False
    
    # 并行推送
    print("\n开始并行推送...")
    results = multi.send_report("pre_market", "📊 推送系统测试", "这是一条测试报告\n\n系统运行正常")
    
    print("\n推送结果：")
    for channel, ok in results.items():
        status = "✅ 成功" if ok else "❌ 失败"
        print(f"  {channel}: {status}")
    
    return True


def main():
    print("=" * 50)
    print("推送渠道测试工具")
    print("=" * 50)
    
    # 单个渠道测试
    test_discord()
    test_qqbot()
    test_wechat()
    
    # 多渠道测试
    test_multi()
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
