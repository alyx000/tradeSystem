# 成交榜容量趋势观察

出处为 `teacher_notes#1089`：按用户要求归档为2026-10-31技术课补充资料，原始材料是2026-10-30小组讨论（非正课）。本功能只提供量价事实，不把老师观点视作验证完成的规则。

## 计算与展示

- 观察对象为既有 `volume-watch daily` 当日成交额榜前20只，保留股票名称、代码和申万二级分类。成交额靠前不等于已确认的中军身份。
- 逐票读取90自然日日线，再读取复权因子；使用统一 `apply_qfq` 将收盘价转换到目标日坐标。
- 至少25根有效K线，展示目标日收盘相对MA20的位置、偏离百分比，以及MA20对比上一根的方向。
- 最近5根有效K线分别对比前一根收盘，统计上涨、下跌、持平次数；末端同向连续次数限定在这5根内。它不是阳线/阴线计数，也不是完整历史连涨纪录。
- 90自然日回看与5根观察窗口是工程口径，不是课程的明确阈值；停牌不填补K线，不能将5根有效K线说成连续5个市场交易日。
- `coverage_basis=returned_bars_only`：完整状态只表示来源返回序列上的指标可计算，尚未独立核验中间缺日是否停牌，不能据此声称逐市场交易日行情覆盖完整。
- 数据不足为 `partial`；缺目标日、重复/错序/未来数据、因子缺失、非法数值或返回身份冲突为 `source_failed`，不退回未复权计算。
- 行情必须带证券身份，按完整证券代码（含交易所）校验；复权接口现有契约省略代码，身份依赖单票请求绑定，若额外返回身份字段则同样完整核验。`identity_basis` 明示此边界，不宣称独立核验了复权源的逐行身份。
- Markdown 与 HTML 均把名称、行业、异常原因作为纯文本转义，避免来源文本被解释为链接或标记。

## 存储和兼容

复用 `daily_volume_concentration.source_json.capacity_trend`，不增加表、列、独立任务或API。保存口径、目标日、逐票行情/因子来源及健康状态。旧快照没有此块时展示“未计算”。

同日刷新失败时，仅在原完整快照的日期及股票/名称/分类/成交额均与本次一致时保留它。结果标记 `fallback_preserved`，完整保存 `latest_attempt` 失败收据并展示失败状态与缺口代码。股票集合或分类改变时不保留旧块。

`volume-watch daily` 随现有任务采集；`trend` 只读展示已存快照。正式每日复盘组装器只读**同日**快照，在②板块区展示摘要与可展开明细，降级状态进入缺口区，不采集、不迁移、不使用最近一天替代。

组装器遵循既有 `--trade-db` 指定的事实库；使用备份/测试库时不会混入默认库的容量趋势。

`--dry-run` 不保存新证据；`--no-push` 保存但不推送。既有自动任务的推送设置不变。此项不参与最票身份、排序、关注池或交易计划。

## 验证

### 板块汇总与历史变化（2026-11-01）

日报、`volume-watch trend --date YYYY-MM-DD --days N` 和正式复盘HTML共享只读派生模块 `capacity_history.py`。日报/HTML默认近10个开放日，trend沿用所请求窗口（容量历史上限60日）。trend的日期为截止日，主报告与容量区统一使用截止日以内最近快照日；HTML仍严格使用报告当日。不新增采集请求、表、字段、任务或推送开关，既有每天的逐股快照自然累积为历史；不反写旧数据，也不重算历史股票的当前行业。

- 当前汇总按当日申万二级列可计算/在榜数、收盘在MA20线上/线下比例、贴线数，以及线上且线向上/线下且线向下数量；比例以该组可计算数为分母，0个可计算样本的指标留空。未分类单列，所有比例只描述Top20样本，不代表全行业。
- 历史严格按SSE完整自然日日历中的开放日排列。缺快照为 `missing-data`，旧快照无容量证据为 `not_computed`，损坏为 `source_failed`；日历缺失或目标日非开放日时保留当日汇总，历史拒算，不用星期或最近记录补齐。
- 每日线上比例变动为动态在榜样本之间的百分点差；另列相邻开放日的同股、同分类共同样本线上比例变化，以及新进、退出、改类数量。两端均 `complete` 才发布比较；缺日、partial和fallback均阻断，窗口首日没有窗口外对照。没有共同股票时显示0只/变化未计算，行业不在榜不当作0%线上比例。
- 回退日展示旧完整证据的汇总，但显式保留 `fallback_preserved` 与本次尝试状态，不纳入跨日变化。
- 两市成交额只读既有 `market_total_billion`，与容量健康状态分别展示；金额和来源都可用、相邻开放日来源一致才算环比。它沿用两市来源口径，不冒充沪深北全市场，也不能由放量直接推断净流入。
- MD与HTML共享表格值与缺口说明，HTML历史可展开且表格可横向滚动。历史缺口进入正式复盘ops区。缺口不插值、不连贯化，不输出趋势成立或买卖结论。每日推送/日报的新增部分最多6000 UTF-8字节，且受全文18000字节剩余空间限制；优先当前汇总与整体逐日表，板块历史仅最近一日对照，按完整行截取并标明限长摘要。已有正文不截断；完整历史由只读trend与HTML展示。

新增验证：`python3 -m pytest scripts/tests/test_capacity_history.py scripts/tests/test_capacity_trend.py scripts/tests/test_volume_concentration_service.py scripts/tests/test_volume_concentration_formatter.py scripts/tests/test_daily_review_html_report.py -q`。覆盖换榜假改善、同股归类变化、节假日/日历缺口、来源切换、旧快照/损坏/回退、只读与dry-run、MD/HTML同源转义。

双门审查第一轮：门1发现十日板块历史直接加入每日消息的长度回归，已用摘要预算修复；门2发现trend最近快照与请求日混用，已统一观察日期。增加20行业×10日、UTF-8转义预算及周末/开放日缺快照测试，修后专项716 passed。

第二轮门1通过；门2的“没有生产日历写入路径”判断已反驳：隔离包未包含 `cli/volume_watch.py`、`utils/trade_date.py` 和 `db/queries.py`，完整仓库的正式daily入口先调用 `is_non_trading_day`，再由 `ensure_trade_calendar` / `upsert_trade_calendar` 保存年度日历。新增空库CLI集成测试，不直接插入任何日历行，验证该路径实际补入365行并成功生成10个开放日的历史视图。只读HTML/trend及dry-run不自行补日历；跨年缺旧年日历或源失败时保留未计算，不降低日历硬门。反驳依据亦保留在 `_open_days` 注释中。

最终逻辑版本全量 `make check-scripts`：7924 passed、1 skipped、21条既有依赖警告（260.82秒）；另补上述日历集成测试。2026-10-30真实库只读验证20只/8行业，线上2只、线下18只；近10开放日有8个旧快照未计算、1日缺快照，未伪造历史变化。独立逐股数字复核、正式组装器校验、1440px/390px浏览器QA均通过。

`python3 -m pytest scripts/tests/test_capacity_trend.py scripts/tests/test_volume_concentration_service.py scripts/tests/test_volume_concentration_formatter.py scripts/tests/test_daily_review_html_report.py -q`

覆盖除权、涨跌/持平、混合走势、缺源、错位、旧快照、失败刷新保护、只读加载及正式复盘注入；随后执行 `make check-scripts`。

### 2026-11-01 审查与验证记录

- 第一道独立审查已关闭数值字符串渲染崩溃、组装器未透传 `--trade-db` 两项问题。
- 第二道审查经用户授权，由 OpenAI Codex 对本次隔离代码与差异审阅。原指定模型不受账户支持，查询可用模型后使用 `gpt-5.6-terra`。
- 第一轮发现的完整证券身份校验与 Markdown 来源文本转义问题均已修复并补测；复权行省略代码符合既有接口契约，限制已在代码注释、收据和展示中说明。
- 第二轮正式结论 `approve`，无剩余实质问题；审查线程 `01a19728-6a6b-7923-8a72-33f51c018547`。
- 修复后容量趋势专项 37 项通过；`make check-scripts` 为 **7361 passed, 1 skipped**，21 条依赖警告，用时 382.85 秒；`git diff --check` 通过。
- 尚未运行真实行情采集或历史回填；自动任务接入复用既有入口，以上结果不代表真实源覆盖验证已经完成。

### 合并 main 前的隔离验证

仅提取本任务九个文件至远端 `main` 基线 `68e30227`，组装器仅适配插入位置，未带入原工作区其他任务改动。该发布版本 `make check-scripts`：**7887 passed, 1 skipped**，21 条依赖警告，281.35 秒；`git diff --check` 通过。此前 7361 项结果对应开发工作区，此处为实际发布基线的完整验证。

2026-11-01：`volume-watch daily` 必须显式提供非空 `--input-by`；Make 入口默认 USER，既有21:00 runner 固定 launchd。请求者及开始/采集完成时间写入 `source_json.audit` 与 `capacity_trend.audit`；刷新回退保留旧证据审计，本次尝试审计在 `latest_attempt.audit`。手动补采使用 `--no-push`。

正式运行验证（2026-11-01）：通过生产 CLI `volume-watch daily --date 2026-10-30 --input-by USER --no-push` 写入真实业务库，13:21:18 UTC 回读为 complete、20/20，所有指标与只读预览一致。`source.audit` 与容量证据 audit 均为 USER，含 UTC 开始/完成时间。实际加载的工作日21:00任务脚本已使用 `--input-by launchd`，无需新增或重载调度；下一开放日2026-11-02。全量7895 passed/1 skipped、运行目录专项45 passed，双道审查通过。
