# 交易系统 - A股/港股短线交易分析

基于「三位一体」+「四维度短线交易法」体系的交易分析系统。
与 OpenClaw（云服务器）协作共建。

## 目录结构

```
tradeSystem/
├── CLAUDE.md                 # AI协作规则 & 交易体系完整说明
├── README.md                 # 本文件
├── docs/                     # 交易体系理论文档（PDF课件等）
├── templates/                # 模板文件
│   ├── daily-review.yaml     # 八步复盘法模板
│   └── trade-log.yaml        # 交易记录模板
├── config/                   # 配置文件
│   ├── sectors.yaml          # 板块定义与分类
│   ├── styles.yaml           # 风格化指标定义
│   └── calendar.yaml         # 投资日历/财报季
├── daily/                    # 每日复盘数据
│   ├── example/              # 示例数据
│   └── YYYY-MM-DD/           # 按日期存放
│       ├── review.yaml       # 当日复盘
│       └── trades.yaml       # 当日交易记录
├── tracking/                 # 持续跟踪数据
│   ├── main-theme.yaml       # 主线板块跟踪
│   ├── emotion-cycle.yaml    # 情绪周期跟踪
│   └── watchlist.yaml        # 关注票池
└── scripts/                  # 辅助脚本（数据采集等）
```

## 与 OpenClaw 协作流程

### 日常协作方式

1. **盘前**：OpenClaw 提醒日历事件、昨日遗留关注点
2. **盘中**：你输入实时观察，OpenClaw 辅助记录和结构化
3. **盘后复盘**：
   - 你提供原始数据（指数、涨跌停、板块表现等）
   - OpenClaw 按模板生成结构化复盘
   - 你审核并补充主观判断（情绪定性、节点判断等）
4. **交易记录**：每笔交易后记录逻辑，OpenClaw 辅助归类
5. **周末回顾**：汇总本周数据，更新 tracking 文件

### 协作原则

- **你做判断，AI做记录和整理**
- AI 不做具体买卖建议
- 所有主观定性（情绪周期、板块节奏）由你决定
- AI 负责数据一致性检查、历史对比、模式匹配

### Git 协作规范

```bash
# 每日复盘提交
git add daily/2026-03-28/
git commit -m "复盘: 2026-03-28 [简要描述]"

# 更新跟踪数据
git add tracking/
git commit -m "跟踪更新: 2026-03-28"

# 配置变更
git add config/
git commit -m "配置: [变更说明]"
```

## 快速开始

### 1. 初始化 Git 仓库

```bash
cd tradeSystem
git init
git add .
git commit -m "初始化交易系统"
```

### 2. 连接远程仓库（与 OpenClaw 共享）

```bash
# GitHub
git remote add origin git@github.com:你的用户名/tradeSystem.git
git push -u origin main

# 或 Gitee（国内更快）
git remote add origin git@gitee.com:你的用户名/tradeSystem.git
git push -u origin main
```

### 3. 每日复盘

```bash
# 创建当日目录
mkdir -p daily/$(date +%Y-%m-%d)
cp templates/daily-review.yaml daily/$(date +%Y-%m-%d)/review.yaml
cp templates/trade-log.yaml daily/$(date +%Y-%m-%d)/trades.yaml
```

或者直接告诉 OpenClaw："开始今天的复盘"，它会自动创建文件并引导你填写。

## 核心概念速查

| 概念 | 说明 |
|------|------|
| 三位一体 | 大势 + 板块 + 个股，综合判断 |
| 最票 | 个股在板块中某属性下的第一名 |
| 情绪周期 | 启动→发酵→高潮→分歧→衰退→启动 |
| 重点因子 | 当下最影响走势的因子（动态变化） |
| 风格化 | 当前市场审美偏好（大/小盘、趋势/连板等） |
| 诚意反包 | 在人们不相信中走出的反包才有价值 |
| 首阴价值 | 大势+板块初期→有价值；充分演绎→没价值 |
| 节点 | 情绪/板块/大盘的关键转折点 |

## 体系来源

- 三位一体教程
- 四维度短线交易法体系课（第1-26节）
