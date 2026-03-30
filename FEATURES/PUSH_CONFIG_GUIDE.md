# 推送渠道快速配置指南

**日期**: 2026-03-30  
**分支**: `feature/discord-qqbot-push`

---

## 📋 一、配置步骤

### 步骤 1: 复制环境变量模板

```bash
cd /root/.openclaw/workspace/tradeSystem/scripts
cp .env.example .env
```

### 步骤 2: 编辑 .env 文件

```bash
vim .env
```

根据你的需求填入真实的配置：

```bash
# ============================================
# 数据源配置
# ============================================
TUSHARE_TOKEN=your_tushare_token_here

# ============================================
# 推送渠道配置
# ============================================

# Discord Webhook（可选）
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_ALERT=https://discord.com/api/webhooks/xxx

# 企业微信机器人（可选）
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### 步骤 3: 编辑 config.yaml

```bash
vim config.yaml
```

启用你需要的推送渠道：

```yaml
push:
  # Discord 推送
  discord:
    enabled: true  # 改为 true 启用
    channels:
      pre_market: "盘前简报"
      post_market: "盘后报告"
      alerts: "交易告警"

  # QQ Bot 推送（通过 OpenClaw）
  qq:
    enabled: true  # 改为 true 启用
    channels:
      pre_market: "user:openid_xxx"    # 私聊：user:openid
      post_market: "group:group_xxx"   # 群聊：group:group_id
      alerts: "user:openid_xxx"

  # 企业微信推送
  wechat:
    enabled: false  # 改为 true 启用
```

---

## 🔧 二、各渠道详细配置

### 1. Discord Webhook

**获取 Webhook URL**：
1. 打开 Discord 频道
2. 点击频道设置（齿轮图标）
3. 选择「集成」→「Webhooks」
4. 点击「新建 Webhook」
5. 复制 Webhook URL

**配置说明**：
- `DISCORD_WEBHOOK_PRE`：盘前简报推送到此频道
- `DISCORD_WEBHOOK_POST`：盘后报告推送到此频道
- `DISCORD_WEBHOOK_ALERT`：实时告警推送到此频道

**可以为不同频道设置不同的 Webhook**：
```bash
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/pre_market_xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/post_market_xxx
DISCORD_WEBHOOK_ALERT=https://discord.com/api/webhooks/alerts_xxx
```

### 2. QQ Bot（通过 OpenClaw）

**获取用户/群聊 ID**：
- 私聊用户 ID 格式：`user:openid_xxx`
- 群聊 ID 格式：`group:group_xxx`

**配置说明**：
- QQ Bot 通过 OpenClaw `message` 工具推送，无需配置 API Key
- 在 `config.yaml` 的 `qq.channels` 中配置推送目标
- 支持同时向多个用户/群聊推送

**示例**：
```yaml
qq:
  enabled: true
  channels:
    pre_market: "user:ou_15c0f9207a53e709ba623598e5da6e8b"  # 私聊
    post_market: "group:123456789"                         # 群聊
    alerts: "user:ou_15c0f9207a53e709ba623598e5da6e8b"     # 私聊
```

### 3. 企业微信机器人

**获取 Webhook URL**：
1. 打开企业微信
2. 进入「工作台」
3. 找到「机器人」（或添加新机器人）
4. 点击机器人进入详情
5. 复制 Webhook 地址

**配置说明**：
- 只需配置 `WECHAT_WEBHOOK`
- 企业微信支持 Markdown 格式
- 适合办公场景推送

---

## 🧪 三、测试推送

### 方法 1: 运行测试脚本

```bash
cd /root/.openclaw/workspace/tradeSystem
python scripts/tests/test_pushers.py
```

测试脚本会：
- 逐个测试 Discord、QQ Bot、企业微信
- 发送测试文本和 Markdown 消息
- 显示各渠道的推送结果

### 方法 2: 运行盘前简报

```bash
cd /root/.openclaw/workspace/tradeSystem
python scripts/main.py pre --date 2026-03-30
```

这会生成真实的盘前简报并推送到所有启用的渠道。

### 方法 3: 运行检查命令

```bash
python scripts/main.py check
```

这会检查所有数据源和推送渠道的配置状态。

---

## ⚠️ 四、常见问题

### Q1: Discord 推送失败

**可能原因**：
- Webhook URL 错误或已失效
- 网络连接问题
- Discord 服务器限流

**解决方法**：
1. 检查 Webhook URL 是否正确
2. 重新生成 Webhook
3. 查看日志：`tail -f scripts/trade_system.log`

### Q2: QQ Bot 推送失败

**可能原因**：
- OpenClaw `message` 命令不可用
- 用户/群聊 ID 格式错误
- QQ Bot 技能未启用

**解决方法**：
1. 检查 `message` 命令：`which message`
2. 确认 ID 格式：`user:openid_xxx` 或 `group:group_xxx`
3. 检查 OpenClaw QQ Bot 技能配置

### Q3: 企业微信推送失败

**可能原因**：
- Webhook URL 错误
- 机器人已被移除
- 消息内容包含敏感词

**解决方法**：
1. 检查 Webhook URL
2. 重新添加机器人
3. 简化消息内容测试

### Q4: 多渠道推送不一致

**现象**：某个渠道成功，某个渠道失败

**原因**：各渠道独立推送，互不影响

**解决方法**：
1. 查看日志确认失败渠道
2. 单独测试失败渠道
3. 可临时禁用故障渠道

---

## 📊 五、推送效果对比

| 渠道 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| **Discord** | Markdown 支持好、可分频道、富媒体 | 国内访问可能不稳定 | 个人使用、技术讨论 |
| **QQ Bot** | 国内访问快、支持私聊/群聊 | 格式支持有限 | 群聊分享、即时通知 |
| **企业微信** | 办公场景、稳定可靠 | 格式支持一般 | 工作通知、团队协作 |

**推荐配置**：
- **个人用户**：Discord + QQ Bot（私聊）
- **团队使用**：企业微信 + QQ Bot（群聊）
- **全渠道**：三者并行，互为备份

---

## 📝 六、配置示例

### 示例 1: 仅启用 Discord

```bash
# .env
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_ALERT=https://discord.com/api/webhooks/xxx
```

```yaml
# config.yaml
push:
  discord:
    enabled: true
    channels:
      pre_market: "盘前简报"
      post_market: "盘后报告"
      alerts: "交易告警"
  qq:
    enabled: false
  wechat:
    enabled: false
```

### 示例 2: 仅启用 QQ Bot

```yaml
# config.yaml
push:
  discord:
    enabled: false
  qq:
    enabled: true
    channels:
      pre_market: "user:ou_xxx"
      post_market: "group:123456"
      alerts: "user:ou_xxx"
  wechat:
    enabled: false
```

### 示例 3: 全渠道启用

```bash
# .env
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_ALERT=https://discord.com/api/webhooks/xxx
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

```yaml
# config.yaml
push:
  discord:
    enabled: true
  qq:
    enabled: true
    channels:
      pre_market: "user:ou_xxx"
      post_market: "group:123456"
      alerts: "user:ou_xxx"
  wechat:
    enabled: true
```

---

## 🔗 七、相关文档

- [INTEGRATION_PRD.md](../INTEGRATION_PRD.md) - 整合 PRD 文档
- [FEATURES/DISCORD_QQBOT_PRD.md](DISCORD_QQBOT_PRD.md) - 推送能力开发 PRD
- [README.md](../README.md) - 项目说明
- [CLAUDE.md](../CLAUDE.md) - AI 协作规则

---

**最后更新**: 2026-03-30  
**维护者**: OpenClaw Agent
