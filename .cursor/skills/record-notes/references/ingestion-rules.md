# 记录信息详细规则

## 老师观点结构化提炼

对 **Agent / 自动化录入方**（`--input-by` 非 `manual`）：必须先完成**结构化总结**并向用户展示，**经用户确认后**再执行 `db add-note`。不得跳过总结步骤。

### Agent 必选字段（老师观点）

| 参数 | 要求 |
|------|------|
| `--title` | 简短、可检索；禁止口语碎片或「老师观点」等空泛标题 |
| `--core-view` | **必选**，1–3 句核心判断（总括性结论，不得用占位套话） |
| `--key-points` | **必选**，JSON 数组，**至少 2 条、至多 8 条**，每条为独立完整要点 |

### Agent 有条件必选 / 强烈建议

- `--sectors`：只要涉及板块就应填 JSON 数组
- `--tags`：建议填，便于检索
- `--position-advice`：只要谈到仓位/节奏就应填
- `--stocks`：**每次**提炼老师**在跟踪 / 建议持续观察**的标的；仅填材料中**明确出现、可绑定真实证券代码**的项，**勿推断、勿编造代码**。仅名称无代码时列入确认模板「待补代码」，不得猜代码写入。

### 原文与结构化字段的关系

- 长原文（OCR/PDF/转写全文）应写入 `--raw-content-file` 或 stdin（`-`），与上表结构化字段**同时存在**；结构化字段是检索与摘要入口，**不能**用「只写 core-view、不保留原文」代替用户提供的完整材料。

### 人工录入（`--input-by manual`）

仍建议满足上表，但不强制 Agent 确认流程；用户自行承担简略录入的检索代价。

## 长文本入口

短文本可直接用：

```bash
python3 main.py db add-note --raw-content "短文本"
```

PDF / OCR / 超长原文优先用文件：

```bash
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "课件长文提炼" \
  --raw-content-file /tmp/ocr.txt \
  --input-by openclaw
```

若内容来自管道或前一步脚本，走 `stdin`：

```bash
cat /tmp/ocr.txt | python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "课件长文提炼" \
  --raw-content-file - \
  --input-by openclaw
```

`--raw-content` 与 `--raw-content-file` 互斥，不能同时传。

## 附件处理

- 图片或截图先落本地临时文件，再传给 `--attachment`
- CLI 会复制到 `data/attachments/{date}/`
- 回查 API 时应能看到 `attachments` 字段

## 用户确认模板

每次展示须包含 **「笔记摘要」** 与 **「关注池」** 两段；用户对两段均明确表态前，Agent 不得执行带 `--sync-watchlist-from-stocks` 的落库。

```text
即将录入：
  类型: 老师观点
  动作: add-note
  录入方: openclaw
  老师: 小鲍
  日期: 2026-04-01
  标题: AI算力主线仍在，龙头首阴可观察
  核心观点: AI算力主线未结束，分歧后核心股首阴仍有价值
  要点:
  - 主线没有结束，分歧更像换手
  - 龙头首阴优先看承接，不追杂毛
  - 仓位不宜激进，等分歧确认后再加
  涉及板块: AI算力, CPO
  标签: 主线, 首阴, 仓位管理
  跟踪个股（写入 --stocks）:
  - 300750 宁德时代 [tier3_sector]  （若无合格代码则写：无，原因：材料未给出代码 / 仅泛泛举例 等）
  原文: 已保留
  附件: 1 张图片

  --- 关注池 ---
  是否将上述「跟踪个股」中已含代码的标的写入关注池？(是 / 否 / 调整子集或 tier 后说明)
  （若无任何可编码标的：是否确认仅落笔记、不入池？(是/否)）

确认以上内容后录入？(是/否)
```

## 关注池与 CLI 行为

- **默认**：`db add-note` 带 `--stocks` 时只写入 `mentioned_stocks`，终端打印候选与 `WATCHLIST_CANDIDATES`；**不会**自动 `insert` 关注池。
- **用户确认入池后**：在同一条命令追加 **`--sync-watchlist-from-stocks`**，或在已落笔记后执行 **`db watchlist-sync-from-note --note-id <id>`**。
- 已在关注池（非 `removed`）的代码会被跳过，不覆盖原 tier/原因。
- API `POST /api/teacher-notes`：默认不同步关注池；仅当 body 含 **`"sync_watchlist_from_mentions": true`**（且用户已在前端/协作流程中确认）时，在创建笔记后按 `mentioned_stocks` 写入关注池；响应中可能含 `watchlist_sync` 字段。
- 仍可用 [`portfolio-manager`](../../portfolio-manager/SKILL.md) 的 `watchlist-add`（可带 `--source-note-id`）单条补录。
