# 板块推演与龙头接力模板内容契约

来源：用户于2026-10-09提供《板块推演填空和龙头接力模版加思维导图.docx》。原DOCX SHA256：`5574d5a21d141b4557a3b07d83843dd00e2cb6c5443f6ecec611d1cce6478243`。

`template_assets/` 保存OOXML正文段落按原序转义后的两份HTML及两张原始PNG。正文中的“23个/23日/34板”等原始写法保持原样；图文差异亦保留。图中三选一C为高低切细分，正文C为趋势分歧，不强行统一。原图原文均是引用教学资料，不是当日建议。HTML内嵌图片data URI，可脱离下载目录离线阅读；不加载远端资源。

## 正式生成流程

`scripts/services/review_templates.py` 为纯读取、转义和渲染模块。组装器自动在②板块及⑤龙头分别注入唯一 `data-review-template="sector|relay"`；chunk禁止手填。保留7 chunks/15 anchors。11+5项分析默认折叠，原文/思维导图另行折叠；来源、状态与所有内容可搜索，手机两列改一列，原图局部滚动。

组装目录可提供 `review_template_answers_<REPORT_DATE>.html`。没有文件或缺少字段时逐项显示待补充，ops汇总缺口；不从当前池、其他日期或模板示例补数。若提供，文件中每个答案只能是如下纯文本p，必须四项属性齐全；不接受脚本、其他标签、重复字段、未知字段、错日或无来源的已有分析。

```html
<p data-template-field="sector.scan" data-as-of="2026-10-08" data-status="partial" data-source-refs="s1 s2">[事实] 根据报告列出已核验方向。[判断] 周期仍待验证。</p>
```

- `data-as-of` 是所属报告日；若引用休市历史事实，正文必须另写事实日和历史性质。
- `data-status` 可为 `complete`、`partial`、`missing-data`、`source_failed`、`pending`、`user-input`；只有complete计入已完整整理。来源失败保留原状态，不能写成无信号。
- `data-source-refs` 是空格分隔的章节锚点：s0–s8、teachers、industry、cognition、ops；complete/partial必须至少一项。来源链接用于追溯，语义与数字仍由主会话逐项核验，渲染器不证明观点正确。
- 每项正文1–1400字，以 `[事实]` 或 `[判断]` 开始；事实、推演和待用户填写明确区分。原文操作/仓位字段保留，分析不生成具体买卖建议，不写TradeDraft/TradePlan、关注池或复盘业务库。

| 前缀 | 字段 |
|---|---|
| sector | scan、cycle、priority、character、return、exhaustion、logic、rolling、auction、risk、reflection |
| relay | market、choice、auction、risk、reflection |

板块逐项涵盖全市场扫描、双周期/连接点、A–E、主动定性/模式、回流、充分演绎甄别、逻辑五维与中期验证、滚动、竞价、风险仓位、反思。接力涵盖市场/情绪/晋级及两日风险、三种思路及分支校验、竞价/新题材、风险仓位、执行反思。仅有单日证据时不得定性连续2天风险或给周期/优先级硬结论。缺少用户真实执行记录时不得代填得失总结。

## 验证与维护

变更需运行 `scripts/tests/test_review_templates.py`、`scripts/tests/test_daily_review_html_report.py`、CLI smoke及 `make check-scripts`；核对完整原文、图片、转义、防重复、防错日与缺口语义，再用桌面/390px验证搜索、展开/收起及无整页横向溢出。保持原正文/证据预算；模板16项（每项最多1400字）、原文/图片及缺口汇总独立计量，只有与官方重渲染逐字对账成功才获得对应容量，其他chunk不能借用。模板根节点必须是②/⑤章节默认可见的直接子节点，缺口提示必须是ops默认可见的直接子节点；禁止额外折叠或隐藏包装借用正文额度。不能删除既有事实换取通过。同步daily-review SKILL、九路流程、模板README、INDEX与skills-sync；本技能当前没有agents/openai.yaml。预览另存，旧归档不覆盖，不重新采集或发送报告。
