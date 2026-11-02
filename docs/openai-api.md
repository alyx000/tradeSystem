# OpenAI API 接入

纯文本 AI 分析可显式选择 OpenAI 官方 Responses API。默认仍为 Antigravity，配置密钥本身不会切换供应商。

在本机 `scripts/.env` 中设置（该文件不提交 Git）：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=你的密钥
LLM_MODEL=你账号可访问的模型ID
LLM_TIMEOUT_SECONDS=180
OPENAI_MAX_OUTPUT_TOKENS=4096
```

主 CLI 加载 `scripts/.env`；已导出的同名环境变量优先。定时 runner 还可能加载 `~/.config/tradeSystem.env`，请避免两处配置冲突。重启长期运行的服务才会重新读取配置。模型必须显式设置，不自动猜测账号权限或付费模型。

覆盖共用 `utils.llm_cli` 的文本调用：行业点评、研报叙事、每日最票、串阳主线、趋势主线概念复核、认知汇总、断板反包/尾盘/七天横盘 PK、三位一体评分。现有证据、红线、结构校验和业务降级继续生效；三位一体缓存按供应商隔离。独立 JS 慧博 workflow（包括其聚合）保留 Antigravity，并隔离 OpenAI 模型名。没有按任务自动选择模型或跨供应商重试；失败沿用各业务的规则/结构化降级。

慧博本地 PDF reader 需要真实文件读取能力，保留 Antigravity 通道。全局使用 OpenAI 时，该 reader 用 `ANTIGRAVITY_MODEL`（可选，空则 CLI 默认），不会将本地路径当成已读的 PDF 内容送给文本 API。

请求只发往 `https://api.openai.com/v1/responses`，不支持自定义代理端点，禁止 HTTP 重定向；系统网络代理仍由 requests 正常处理。请求设置 `store=false`，不附带工具、目录或自动读取文件。已有传入提示词中的业务证据会送至 OpenAI。密钥仅通过环境读取，不进入命令参数或诊断。API 按账号用量计费。

401/403、429、超时、响应未完成、拒绝、空输出或格式错误均为失败，保留既有降级路径，不伪装成功。适配器不自动重试 HTTP 请求；各业务原有重试仍适用。调大输出上限需权衡用量；上限不足返回未完成，不使用截断内容。

验证：`python3 -m pytest scripts/tests/test_openai_responses.py -q` 与 `make check-scripts`。单元测试隔离外网和密钥，不代表真实账号、余额和模型已验证。配置后须再做一次最小真实请求才能称为已连通。

回滚：将 `LLM_PROVIDER=antigravity`，并恢复原 `LLM_MODEL`；无需迁移数据库。

官方依据：[API 快速开始](https://developers.openai.com/api/docs/quickstart)。
