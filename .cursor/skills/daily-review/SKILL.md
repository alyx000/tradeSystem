---
name: daily-review
description: 协助用户完成每日「八步复盘法」，自动拉取客观数据、引导填写主观判断，并将复盘写入数据库
version: "1.1"
---

# Skill: 每日复盘（八步复盘法）

## 使用场景

当用户说「开始今天的复盘」「帮我复盘」「复盘一下」时，激活此 skill。

## 八步复盘框架

```
第一步：大盘分析   → 客观数据 + 主观判断
第二步：板块梳理   → 客观数据 + 主观判断
第三步：情绪周期定位 → 主观判断
第四步：风格化赚钱效应 → 客观数据 + 主观判断
第五步：龙头/最票识别 → 主观判断
第六步：节点判断   → 主观判断
第七步：持仓检视   → 客观数据 + 主观判断
第八步：次日计划   → 主观判断
```

## 工作流程

## 优先入口

若仓库根目录 `Makefile` 可用，优先使用：

```bash
make review-open DATE=YYYY-MM-DD
make review-prefill DATE=YYYY-MM-DD
make today-close DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

底层 API / `python3 main.py ...` 仍保留，供调试与精细化参数场景使用。

### Step 1：确认复盘日期

```
今日日期是 2026-04-01，复盘对象是今天吗？还是指定其他日期？
```

获取日期（格式 YYYY-MM-DD）后，继续下一步。

### Step 2：拉取客观数据（预填充）

调用 API 获取已采集的行情数据：

```
GET /api/review/{date}/prefill
```

返回数据包含：
- 大盘指数（沪指/深指/创业板）涨跌幅、成交额
- 行业板块涨跌幅排名 TOP 10
- 涨停、跌停、炸板数量
- 溢价率（20cm首板/二板/30cm首板）
- 北向资金净流入

如果 API 未返回数据（行情未采集），提示：
```
⚠️ 今日行情数据尚未采集，请先运行：
   cd /path/to/tradeSystem && make today-close DATE=YYYY-MM-DD
   或直接使用 market-tasks skill 触发采集
```

若用户是要进入复盘工作台本身，而不是只看 JSON，优先使用：

```bash
cd /path/to/tradeSystem
make review-open DATE=YYYY-MM-DD
```

也可以搜索历史笔记作为参考。下面命令里的 `{date}` 是占位符，执行前须替换为实际 `YYYY-MM-DD`（可与当日复盘日期相同，或自行设定起止区间）：

```bash
cd /path/to/tradeSystem
make review-prefill DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

底层等价命令：

```bash
open http://localhost:5173/review/YYYY-MM-DD
curl http://localhost:8000/api/review/YYYY-MM-DD/prefill
cd /path/to/tradeSystem/scripts
python3 main.py db query-notes --keyword "主线" --from YYYY-MM-DD --to YYYY-MM-DD
python3 main.py db db-search --keyword "情绪" --from YYYY-MM-DD --to YYYY-MM-DD
```

### Step 3：逐步引导用户填写（自底向上）

每步展示已有客观数据，然后提问主观判断：

---

**第一步：大盘分析**

```
📊 大盘数据（{date}）：
  上证指数：+1.23%，成交额 8,234亿
  深证成指：+0.87%，成交额 6,102亿
  创业板指：+1.56%，成交额 2,891亿
  5周均线：上方（中期趋势向上）

请问你对今日大盘的主观判断？
  1. 大势方向（主升/震荡/下降）
  2. 整体赚钱效应（强/中/弱）
  3. 是否有需要标记的节点？
```

---

**第二步：板块梳理**

```
📈 板块涨跌幅 TOP 5：
  AI算力：+5.2%
  锂电池：+3.1%
  ...

请问：
  1. 今日主线板块是什么？
  2. 板块处于什么节奏？（启动/主升/分歧/轮动）
  3. 有没有值得关注的新异动板块？
```

---

**第三步：情绪周期**

```
📊 情绪数据：
  涨停板：45只（封板率 78%）
  跌停板：3只
  炸板率：22%
  20cm首板溢价率：+8.2%

请问你判断今日市场情绪处于哪个阶段？
  → 启动 / 发酵 / 高潮 / 分歧 / 衰退
  与上一个节点相比，是加强还是减弱？
```

---

**第四步：风格化赚钱效应**

```
请回顾今日的风格化特征：
  1. 连板股表现如何？
  2. 趋势大票还是小票更容易赚钱？
  3. 情绪面还是基本面主导？
  4. 今日最强的赚钱效应是什么？
```

---

**第五步：龙头/最票识别**

```
今日最强主线的龙头（最票）是谁？
  代码 + 名称，以及它「最」在哪里？
  （走势最引领？最先板？最高标？）
```

---

**第六步：节点判断**

```
今日是否有重要节点？
  □ 大盘节点（突破/压力/止跌）
  □ 板块节点（启动日/首次分歧/高潮日）
  □ 风格切换节点
  □ 日历节点（节假日前/财报季）
```

---

**第七步：持仓检视**

```
📋 当前持仓（来自 DB）：
  [从 holdings-list 输出]

每只持仓：
  1. 今日表现如何？
  2. 是否符合买入时的预期？
  3. 有无调整需要？
```

---

**第八步：次日计划**

```
根据以上复盘，明日的操作计划：
  1. 持仓策略（持有/减仓/清仓/加仓）
  2. 重点关注标的（从关注池中）
  3. 市场整体预期（加强/减弱/观望）
  4. 风险提示
```

### Step 4：写入前先做复盘汇总确认

所有步骤完成后，**先汇总一版结构化复盘摘要给用户确认，再调用 API 保存**。不要把 AI 自己补全的主观判断直接写入数据库。

**强制顺序：**
1. 汇总前 8 步中用户已明确表达的结论
2. 将客观数据与主观判断分开展示
3. 对缺失字段明确标注“待补充”或“未判断”
4. 用户确认后，再调用 `PUT /api/review/{date}`

**禁止 AI 自行补全的字段：**
- 情绪周期阶段
- 主线板块与节奏判断
- 龙头 / 最票归属
- 节点结论
- 次日交易计划
- 主观评分

可参考以下确认模板。建议统一按“类型/动作 + 关键结论 + 待补充项 + 确认语句”的结构展示：

```text
即将保存复盘：
  类型: 每日复盘
  动作: PUT /api/review/{date}
  日期: 2026-04-01
  大势判断: 主升
  主线板块: AI算力（主升）
  情绪阶段: 发酵，较昨日加强
  最票: 海光信息（AI算力走势最引领）
  关键节点: AI主升第三天
  持仓检视: 宁德时代+1.2%，符合预期
  次日计划: 持仓观望，关注AI是否分歧
  待补充: 主观评分
  说明: 未被用户明确表达的主观字段不会自动补全

确认保存？(是/否)
```

### Step 5：汇总并写入数据库

确认后，调用 API 保存：

```
PUT /api/review/{date}
Content-Type: application/json

{
  "date": "2026-04-01",
  "market_summary": {
    "direction": "主升",
    "money_effect": "强"
  },
  "sector_analysis": {
    "main_theme": "AI算力",
    "rhythm": "主升",
    "emerging": "低空经济"
  },
  "emotion_cycle": {
    "stage": "发酵",
    "trend": "加强",
    "limit_up_count": 45,
    "limit_down_count": 3
  },
  "style_analysis": {
    "money_effect_type": "连板",
    "dominant_style": "情绪面小票"
  },
  "top_leaders": [
    {"code": "688041", "name": "海光信息", "reason": "AI算力走势最引领"}
  ],
  "key_nodes": ["AI主升第三天"],
  "holding_review": "宁德时代+1.2%，符合预期",
  "next_day_plan": "持仓观望，关注AI是否分歧",
  "subjective_rating": "B"
}
```

写入成功后，输出确认：
```
✅ 2026-04-01 复盘已保存
   路径: daily/2026-04-01/review.yaml（YAML 同步）
   DB: daily_reviews 表 id=xxx
```

## 与新计划工作台的关系

第一阶段后，`daily-review` 仍然负责八步复盘本身；但第八步“次日计划”会逐步迁移到独立的计划工作台。

新的推荐衔接流程：

```bash
python3 main.py plan draft --date YYYY-MM-DD
python3 main.py plan show-draft --date YYYY-MM-DD
python3 main.py plan confirm --date YYYY-MM-DD
python3 main.py plan diagnose --date YYYY-MM-DD
```

若用户要从复盘直接进入次日计划，请切换或联动 `plan-workbench` skill。

### Step 6：可选 — 生成复盘摘要

如用户需要，生成 Markdown 格式的复盘摘要用于推送：

```markdown
## 📋 2026-04-01 复盘摘要

**大势**：主升，赚钱效应强，成交额 8234亿（上方均量）
**主线**：AI算力主升第3天，封板率 78%，情绪发酵阶段
**最票**：海光信息，走势最引领，AI服务器逻辑最清晰
**次日计划**：持仓观望，AI分歧信号出现前不减仓
```

## 注意事项

- 主观判断类内容（情绪定位、最票识别、交易决策）必须由用户填写，AI 不替代
- AI 可以根据客观数据给出分析参考，但必须标注 `[判断]` 
- 如果用户暂时不想填某一步，可以跳过，后续补充
- 复盘一旦归档（次日开盘后）不再修改
- 写入前应先展示复盘汇总，未被用户明确表达的主观字段不得自动补全

## 依赖的 CLI 命令

均在仓库 `scripts/` 目录下通过 `python3 main.py db …` 调用（见 Step 2 示例）。

- `db query-notes --keyword ... --from ... --to ...`：搜索当日老师观点（`--from`/`--to` 为 `YYYY-MM-DD`）
- `db db-search --keyword ... --from ... --to ...`：跨表搜索相关信息
- `db holdings-list`：获取当前持仓（第七步用）

## 依赖的 API 端点

- `GET /api/review/{date}/prefill`：拉取预填充数据
- `PUT /api/review/{date}`：提交主观判断
