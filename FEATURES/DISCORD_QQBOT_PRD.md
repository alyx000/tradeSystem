# Discord & QQBot 推送能力开发 PRD

**版本**: v1.0  
**日期**: 2026-03-30  
**分支**: `feature/discord-qqbot-push`

---

## 📋 一、目标

为 tradeSystem 添加 Discord 和 QQ Bot 双渠道推送能力，实现：
1. **Discord 推送**：复用现有 webhook 架构，优化 Markdown 格式支持
2. **QQ Bot 推送**：通过 OpenClaw QQ Bot 技能集成，支持文本和富媒体推送
3. **统一推送管理**：通过 `MultiPusher` 统一管理多渠道

---

## 🎯 二、核心需求

### 2.1 Discord 推送（增强现有）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| Webhook 推送 | 复用现有 `DiscordPusher` | P0 |
| Markdown 优化 | 支持代码块、表格、引用等格式 | P1 |
| 分频道推送 | 盘前/盘后/告警推送到不同频道 | P0 |
| 长文本分段 | 自动分割超过 2000 字符的消息 | P0 |

### 2.2 QQ Bot 推送（新增）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 文本推送 | 通过 OpenClaw `message` 工具发送 | P0 |
| 富媒体推送 | 支持图片、文件（通过 `<qqmedia>` 标签） | P1 |
| 推送渠道 | 支持私聊和群聊 | P0 |
| 定时推送 | 集成 `qqbot-cron` 技能 | P2 |

### 2.3 统一配置

| 配置项 | Discord | QQ Bot |
|--------|---------|--------|
| 启用开关 | `push.discord.enabled` | `push.qq.enabled` |
| 认证信息 | Webhook URL | Bot API + 用户/群 ID |
| 频道映射 | `channels.pre_market` 等 | `channels.pre_market` 等 |

---

## 📦 三、技术方案

### 3.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    main.py                               │
│  setup_pushers() 初始化所有推送渠道                      │
└─────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────┐
│                    MultiPusher                           │
│  统一管理 Discord / QQ Bot / Wechat 等多个推送渠道        │
└─────────────────────────────────────────────────────────┘
              ↓                    ↓
    ┌─────────────────┐    ┌─────────────────┐
    │ DiscordPusher   │    │ QQBotPusher     │
    │ (已有，增强)    │    │ (新增)          │
    └─────────────────┘    └─────────────────┘
```

### 3.2 QQBotPusher 实现方案

**核心思路**：QQ Bot 推送通过 OpenClaw 的 `message` 工具实现，而非直接调用 QQ Bot API。

**原因**：
1. OpenClaw 已封装好 QQ Bot 技能（`qqbot-media`、`qqbot-cron`）
2. 避免重复造轮子，复用现有能力
3. 通过 `message` 工具可自动处理富媒体、会话路由等复杂逻辑

**实现方式**：
- `QQBotPusher` 调用 `message` 工具（通过 `exec` 或 `sessions_send`）
- 文本消息：直接发送
- 富媒体消息：生成临时文件 → 用 `<qqmedia>` 标签包裹

### 3.3 配置文件更新

```yaml
# scripts/config.yaml
push:
  discord:
    enabled: true
    webhook_pre: ""  # 在 .env 中配置
    webhook_post: ""
    webhook_alert: ""
    channels:
      pre_market: "盘前简报"
      post_market: "盘后报告"
      alerts: "交易告警"

  qq:
    enabled: true
    channels:
      pre_market: "user:openid_xxx"  # 私聊
      post_market: "group:group_xxx"  # 群聊
      alerts: "user:openid_xxx"

  wechat:
    enabled: false
    webhook_url: ""
```

---

## 🚀 四、开发任务

### Phase 1: Discord 推送增强（1 小时）

- [ ] **任务 1.1**：增强 `DiscordPusher.send_markdown()` 支持更好格式
  - 表格用代码块包裹
  - 标题用 `**粗体**`
  - 引用用 `>`
- [ ] **任务 1.2**：更新 `.env.example` 添加 Discord 配置说明
- [ ] **任务 1.3**：测试推送（可用 mock 数据）

### Phase 2: QQBotPusher 实现（2 小时）

- [ ] **任务 2.1**：创建 `scripts/pushers/qqbot_pusher.py`
  - 继承 `MessagePusher` 基类
  - 实现 `send_text()` 和 `send_markdown()`
  - 通过 `exec` 调用 `message send --channel=qqbot`
- [ ] **任务 2.2**：更新 `scripts/main.py` 的 `setup_pushers()` 注册 QQ Bot
- [ ] **任务 2.3**：更新 `scripts/config.yaml` 添加 QQ Bot 配置
- [ ] **任务 2.4**：更新 `.env.example` 添加 QQ Bot 配置说明

### Phase 3: 集成测试（1 小时）

- [ ] **任务 3.1**：运行 `python main.py check` 检查推送配置
- [ ] **任务 3.2**：模拟推送测试（可用 `--dry-run` 模式）
- [ ] **任务 3.3**：实际推送测试（盘前/盘后报告）

### Phase 4: 文档更新（0.5 小时）

- [ ] **任务 4.1**：更新 `README.md` 添加推送配置说明
- [ ] **任务 4.2**：更新 `CLAUDE.md` 添加推送渠道说明

---

## ⚠️ 五、风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| QQ Bot 技能调用失败 | 推送中断 | 降级到 Discord/微信单渠道 |
| Discord Webhook 限流 | 消息丢失 | 添加重试机制 + 失败日志 |
| 富媒体文件过大 | QQ Bot 拒绝 | 限制文件大小 < 10MB，超限转链接 |
| 多渠道推送不一致 | 用户体验差 | 统一消息格式，同步推送 |

---

## ✅ 六、验收标准

### Discord 推送验收
- [ ] `python main.py pre` 成功推送到 Discord
- [ ] 消息格式正确（标题、表格、代码块）
- [ ] 长文本自动分段
- [ ] 失败时有日志记录

### QQ Bot 推送验收
- [ ] `python main.py pre` 成功推送到 QQ（私聊或群聊）
- [ ] 文本消息正常显示
- [ ] 富媒体（如有）正确发送
- [ ] 失败时有日志记录

### 统一推送验收
- [ ] 可同时向 Discord + QQ Bot 推送
- [ ] 单一渠道失败不影响其他渠道
- [ ] 配置灵活（可单独启用/禁用各渠道）

---

## 📝 七、下一步行动

**立即执行**（Phase 1）：

```bash
cd /root/.openclaw/workspace/tradeSystem
git checkout -b feature/discord-qqbot-push

# 1. 增强 DiscordPusher
vim scripts/pushers/discord_pusher.py

# 2. 创建 QQBotPusher
vim scripts/pushers/qqbot_pusher.py

# 3. 更新 main.py 注册新推送渠道
vim scripts/main.py

# 4. 更新配置文件
vim scripts/config.yaml
vim scripts/.env.example
```

---

**文档版本**: v1.0  
**最后更新**: 2026-03-30  
**维护者**: OpenClaw Agent
