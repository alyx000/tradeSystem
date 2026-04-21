---
name: record-notes
description: 录入老师观点、行业板块信息、宏观经济信息到 SQLite 数据库；Agent 录入老师观点前必须先做结构化总结并经用户确认，再执行 CLI（支持文字/图片/混合内容，适配 Discord/QQ/微信 channel 场景）
version: "1.5"
---

# Skill: 记录信息（老师观点 / 行业 / 宏观）

## 使用场景

当用户或 channel 消息包含：

- 老师观点、复盘结论、直播要点
- 行业 / 板块动态
- 宏观经济信息
- 图片、OCR、PDF 提取结果

时激活此 skill。

详细字段门槛、确认模板与长文本入口见 [references/ingestion-rules.md](references/ingestion-rules.md)。

## Agent 强制要求（老师观点）

凡由 Agent / 自动化（`--input-by` 非 `manual`）执行 **`db add-note`** 时，**必须先做结构化总结，再经用户确认，最后才调用 CLI**。禁止跳过总结直接落库。

结构化总结须至少包含（并映射到对应 CLI 参数）：

| 内容 | CLI | 要求 |
|------|-----|------|
| 可检索短标题 | `--title` | 禁止空泛词（如仅写「老师观点」） |
| 核心判断 | `--core-view` | 1–3 句，**必选** |
| 独立要点 | `--key-points` | JSON 数组，**至少 2 条、至多 8 条** |
| 涉及板块 | `--sectors` | 有则填 JSON 数组 |
| 检索标签 | `--tags` | 建议填 JSON 数组 |
| 仓位/节奏态度 | `--position-advice` | 有则填 |
| 跟踪个股（写入 `--stocks`） | `--stocks` | **每次**从材料提炼老师**在跟踪 / 建议持续观察**的标的；材料有代码直接填入；**仅有名称无代码时**，Agent 先用 Provider（`get_stock_basic_batch` / `get_stock_basic_list`）查询补全，在确认模板中展示结果供用户验证，确认后写入；多候选由用户选定；查询失败标注「待补代码」；无合格标的时写明「跟踪个股：无（原因）」 |
| 同步到关注池（用户已确认入池后） | `--sync-watchlist-from-stocks` | **可选 flag**：默认只记 `mentioned_stocks` 并打印候选；**仅当**用户对「是否写入关注池」单独确认同意后再加此 flag |

完整原文（含 OCR/PDF 长文）须进 **`--raw-content-file` 或 stdin（`-`）**；若用户提供了长原文，**不得**只写 `core-view` 而不保留原文。

确认流程：先向用户展示与 [ingestion-rules 中的确认模板](references/ingestion-rules.md#用户确认模板) 等价的摘要（**须含「关注池」区块**），**得到用户明示同意后再执行** `db add-note`。禁止在用户未答复「是否入池」前使用 `--sync-watchlist-from-stocks`。

**两步入池（可选）**：若第一次执行 `add-note` 时未带 `--sync-watchlist-from-stocks`，在用户二次确认入池后可执行 `python3 main.py db watchlist-sync-from-note --note-id <笔记id>`（从该笔记的 `mentioned_stocks` 写入关注池）。

行业 / 宏观录入仍须先提炼要点再写入，但字段以各子命令为准（见 ingestion-rules 与同目录说明）。

## 优先入口

查询类优先使用仓库根目录：

```bash
make teachers-open
make notes-search KEYWORD=AI FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=锂电 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

写操作在 `scripts/` 目录执行：

```bash
python3 main.py db add-note ...
# 用户确认入池后（与 add-note 同次或稍后）：
python3 main.py db add-note ... --stocks '[...]' --sync-watchlist-from-stocks
# 或先落笔记再同步：
python3 main.py db watchlist-sync-from-note --note-id <id>
python3 main.py db add-industry ...
python3 main.py db add-macro ...
```

## 核心流程

1. 先判断是老师观点、行业信息还是宏观信息。
2. **老师观点**：按上文「Agent 强制要求」输出结构化总结 → 用户确认 → 再 `db add-note`。
3. 长文本优先使用 `--raw-content-file` 或 `stdin`，不要把超长正文直接塞进命令行参数。
4. 写入后回查结果，并保留附件与原文路径。

## 禁止事项

- **禁止**在未展示结构化总结、未获用户确认的情况下执行 `db add-note`（Agent 场景）。
- **禁止**在用户未对「关注池」区块明确同意前使用 `--sync-watchlist-from-stocks`。
- 不要把老师原话未经提炼直接原样当「唯一正文」落库（原文可进 `raw-content`，但必须有 `core-view` + `key-points` 等结构化字段）。
- 不要把截图 / OCR 长文本直接塞进 `--raw-content` 导致命令行溢出。
- 不要猜测标题、要点、标签或个股代码。
- 不要把老师观点写到 `knowledge_assets`。

## 最小验证

- `db add-note` 成功后，用 `query-notes` 或 API 回查目标标题 / 老师 / 日期。
- 带附件时确认输出里有附件数量，并能从 API 看到 `attachments`。
- 长文本场景优先验证 `--raw-content-file` 或 `--raw-content-file -` 能正确落库。

## 切换条件

- 若用户要把老师观点或资料继续转成计划草稿，切到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)。
- 若用户要从老师观点继续**提炼 / 落库 / 精炼交易认知**，切到 [`cognition-evolution/SKILL.md`](../cognition-evolution/SKILL.md)；认知候选质量、适用边界、失效边界与 refine 流程都在那里约束。
- 若用户其实要管理关注池 / 持仓，切到 [`portfolio-manager/SKILL.md`](../portfolio-manager/SKILL.md)。
- 若 CLI / API 写入语义异常，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已写入的信息类型与对象
2. 结构化摘要与附件 / 原文说明
3. 验证结果
4. 剩余风险或待确认项
