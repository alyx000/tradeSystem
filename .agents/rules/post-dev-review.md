# 开发后代码审查（Codex MCP 轻量门禁）

> 2026-11-21 精简版：仅对高风险改动自动触发 **Codex MCP**（`codex` / `codex-reply`）只读审查；常规渲染、文案、只读子命令、纯 UI、单测及文档改动免强制审查（按 `dev-workflow.md` 跑通测试即可，用户点名时可随时追加 Codex 审查）。

## 触发范围（高风险改动）

完成测试后，触碰以下任一项时自动触发一次审查；紧耦合生产方与消费方可合并为一次审查，不攒到多阶段最终收尾：

- schema / 迁移、写库与状态流转
- 定时调度或外部推送出口
- 新增或变更 CLI / API / provider capability / 共享契约
- 凭据 / env 读取路径

## 执行约束

1. **调用方式**：调用 `codex` MCP 工具，固定 `sandbox: "read-only"`、`approval-policy: "never"`、`cwd` 指向仓库根目录；`prompt` 必须明确限定本次改动的文件清单/diff 范围与关键不变量，避免混审工作区中无关未提交改动。
2. **不可用降级**：当前会话未加载 Codex MCP 或服务/鉴权失败时，不得伪造结果或擅自绕过权限跑外部命令；退回 **1 名会话内独立审查者** 完成审查，并在结论中注明降级原因。
3. **处置与止损**：
   - 审查聚焦 bug、回归、边界、事务/状态一致与测试缺口，按“严重 / 中等 / 轻微”附文件与行号输出；
   - 主会话独立判断并处置（不整段甩给用户裁决）：**严重问题必修并重跑适用测试**，中等问题说明“已修 / 不采纳理由 / defer”，轻微问题简述即可；
   - 修复后如需复审，优先用 `codex-reply` 复用首轮 `threadId`；**最多两轮**，第二轮仍有严重问题时停止并交用户决策。

## 配套规则

- [dev-workflow.md](dev-workflow.md) / [implementation-plan.md](implementation-plan.md) / [test-design.md](test-design.md) / [tdd-commit-strategy.md](tdd-commit-strategy.md)
