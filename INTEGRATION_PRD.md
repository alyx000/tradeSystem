# tradeSystem 整合 PRD 文档

**版本**: v1.0  
**日期**: 2026-03-29  
**目标**: 将 tradeSystem 与现有 daily-review-web + Obsidian 系统无缝整合

---

## 📋 一、现状分析

### 1.1 现有系统架构

```
┌────────────────────────────────────────────────────────────┐
│                    用户交互层                               │
│  ┌─────────────────┐         ┌─────────────────────────┐   │
│  │ daily-review-web│         │ Obsidian Vault          │   │
│  │ (Vue3 + Flask)  │ ──────→ │ (Markdown 存储)         │   │
│  │ - 复盘录入       │         │ - /daily-reviews/       │   │
│  │ - 市场数据展示   │         │ - /teacher-notes/       │   │
│  │ - 老师观点记录   │         │ - /industry-info/       │   │
│  └─────────────────┘         └─────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### 1.2 tradeSystem 架构

```
┌────────────────────────────────────────────────────────────┐
│                    tradeSystem                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ scripts/                                             │   │
│  │ ├── collectors/   # 数据采集（市场/持仓）            │   │
│  │ ├── providers/    # 数据源（Tushare/Akshare）        │   │
│  │ ├── generators/   # 报告生成（Markdown + YAML）      │   │
│  │ └── pushers/      # 推送（Discord/微信）             │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ templates/                                           │   │
│  │ ├── daily-review.yaml  # 八步复盘法模板              │   │
│  │ └── trade-log.yaml     # 交易记录模板               │   │
│  └─────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ daily/                                               │   │
│  │ └── YYYY-MM-DD/                                      │   │
│  │     ├── pre-market.yaml   # 盘前简报                 │   │
│  │     ├── post-market.yaml  # 盘后报告                 │   │
│  │     ├── review.yaml       # 八步复盘（主观填写）     │   │
│  │     └── trades.yaml       # 交易记录                 │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

### 1.3 核心差异对比

| 维度 | daily-review-web | tradeSystem |
|------|-----------------|-------------|
| **数据格式** | Markdown | YAML + Markdown |
| **复盘框架** | 自由格式 | 八步复盘法（结构化） |
| **数据采集** | Tushare API（手动触发） | Tushare/Akshare（自动采集） |
| **推送渠道** | 本地存储 | Discord/微信/QQ |
| **老师观点** | ✅ 已支持 | ❌ 待实现 |
| **交易记录** | ❌ 无 | ✅ 已实现 |
| **持仓管理** | ❌ 无 | ✅ 已实现 |

---

## 🎯 二、整合目标

### 2.1 核心原则

1. **保留用户习惯**：Obsidian 知识库 + Web UI 交互不变
2. **增强数据结构**：引入 YAML 结构化存储，支持量化分析
3. **自动化升级**：盘前/盘后自动采集 + 推送
4. **理论体系落地**：三位一体 + 四维度 + 八步复盘法

### 2.2 整合后架构

```
┌────────────────────────────────────────────────────────────┐
│                    用户交互层                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ daily-review-web (增强版)                           │   │
│  │ - 支持 YAML 编辑 + Markdown 预览                      │   │
│  │ - 八步复盘法表单引导                                │   │
│  │ - 交易记录录入                                      │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
                              ↑ ↓
┌────────────────────────────────────────────────────────────┐
│                    数据同步层                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ sync_engine.py                                       │   │
│  │ - YAML ←→ Markdown 双向转换                          │   │
│  │ - tradeSystem/daily/ ←→ obsidian-vault/ 同步        │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
                              ↑ ↓
┌────────────────────────────────────────────────────────────┐
│                    数据采集层                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ tradeSystem/scripts/                                 │   │
│  │ - 盘前简报 (07:00 自动)                               │   │
│  │ - 盘后报告 (20:00 自动，含晚间任务)                    │   │
│  │ - 推送 Discord/微信                                   │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

---

## 📦 三、Phase 1: 数据格式对齐（优先级：高）

### 3.1 目标

实现 tradeSystem 的 YAML 数据自动转换为 Obsidian Markdown，保持现有知识库可读性。

### 3.2 交付物

| 文件 | 路径 | 功能 |
|------|------|------|
| `obsidian_export.py` | `scripts/generators/` | YAML → Markdown 转换器 |
| `obsidian_sync.py` | `scripts/utils/` | 文件同步工具 |
| `sync_config.yaml` | `config/` | 同步配置 |

### 3.3 数据映射规则

#### 3.3.1 盘后报告 → Obsidian

```yaml
# tradeSystem/daily/2026-03-29/post-market.yaml
indices:
  shanghai:
    close: 3400
    change_pct: 1.5
```

↓ 转换为 ↓

```markdown
# 2026-03-29 盘后数据

## 指数表现
| 指数 | 收盘 | 涨跌幅 |
|------|------|--------|
| 上证指数 | 3400 | +1.5% |
```

#### 3.3.2 八步复盘 → Obsidian

```yaml
# tradeSystem/daily/2026-03-29/review.yaml
market_overview:
  direction:
    trend: "主升"
    ma5w_position: "5 周均线上"
```

↓ 转换为 ↓

```markdown
# 2026-03-29 复盘

## 一、大盘分析
- **趋势**: 主升
- **5 周均线**: 上方
```

### 3.4 实现代码骨架

```python
# scripts/generators/obsidian_export.py
"""
YAML → Obsidian Markdown 导出器
"""
import yaml
from pathlib import Path
from datetime import datetime

class ObsidianExporter:
    def __init__(self, tradesystem_dir: str, obsidian_dir: str):
        self.ts_dir = Path(tradesystem_dir)
        self.ob_dir = Path(obsidian_dir)
    
    def export_daily_review(self, date: str) -> str:
        """导出单日复盘到 Obsidian"""
        # 读取 YAML
        yaml_path = self.ts_dir / "daily" / date / "review.yaml"
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        # 生成 Markdown
        md_lines = [
            f"# {date} 复盘笔记",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 一、大盘分析",
            f"- **趋势**: {data['market_overview']['direction']['trend']}",
            # ... 更多字段
        ]
        
        # 写入 Obsidian
        md_path = self.ob_dir / "daily-reviews" / "2026" / f"{date}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_lines))
        
        return str(md_path)
    
    def export_post_market(self, date: str) -> str:
        """导出盘后数据到 Obsidian"""
        # 类似实现
        pass
```

---

## 📦 四、Phase 2: 老师观点整合（优先级：中）

### 4.1 目标

在 tradeSystem 中增加老师观点模块，复用现有 daily-review-web 的 Obsidian 存储逻辑。

### 4.2 交付物

| 文件 | 路径 | 功能 |
|------|------|------|
| `teacher_collector.py` | `scripts/collectors/` | 老师观点采集器 |
| `teacher_notes.yaml` | `tracking/` | 老师观点跟踪 |
| `teacher_bp.py` | `backend/routes/` | Flask API 路由（复用现有） |

### 4.3 数据结构

```yaml
# tracking/teacher-notes.yaml
teachers:
  - name: "边风炜"
    platform: "娓娓道来"
    schedule: "每周三/六下午"
    last_update: "2026-03-29"
    notes:
      - date: "2026-03-29"
        title: "医药财报点评（恒瑞医药）"
        core_views:
          - "创新药是今年要重点关注的行业"
          - "2500 亿是下限，往下空间被封杀"
        obsidian_path: "obsidian-vault/teacher-notes/2026/teacher-note-bianfengwei-2026-03-29.md"
```

### 4.4 整合方案

**方案 A：完全复用现有逻辑**
- 保留 daily-review-web 的 `obsidian-review-storage` 技能
- tradeSystem 调用现有 Python 脚本存储

**方案 B：新建 tradeSystem 模块**
- 在 `scripts/collectors/` 新增 `teacher_collector.py`
- 直接写入 Obsidian Vault

**推荐：方案 A**（减少重复代码）

---

## 📦 五、Phase 3: Web UI 适配（优先级：中）

### 5.1 目标

daily-review-web 支持读取和编辑 tradeSystem 的 YAML 数据。

### 5.2 交付物

| 文件 | 路径 | 功能 |
|------|------|------|
| `yaml_api.py` | `backend/routes/` | YAML 数据 API |
| `EightStepsForm.vue` | `frontend/components/` | 八步复盘表单组件 |
| `trade_log.py` | `backend/services/` | 交易记录服务 |

### 5.3 API 接口设计

```python
# backend/routes/yaml_api.py
from flask import Blueprint, jsonify, request
import yaml

yaml_bp = Blueprint('yaml', __name__, url_prefix='/api/yaml')

@yaml_bp.route('/review/<date>', methods=['GET'])
def get_review(date):
    """获取指定日期的复盘 YAML"""
    yaml_path = f"/root/.openclaw/workspace/tradeSystem/daily/{date}/review.yaml"
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return jsonify({"status": "success", "data": data})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "文件不存在"}), 404

@yaml_bp.route('/review/<date>', methods=['PUT'])
def update_review(date):
    """更新复盘 YAML"""
    yaml_path = f"/root/.openclaw/workspace/tradeSystem/daily/{date}/review.yaml"
    data = request.json
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True)
    return jsonify({"status": "success"})
```

### 5.4 前端组件

```vue
<!-- frontend/components/EightStepsForm.vue -->
<template>
  <div class="eight-steps-form">
    <!-- 第一步：大盘分析 -->
    <section>
      <h3>一、大盘分析</h3>
      <select v-model="form.market_overview.direction.trend">
        <option value="主升">主升</option>
        <option value="震荡">震荡</option>
        <option value="下降">下降</option>
      </select>
    </section>
    
    <!-- 第二步：板块梳理 -->
    <!-- ... -->
    
    <!-- 保存按钮 -->
    <button @click="saveReview">💾 保存复盘</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const form = ref({
  market_overview: {
    direction: { trend: '' }
  }
})

const saveReview = async () => {
  await fetch(`/api/yaml/review/${date.value}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form.value)
  })
}
</script>
```

---

## 📦 六、Phase 4: 自动采集配置（优先级：高）

### 6.1 目标

配置 tradeSystem 的定时任务，实现盘前/盘后自动采集 + 推送。

### 6.2 交付物

| 文件 | 路径 | 功能 |
|------|------|------|
| `.env` | `scripts/` | 环境变量配置 |
| `config.yaml` | `scripts/` | 数据源配置 |
| `systemd/tradesystem.service` | `deploy/` | Systemd 服务配置 |

### 6.3 配置步骤

**步骤 1：环境变量**
```bash
# scripts/.env
TUSHARE_TOKEN=your_token_here
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/xxx
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/xxx
```

**步骤 2：数据源配置**
```yaml
# scripts/config.yaml
providers:
  tushare:
    enabled: true
    priority: 1
  akshare:
    enabled: true
    priority: 2

push:
  discord:
    enabled: true
    channels:
      pre_market: "盘前简报"
      post_market: "盘后报告"
  wechat:
    enabled: false
```

**步骤 3：定时任务**
```bash
# 使用 systemd 服务
sudo systemctl enable tradesystem
sudo systemctl start tradesystem
```

---

## 📊 七、数据流完整示意

### 7.1 盘前流程（07:00）

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│ APScheduler │ →  │ MarketCollector │ → │ ReportGenerator │
│ 定时触发     │    │ 采集外盘/商品  │    │ 生成 Markdown   │
└─────────────┘    └──────────────┘    └─────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ MultiPusher     │
                                    │ → Discord/微信  │
                                    └─────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ ObsidianExporter│
                                    │ → Markdown 同步  │
                                    └─────────────────┘
```

### 7.2 盘后流程（20:00，`main.py post`）

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│ APScheduler │ →  │ MarketCollector │ → │ ReportGenerator │
│ 定时触发     │    │ 采集指数/涨停  │    │ 生成 Markdown   │
└─────────────┘    └──────────────┘    └─────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ 用户填写 review.yaml │
                                    │ (八步复盘法)     │
                                    └─────────────────┘
                                              ↓
                                    ┌─────────────────┐
                                    │ ObsidianExporter│
                                    │ → Markdown 同步  │
                                    └─────────────────┘
```

---

## 🚀 八、实施时间表

| Phase | 任务 | 预计工时 | 依赖 |
|-------|------|---------|------|
| **Phase 1** | YAML → Markdown 转换器 | 2 小时 | 无 |
| **Phase 2** | 老师观点整合 | 1 小时 | Phase 1 |
| **Phase 3** | Web UI 适配 | 4 小时 | Phase 1 |
| **Phase 4** | 自动采集配置 | 2 小时 | 无 |
| **Phase 5** | 测试与优化 | 2 小时 | 全部 |

**总计**: 约 11 小时

---

## ⚠️ 九、风险与应对

| 风险 | 影响 | 应对方案 |
|------|------|---------|
| YAML 与 Markdown 同步冲突 | 数据不一致 | 单向同步（YAML → MD），MD 仅用于阅读 |
| Tushare 接口限流 | 采集失败 | 自动降级到 Akshare |
| Web UI 表单复杂度高 | 用户体验下降 | 保留现有 Markdown 编辑器作为备选 |
| 定时任务失败 | 报告未推送 | 添加失败告警 + 手动重跑脚本 |

---

## ✅ 十、验收标准

### Phase 1 验收
- [ ] `python scripts/generators/obsidian_export.py 2026-03-29` 成功生成 Markdown
- [ ] Obsidian 中可查看格式化后的复盘笔记
- [ ] 中文编码正确，无乱码

### Phase 2 验收
- [ ] 边风炜等老师观点可录入 tradeSystem
- [ ] 观点自动同步到 Obsidian `teacher-notes/`
- [ ] daily-review-web 可查询老师观点

### Phase 3 验收
- [ ] Web UI 可编辑 `review.yaml`
- [ ] 保存后 Obsidian 同步更新
- [ ] 交易记录可录入和查看

### Phase 4 验收
- [ ] 盘前简报 07:00 自动推送
- [ ] 盘后报告 20:00 自动推送（`post` 含溢价/关注池/复盘导出与全日盘后）
- [ ] Discord/微信可收到报告

---

## 📝 十一、下一步行动

**立即执行**（Phase 1）：

```bash
# 1. 创建导出器脚本
cd /root/.openclaw/workspace/tradeSystem
mkdir -p scripts/generators
vim scripts/generators/obsidian_export.py

# 2. 测试运行
python scripts/generators/obsidian_export.py --date 2026-03-29

# 3. 验证 Obsidian 输出
cat /root/.openclaw/workspace/obsidian-vault/daily-reviews/2026/2026-03-29.md
```

---

**文档版本**: v1.0  
**最后更新**: 2026-03-29  
**维护者**: OpenClaw Agent
