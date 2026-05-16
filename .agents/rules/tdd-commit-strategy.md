# TDD 实施 commit 策略

## 适用范围

按 [`test-driven-development`](../../../.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/test-driven-development/SKILL.md) 模式实施完成、准备 commit 时。本规则解决"N 轮 R-G-R 微循环该如何映射成 commit"的问题。

## 三种策略对比

| 策略 | 优点 | 缺点 | 何时用 |
|---|---|---|---|
| **每个 R/G/REFACTOR 一个 commit** | 严格 TDD 历史可追溯 | 噪音过大（一个功能 30+ commit），git log 不可读，PR review 灾难 | **永不用**（除非纯教学场景） |
| **全 squash 一个大 commit** | git log 干净 | 难 revert 单个层、PR diff 巨大、cherry-pick 不可能 | 极简任务（< 3 文件 / < 100 行） |
| **按功能层次 N 个 commit**（推荐） | 平衡可读性与可 revert 性 | 需要构思 commit 边界 | **大多数实施任务** |

## 推荐：按功能层次切 commit

对于跨服务层 / 多文件的功能落地（如行业推荐定时推送 16 个 R-G-R），按 plan 的并行分组（G1/G2/G3/G5）对应切：

| Commit | 内容 | 对应分组 |
|---|---|---|
| 1 | 核心 service + 测试（aggregator / 业务核心） | G1 主体 |
| 2 | service 编排 + 其他 service 模块（formatter / llm） | G1 衍生 |
| 3 | 基础设施（新 pusher / 新 provider） | G2 |
| 4 | CLI + scheduler + 部署模板 | G3 |
| 5 | 文档同步（SKILL / INDEX / Makefile / README） | G5 |

**关键原则**：

1. **同一 commit 内的文件必须是"同一逻辑单元"**：service + 测试同 commit；CLI + scheduler 同 commit；文档单独 commit
2. **每个 commit 都能独立通过 pytest**：不允许"commit 1 留个未实现，commit 2 才补完"，前提是该层不依赖未提交的层
3. **commit message 标三段**：
   - 第一行：`<type>(<scope>): <短描述>` 标准格式
   - body：**做了什么** + **关键设计点**（2-5 条要点）
   - 末尾：测试数量 / 覆盖率 / TDD 轮数（如 `10 个 TDD 微循环 + 模块覆盖率 95%`）

## commit message 模板

```
<type>(<scope>): <一行短描述>

<2-4 段段落，描述：
- 做了什么（What）
- 关键设计点（Why this design）
- 注意事项 / TODO（What's not done）>

<n> 个 TDD 微循环（R-G-R）：<简述 R 序列>。模块覆盖率 <N>%。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

实战示例（行业推荐 G1 aggregator）：

```
feat(recommend): aggregator service with TDD coverage

跨 teacher_notes.sectors (JSON) + industry_info.sector_name 两路聚合行业热度，
按 score = mentions × (CONF_FLOOR + CONF_WEIGHT × avg_conf) × recency_decay 排序。

关键设计：
- 魔法数常量化：CONF_FLOOR=0.7 / CONF_WEIGHT=0.3 / DECAY_FLOOR=0.3
- industry_info.confidence 实为 TEXT（高/中/低），加 _parse_confidence 映射
- 窗口双边过滤 + delta_days 钳为非负
- sectors JSON 去重，snippets ORDER BY date DESC

10 个 TDD 微循环：T1-T5 plan 原定 + T6 confidence 映射 + T7 snippets +
T8 窗口上界 + T9 sectors 去重 + T10 snippets 倒序。模块覆盖率 95%。
```

## 不要做

- **不要在 commit message 里标 RED/GREEN/REFACTOR**：那是过程语义，最终 commit 关心"做了什么"
- **不要把测试和实现拆成两个 commit**："commit 1 加测试、commit 2 加实现"会让 commit 1 永远红 → revert 一个 commit 状态就坏
- **不要把"用户的另一个 in-progress 改动"也 add 进来**：每次 `git add` 用具体路径，**不用 `git add -A` 或 `git add .`**，避免误带 `.coverage` / `tmp/` / 别的工作分支

## Push 之前的 pre-push hook

项目已配 hooks（`make hooks-install`）在 `git push` 前自动跑 pytest 套件。**不要 `--no-verify`**：

- pytest 失败 = 推上去就破构建
- 真要紧急跳过（如改文档却被 hook 误判）→ 先在 commit 后跑一次 `make check-scripts` 自证，再考虑是否绕

## 配套规则

- TDD 流程：`superpowers:test-driven-development`
- 实施前 plan review：`implementation-plan.md`
- 实施后 codex review：`post-dev-codex-review.md`
- 文档同步：`skills-sync.md`
