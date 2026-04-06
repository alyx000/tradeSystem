# 仓库维护检查清单

## Debug

1. 读 `AGENTS.md`
2. 找真实入口、service、schema、route、cli
3. 确认默认值、校验、状态流转、写入目标
4. 改最小范围
5. 跑最小验证

## Review

优先找：

- 行为回归
- 状态流转错误
- CLI / API / Web 语义不一致
- `fact_checks` / `judgement_checks` 混用
- `missing_data` 被伪装成 pass / fail
- 缺少必要测试

## 文档与索引同步

修改以下文件后必须检查：

- `scripts/main.py`
- `scripts/api/routes/*.py`
- `.cursor/skills/**/*.md`

同步项：

- `.cursor/skills/INDEX.md`
- `.cursor/rules/skills-sync.mdc`

若涉及命令索引，再检查：

- `docs/commands.md`
- `docs/commands.json`

## 最小命令集

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
make check-scripts
make commands-doc
make commands-check
```

## 默认汇报格式

1. 根因 / findings
2. 实际改动
3. 验证结果
4. 剩余风险
