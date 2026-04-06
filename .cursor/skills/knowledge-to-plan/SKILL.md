---
name: knowledge-to-plan
description: 从课程笔记、新闻资料等知识资产触发 MarketObservation 和 TradeDraft；老师观点走 teacher_notes 唯一事实源
version: "0.2"
---

# Skill: 资料提炼到计划

## 使用场景

当用户说：

- 「把这条**新闻/课程/手动笔记**转成计划草稿」
- 「从资料里提炼一下交易线索」
- 「从这篇笔记生成 observation」

时激活此 skill。

若用户明确是**某位老师的结构化观点**（复盘、直播要点），应走 **record-notes** / `db add-note` 写入 `teacher_notes`，再用本 skill 的 **从老师笔记生成草稿** 路径，**不要**用 `knowledge add-note` 冒充老师观点。

## 当前标准 CLI

```bash
make knowledge-open
make knowledge-list
make knowledge-add-note
make knowledge-draft-from-asset ASSET_ID=asset_xxx
make knowledge-draft-from-teacher-note NOTE_ID=42 DATE=2026-04-10
python3 main.py knowledge add-note
python3 main.py knowledge list
python3 main.py knowledge draft-from-asset --asset-id asset_xxx
python3 main.py knowledge draft-from-teacher-note --note-id 42 --date 2026-04-10
```

老师观点录入（唯一事实源）：

```bash
cd scripts && python3 main.py db add-note --teacher "小鲍" --date 2026-04-01 --title "..." --input-by openclaw
```

若需要结构化输出，附加：

```bash
--json
```

说明：

- 若用户是要进入 Web 资料工作台本身，优先使用 `make knowledge-open`
- **老师观点**：Web 资料工作台选「老师观点」或 CLI `db add-note`，数据在 `teacher_notes`
- **其它资料**：`knowledge add-note`（`news_note` / `course_note` / `manual_note`），数据在 `knowledge_assets`
- `knowledge add-note` **不再接受** `--asset-type teacher_note`（已移除）；`POST /api/knowledge/assets` 若带 `asset_type=teacher_note` 返回 **422**；误用 CLI/API 会提示改用 `db add-note` / `teacher-notes`
- 若需要补充底层参数，再退回 `python3 main.py knowledge ...`

## 协作规则

- **非老师类**资料先进入 `knowledge_assets`，再由 `draft-from-asset` 触发 `MarketObservation(source_type=knowledge_asset)` 与 `TradeDraft`
- **老师观点**只存 `teacher_notes`，由 `draft-from-teacher-note`（CLI 或 `POST /api/knowledge/teacher-notes/{note_id}/draft`）触发 `MarketObservation(source_type=teacher_note)` 与 `TradeDraft`，`source_refs` 含 `teacher_note_id`
- Agent 可触发 observation，但不得跳过人工确认直接生成正式计划

## 当前能力

- `knowledge_assets`：`add-note` / `list` / `draft-from-asset`；**禁止**新建 `asset_type=teacher_note`（服务层与 API 422）；列表 API **不返回** `asset_type=teacher_note`；库内遗留 `teacher_note` 行**不可**用 `draft-from-asset`，应迁移到 `teacher_notes` 后用 `teacher-notes/.../draft`
- `teacher_notes`：`db add-note`；`draft-from-teacher-note` / 上述 API
- Web 资料工作台：合并展示老师笔记与其它资料；老师观点录入走 `teacher-notes` API；列表支持删除（笔记与资产分别调用对应 DELETE）

当前限制：

- 资料提炼先走规则抽取，不依赖 LLM
- 生成的检查项仍停留在 draft 候选层，正式计划需人工确认
