# teacher_notes v40 受控迁移

仅在任务涉及 `teacher_notes` 来源追溯首次启用、`db backup`、`db migrate`、schema v39→v40 或相关唯一索引修复时读取本文件。

## 授权与前置条件

- 此流程会写备份文件并迁移数据库，必须获得用户明确授权。
- 迁移前停止 API 与所有数据库写入者，确认源库与备份目标路径。
- Agent 只能调用标准 CLI，不得直接修改 SQLite。

## 标准入口

从 `scripts/` 目录执行：

```bash
python3 main.py db backup --output ../data/backups/trade-v39-before-v40.db --input-by codex_automation --json
python3 main.py db migrate --require-backup ../data/backups/trade-v39-before-v40.db --input-by codex_automation --json
```

## 强制门禁

- 备份文件权限必须严格为 `0600`。
- 备份必须来自当前源库，并绑定备份时的规范快照与完整 SHA-256。
- 源库在备份后发生变化、备份来自其他同版本数据库、版本不匹配或索引修复失败时，迁移必须拒绝或回滚。
- 普通 `migrate()`、API 请求和查询命令不得隐式把既有 v39 激活为 v40。

## 验证

1. 核对 backup JSON 回执、备份路径、文件权限与 SHA-256。
2. 核对 migrate JSON 回执、最终 schema 版本与目标 partial unique indexes。
3. 确认失败路径没有留下半迁移状态。
4. 完成验证后再恢复 API 与其他写入者，并在结果中记录操作者、备份路径和迁移状态。
