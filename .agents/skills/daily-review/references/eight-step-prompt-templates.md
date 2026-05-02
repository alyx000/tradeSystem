# 八步复盘：分步提问话术模板（附录）

配合 [每日复盘 Skill 速查](../SKILL.md) 使用。下文**粗体块内为可直接套用的提问模板**；其中的行情数字仅为占位示例，**务必用** `GET /api/review/{date}/prefill`（或 `make review-prefill DATE=...` 查看 JSON）**替换为当日真实预填充**。

---

## 八步框架一览

| 步 | 主题 | 客观 / 主观 | step_key | 认知 category（cognitions_by_step） |
|----|------|-------------|----------|-------------------------------------|
| 1 | 大盘分析 | 客观 + 主观 | `step1_market` | `structure`, `macro`, `cycle` |
| 2 | 板块梳理 | 客观 + 主观 | `step2_sectors` | `structure`, `signal` |
| 3 | 情绪周期 | 主观为主 | `step3_emotion` | `sentiment` |
| 4 | 风格化赚钱效应 | 客观 + 主观 | `step4_style` | `structure`, `signal` |
| 5 | 龙头 / 最票 | 主观 | `step5_leaders` | `execution` |
| 6 | 节点判断 | 主观 | `step6_nodes` | `cycle`, `position` |
| 7 | 持仓检视 | 客观 + 主观 | `step7_positions` | `sizing`, `position`, `execution`, `fundamental` |
| 8 | 次日计划 | 主观 | `step8_plan` | `execution`, `synthesis`, `valuation` |

> 认知引用来源：`GET /api/review/{date}/prefill` → `cognitions_by_step.<step_key>`（按 `confidence DESC / instance_count DESC / updated_at DESC / cognition_id ASC` 排序，每步最多 5 条；字段白名单见 `daily-review/SKILL.md` 的「认知联动」章节）。

---

## 1. 确认复盘日期

```
今日日期是 YYYY-MM-DD，复盘对象是今天吗？还是要指定其他日期？
```

取得 `YYYY-MM-DD` 后继续。

---

## 2. 拉取客观数据（预填充）

- API：`GET /api/review/{date}/prefill`
- 便捷：`make review-prefill DATE=YYYY-MM-DD`

预填充通常含：大盘指数与成交额、板块排名、涨跌停与封板率、溢价率、北向等（以实际返回为准）。

若关键行情缺失，提示用户先补采集，例如：

```
⚠️ 今日行情数据可能尚未就绪，请先补跑盘后采集后再复盘，例如：
   make post DATE=YYYY-MM-DD
   或切到 market-tasks skill 按流程触发采集
```

可参考历史笔记与跨表搜索（仓库根目录）：

```bash
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

---

## 3. 八步提问模板（自底向上）

每步先展示**本步相关的预填充客观事实**，再套下面话术问主观判断。每步开始前都先扫一下 `cognitions_by_step.<step_key>`：有相关 active 认知就主动引用并对照事实；没有就不强行引入。通用话术（可作为每步开头的引子）：

```
🧠 相关底层认知（{N} 条）：
  · <title>（<category> · 置信度 XX%，实例 M/验证 V/推翻 I）
  · ...

请结合以上认知判断本步的主观结论；若当日事实与某条认知冲突，直接指出冲突，不要替我取舍。
```

（若 `cognitions_by_step.<step_key>` 为空数组 / 未返回，跳过引子，不要捏造「相关认知」。）

### 第一步：大盘分析

```
📊 大盘数据（{date}）：（此处粘贴预填充中的指数、成交额、均线等摘要）

🧠 相关底层认知：读 cognitions_by_step.step1_market，覆盖 structure / macro / cycle。

请问你对今日大盘的主观判断？
  1. 大势方向（主升 / 震荡 / 下降）
  2. 整体赚钱效应（强 / 中 / 弱）
  3. 是否有需要标记的节点？
  4. 以上结论与「相关底层认知」里的哪些条目一致 / 冲突？
```

### 第二步：板块梳理

```
📈 板块与主线：（此处粘贴预填充中的板块排行 / 主线摘要）

🧠 相关底层认知：读 cognitions_by_step.step2_sectors，覆盖 structure / signal。

请问：
  1. 今日主线板块是什么？
  2. 板块处于什么节奏？（启动 / 主升 / 分歧 / 轮动 等）
  3. 有没有值得关注的新异动板块？
  4. 以上判断是否印证 / 挑战相关底层认知中的节奏识别规则？
```

### 第三步：情绪周期

```
📊 情绪与涨跌停：（此处粘贴涨停家数、封板率、跌停、炸板、溢价等摘要）

🧠 相关底层认知：读 cognitions_by_step.step3_emotion，覆盖 sentiment。

请问你判断今日市场情绪处于哪个阶段？
  → 启动 / 发酵 / 高潮 / 分歧 / 衰退
  与上一个节点相比，是加强还是减弱？
  今日情绪信号是否命中相关底层认知中的阶段识别范式？
```

### 第四步：风格化赚钱效应

```
🧠 相关底层认知：读 cognitions_by_step.step4_style，覆盖 structure / signal（偏风格切换 / 审美偏好）。

请回顾今日的风格化特征：
  1. 连板股表现如何？
  2. 趋势大票还是小票更容易赚钱？
  3. 情绪面还是基本面主导？
  4. 今日最强的赚钱效应是什么？
  5. 当前风格化主导是否与相关底层认知中的审美偏好规则一致？
```

### 第五步：龙头 / 最票识别

**阶段 1：Agent 主动推荐**

进入第 5 步时，Agent 应先读取以下数据，综合分析后输出结构化最票推荐：
- 预填候选中的 `step5_leaders.top_leaders`（情绪龙头 / 容量中军）
- 前 4 步已完成的复盘上下文（板块阶段、情绪周期、风格化偏好）
- `leader_tracking` 中的近期活跃最票历史（如有）
- **`cognitions_by_step.step5_leaders`（覆盖 `execution`）** —— 对照"最票识别"相关的执行类底层认知，对候选进行判断
- 方法论参考：`sector-projection-analysis/references/leader-identification.md`

推荐输出格式：

```
## 最票候选推荐 [判断]

| 板块 | 候选股 | 最的属性 | 清晰度 | 理由 |
|------|--------|----------|--------|------|
| ... | ... | ... | ... | ... |

多因子评估（引领性候选展开）：
- 逻辑正宗度：...
- 辨识度：...
- 风格化匹配：...
- 预期差节点：...
- 新最信号：...

历史对照：
- 前一日最票：... → 当前状态
- 龙头切换信号：...

板块阶段适配：
- 当前阶段 = ... → 应关注 ...
```

**阶段 2：用户确认**

```
以上为系统候选推荐 [判断]，请确认或修改：
  1. 今日最强主线的最票是谁？
     - 代码 + 名称
     - 它「最」在哪个属性？（走势引领 / 最先板 / 最高标 / 容量最大 / 基本面最正宗 / 连板最高）
     - 这个"最"一眼看得出吗？还是需要勉强辨别？
  2. 是否为新最？（之前没当过龙头，今天首次引领）
  3. 如有龙头更替：旧龙头是充分演绎还是意外失手？启动模式？
```

### 第六步：节点判断

```
🧠 相关底层认知：读 cognitions_by_step.step6_nodes，覆盖 cycle / position。

今日是否有重要节点？
  □ 大盘节点（突破 / 压力 / 止跌）
  □ 板块节点（启动日 / 首次分歧 / 高潮日）
  □ 风格切换节点
  □ 日历节点（节假日前 / 财报季等）
  □ 以上节点是否与相关底层认知中的周期 / 位置规则吻合？是否有被推翻 / 印证的条目？
```

### 第七步：持仓检视

```
📋 当前持仓：（此处粘贴 make holdings 或持仓 API 的摘要）

🧠 相关底层认知：读 cognitions_by_step.step7_positions，覆盖 sizing / position / execution / fundamental。

对每只持仓：
  1. 今日表现如何？
  2. 是否符合买入时的预期？
  3. 有无调整需要？
  4. 仓位 / 位置 / 基本面类认知里，是否有条目命中当前持仓或触发警戒线？
```

### 第八步：次日计划

```
🧠 相关底层认知：读 cognitions_by_step.step8_plan，覆盖 execution / synthesis / valuation。

根据以上复盘，明日的操作计划：
  1. 持仓策略（持有 / 减仓 / 清仓 / 加仓）
  2. 重点关注标的（可从关注池）
  3. 市场整体预期（加强 / 减弱 / 观望）
  4. 风险提示
  5. 以上计划是否与执行 / 综合 / 估值类底层认知中的规则一致？
     如果存在潜在冲突，是本次破例？还是需要回头 refine 认知？（refine 本身切到 cognition-evolution 执行）
```

---

## 4. 汇总并写入

用户确认摘要后，通过 Web 复盘工作台保存，或调用：

- `PUT /api/review/{date}` — body 为结构化 JSON，**字段以前端保存格式与 OpenAPI 为准**。

下面是一段**示意**结构（可能与当前前端字段名略有出入，保存前请对照实际 payload）：

```json
{
  "market_summary": { "direction": "主升", "money_effect": "强" },
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
    { "code": "688041", "name": "海光信息", "reason": "AI算力走势最引领" }
  ],
  "key_nodes": ["AI主升第三天"],
  "holding_review": "宁德时代+1.2%，符合预期",
  "next_day_plan": "持仓观望，关注AI是否分歧",
  "subjective_rating": "B"
}
```

写入成功后，可提示用户用 `GET /api/review/{date}` 或工作台再读一次确认。

---

## 5. 可选：复盘摘要 Markdown

如用户需要短摘要用于笔记或推送，可在上述结构基础上生成，例如：

```markdown
## 📋 YYYY-MM-DD 复盘摘要

**大势**：…
**主线**：…
**最票**：…
**次日计划**：…
```

---

## 注意事项

- 主观判断（情绪阶段、最票、买卖倾向）须由用户确认；AI 只辅助整理，不替用户拍板。
- 引用结论时区分 `[事实]`（来自采集 / 预填充）与 `[判断]`（用户或 AI 推理）。
- 用户可暂跳过某步，后续再补；**已归档交易日**的数据勿再改（见项目数据规范）。
- 不要把本流程与 `TradePlan` 确认 / `plan review` 回写混淆（见 Skill 速查中的「禁止事项」）。
- **引用 cognitions_by_step 时严禁执行写操作**（`instance-add` / `validate` / `cognition-refine` 均切到 `cognition-evolution`）；cognitions_by_step 字段本身是**判断体系**，不是当日事实，不要当 `[事实]` 使用。

---

## 命令与 API 速查（与 SKILL 一致）

| 用途 | 入口 |
|------|------|
| 打开工作台 | `make review-open DATE=YYYY-MM-DD` |
| 预填充 JSON | `make review-prefill DATE=YYYY-MM-DD` |
| 老师笔记搜索 | `make notes-search KEYWORD=… FROM=… TO=…` |
| 跨表搜索 | `make db-search KEYWORD=… FROM=… TO=…` |
| 当前持仓 | `make holdings` |
| 预填充 | `GET /api/review/{date}/prefill`（含 `cognitions_by_step`） |
| 读取 / 保存 | `GET` / `PUT /api/review/{date}` |
| 按 category 查全部 active 认知（只读扩展） | `python3 main.py knowledge cognition-list --category <cat> --status active --json`（仅供排查，不在复盘主流程中使用） |
