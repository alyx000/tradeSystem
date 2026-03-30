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
"""

import sys
from pathlib import Path

# 添加脚本目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from pushers import DiscordPusher, QQBotPusher, WechatPusher, MultiPusher


def test_discord():
    """测试 Discord 推送"""
    print("\n=== 测试 Discord 推送 ===")
    
    pusher = DiscordPusher({
        "webhook_pre": "",  # 填入真实 Webhook URL
        "webhook_post": "",
        "webhook_alert": "",
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
    
    pusher = QQBotPusher({
        "channels": {
            "default": "user:openid_xxx",  # 填入真实用户 openid
        }
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
    
    pusher = WechatPusher({
        "webhook_url": "",  # 填入真实 Webhook URL
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
    
    # 注册所有可用的推送渠道
    discord = DiscordPusher({
        "webhook_pre": "",
        "webhook_post": "",
        "webhook_alert": "",
    })
    if discord.initialize():
        multi.register(discord)
        print("✓ 已注册 Discord")
    
    qqbot = QQBotPusher({
        "channels": {"default": "user:openid_xxx"}
    })
    if qqbot.initialize():
        multi.register(qqbot)
        print("✓ 已注册 QQ Bot")
    
    wechat = WechatPusher({
        "webhook_url": "",
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
