# 资料分流规则

## 路由总表

| 输入类型 | 事实源 | 写入入口 | 草稿入口 |
|---------|--------|---------|---------|
| 老师观点 | `teacher_notes` | `db add-note` / `POST /api/teacher-notes` | `knowledge draft-from-teacher-note` / `POST /api/knowledge/teacher-notes/{note_id}/draft` |
| 普通资料 | `knowledge_assets` | `knowledge add-note` / `POST /api/knowledge/assets` | `knowledge draft-from-asset` / `POST /api/knowledge/assets/{asset_id}/draft` |

## 明确禁止

- 不要用 `knowledge add-note` 写老师观点。
- 不要新建 `asset_type=teacher_note` 或 `asset_type=course_note`。
- 不要把草稿直接升级成正式 `TradePlan`。

## 常见判断

### 属于老师观点

- 明确来自某位老师、直播、复盘、语音整理
- 内容是结构化主观看法、节奏判断、交易观察

这类先走 `record-notes`。

### 属于普通资料

- 新闻、公告、课程摘要、人工资料整理
- 更偏事实、主题、事件、材料

这类走 `knowledge add-note`。

## 遗留数据说明

- 遗留 `knowledge_assets.asset_type=teacher_note` 不应继续用于 `draft-from-asset`
- 遗留 `course_note` 可继续被读取，但不应再新建

## 最小验证建议

1. 写入后先 `knowledge list` 或查老师笔记 API，确认落到了正确表。
2. 再执行对应的 draft 命令。
3. 若 draft 失败，先排查来源是否走错，再排查 service / API。
