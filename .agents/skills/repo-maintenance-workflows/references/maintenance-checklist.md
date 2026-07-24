# 仓库维护检查清单

## 0. 范围与工作树基线

1. 读取 `AGENTS.md` 及任务命中的 agent-context / rules。
2. 运行 `git status --short --branch` 与 `git diff --name-only`，记录分支、既有修改和未跟踪文件。
3. 明确任务是只读诊断、Review、巡检，还是已授权修复。
4. 声明允许修改的文件、禁止触碰的范围、验证命令与完成标准。
5. 若目标文件已有改动，先确认能否做不重叠的精准修改；不能安全隔离时停止并向用户说明。

## 1. Diagnose

1. 复现问题或取得等价证据，记录实际错误、输入与环境边界。
2. 从真实入口追踪 `CLI / API / Web → route → service → repo/schema → 写入目标`。
3. 核对默认值、参数校验、状态流转、事务边界、幂等性与失败降级。
4. 区分根因、伴随症状和未验证假设。
5. 默认只报告根因与修复方案；用户明确要求修复后才修改。

## 2. Review

优先检查：

- 行为回归、边界条件和异常路径。
- 状态流转、事务原子性、幂等性和重试语义。
- CLI / API / Web 是否复用同一 service、默认值与校验。
- `fact_checks` / `judgement_checks` 是否混用。
- `missing_data`、`source_failed`、stale 等状态是否被伪装成 pass / fail 或正常空值。
- 写入命令是否显式带 `--input-by`，是否存在错误双写或绕过确认。
- 测试是否覆盖新增行为和高风险边界。

按“严重 / 中等 / 轻微”排序 findings，每条附文件、行号、影响和最小修正方向。没有 finding 时明确说明检查范围与残余风险；不得为凑数制造问题。

## 3. CLI / API / Web / service 对齐

逐项建立对照：

| 维度 | 检查内容 |
|---|---|
| 入口 | 命令、route、页面动作是否指向同一业务能力 |
| 默认值 | 日期、状态、分页、开关和 dry-run 默认值是否一致 |
| 校验 | 必填项、枚举、日期范围、身份校验是否一致 |
| 状态流转 | draft / confirmed / reviewed 等迁移是否合法 |
| 写入 | 是否复用同一 service、事务和写入目标 |
| 审计 | Agent 写入是否要求 `--input-by`，审计字段是否正确传播 |
| 失败语义 | `missing_data`、source failure、stale 与真实空结果是否分离 |
| 返回结构 | CLI JSON、API schema 与 Web 类型是否同步 |

先报告确定不一致和可疑风险；用户只要求检查时不直接修复。

## 4. 每日固定巡检

默认只读，至少覆盖：

- CLI / API / Web 是否仍共用同一 service。
- `plan`、`knowledge`、`ingest` 三条主链路是否发生语义漂移。
- 标准写入命令是否显式带 `--input-by`。
- `plan diagnose` 等缺事实快照场景是否正确降级为 `missing_data`。
- 新增命令、Skill、reference 或入口后，索引和路由是否同步。
- 是否存在缺失测试、缺失状态校验、错误双写或将来源失败伪装成空结果。

输出：

1. 确定问题。
2. 可疑风险。
3. 可机械修复的候选项。
4. 本次覆盖范围与未检查项。

未经明确授权，不实施第 3 类候选修复。

## 5. 文档与索引同步

修改以下文件后检查 `.agents/rules/skills-sync.md`：

- `scripts/main.py`
- `scripts/api/routes/*.py`
- `.agents/skills/**/*.md`

至少核对：

- `.agents/skills/INDEX.md`
- 对应 Skill 与 reference 的唯一真源关系
- `.agents/rules/skills-sync.md`
- 受影响 Skill 的 `agents/openai.yaml`
- `AGENTS.md`、`CLAUDE.md`、`README.md` 是否因外部行为或协作规则变化需要同步
- 涉及命令索引时的 `docs/commands.md` 与 `docs/commands.json`

先运行只读的 `make commands-check`。只有确认命令索引需要更新且任务已授权修改时，才运行 `make commands-doc`。

## 6. 验证与结束检查

1. 运行目标模块的最小有效测试，逻辑改动不得只跑 CLI smoke。
2. 按影响范围运行 `make check-scripts`、`make check-web` 或 `make check`。
3. 实质性代码改动按 `post-dev-review.md` 定档并完成审查（双门时门1 `/code-review` ∥ 门2 adversarial-review 并行，findings 合并处置）；豁免或降档时说明原因。
4. 运行 `git diff --check`。
5. 再次运行 `git status --short --branch`，与初始基线对比。
6. 检查 `data/`、`daily/`、`tracking/` 等运行时路径是否出现非预期修改。
7. 汇报实际执行的命令、通过/失败数量、未执行项及原因。
