# 大金重工类量价观察：近五日形态

2026-11-17 用户确认回看最近 5 个交易日，并允许 A 阶段第三根为高低点同时上移的阴线。规则版本：`recent-five-raised-range-hs-no-limit-down-2026-11-17`。

## 筛选与展示

- 窗口按完整 SSE 日历取事实日及此前 4 个开放日；逐日从该日已核验归档的完整合格范围重新计算 A，不只合并旧候选。停牌、行情缺失和历史不足保留原因。
- A 前两根须阳线、收盘严格上升。第三根若为阳线，收盘仍须上升；若为阴线，前复权最高价和最低价均须严格高于第二根。十字星、任一高低点持平均不满足阴线例外。
- 原量能与涨停条件保留：三根量严格递增，各根不低于此前 20 根均量，三根均量至少为其 2 倍；近 10 根不得出现已核验的真实收盘涨停。缺涨停价时不能推断为无涨停。
- B 筛选不变，沿用各日已核验 v2 事件；历史 B 的原始结构低点保持固定，期间曾跌破就不会因后来反弹恢复。停牌或缺日使结构检查待核验。
- 按股票合并全部命中日期，显示阶段、最近出现距今几个开放日、今日是否满足、历史 B 结构状态。今日 A 或 B 有缺口且无已确认当前命中时标为待核验。
- 最终 A/B 与近五日历史并集统一排除北交所、事实日收盘跌停。盘中触及后打开或过去某日跌停不属于此次排除；当日收盘/跌停价缺失或矛盾单列待核验，不混入合格名单。排除明细与价格证据保存在附件。
- 手机稿只保留一行汇总与每股一行，分“今天仍满足”和“仅历史出现”，列名称代码、板块、阶段、最近形态日与倍数；已有当日 A 持续性数据附 5 日量倍数。保留必要缺口和旧 B 失效标记。原 v2 对照、逐日事件和详细持续性只在完整稿中保留，手机不再拼接旧名单。历史命中不等于今日成立。

## 可追踪入口与既有依赖

`scripts/tools/volume_buildup_recent.py` 只读取既有本地研究归档与交易日历，写入内容寻址的研究附件。它不采集行情、不发送消息、不写业务数据库，不新增调度。依赖原研究流程生成的 `report.json`、校验和回执、`enhanced-v2` 报告/逐票状态、日线/因子/行业/涨停价来源及各自 SHA。来源中的 pickle 只接受本机可信研究流程生成、被归档绑定并通过 SHA 核验的文件，不读取外来 pickle。

```sh
python3 scripts/tools/volume_buildup_recent.py \
  --run-dir /Users/alyx/tradeSystem/data/reports/volume-buildup/runs/2026-11-16 \
  --input-by USER
```

默认只读归档所属仓库的 `data/trade.db`，可用 `--db-path` 指定其他完整 SSE 日历库。目标日必须开放；目标日归档失败直接失败，历史日期失败逐日标 `source_failed`，该日既不贡献候选也不计入核验覆盖。旧合同仅在内容寻址历史快照 SHA 匹配时可复用。写出前后核对来源与程序 SHA，保留新规则版本和请求者；原封存报告不覆盖。

附件位于日期目录的 `enhanced-v2/recent-shapes/`，包括 JSON、摘要、完整稿、计算程序快照和校验回执。完整稿重定位全部相对链接。

## 连接既有每日交付

`scripts/tools/volume_buildup_prepare.py` 将同目录的 `recent_shapes.py` 设为正式模块的兼容入口，并在原 `deliver_enriched.py` 的每日发送前加入近期形态模块。须先将本版本正式模块部署到研究目录所属仓库的 `scripts/tools/`；安装器核对目标模块存在且 SHA 匹配，并在落盘前复查。安装前需要现有文件的精确 SHA，默认预览，`--apply` 才备份并原子替换。已有近期脚本须同时提供匹配的 SHA；未知交付结构、文件漂移和符号链接拒绝安装。

```sh
shasum -a 256 data/reports/volume-buildup/research/deliver_enriched.py
shasum -a 256 data/reports/volume-buildup/research/recent_shapes.py
shasum -a 256 data/reports/volume-buildup/recurring-contract.md
python3 scripts/tools/volume_buildup_prepare.py --input-by USER \
  --expected-delivery-sha256 DELIVERY_SHA \
  --expected-wrapper-sha256 WRAPPER_SHA \
  --expected-contract-sha256 CONTRACT_SHA
# 核对预览后，同一命令加 --apply 安装。
```

全新、尚无 `recent_shapes.py` 的环境省略 wrapper SHA 参数。提供合同 SHA 时追加最新规则并在 `contract-history/` 保留旧合同的内容寻址快照；省略该参数则不改合同。安装回执和旧脚本保留在 `data/reports/volume-buildup/runtime-installs/`。如需回退，使用该次备份恢复两个脚本及本次追加的合同；安装本身不会变更原调度、实际推送或已有发送账本。既有研究采集、持续性模块和补充稿渲染仍是运行前置依赖，不属于本次迁移范围。

每日交付的原当日/归档校验、持续性、分段、送达去重与未知送达禁止重发保持原行为。近期模块失败时终止日报发送，不以旧稿代替；补充稿模式保持原行为。手动验证只执行预览，不发送钉钉。

## 验证

```sh
python3 -m pytest scripts/tests/test_volume_buildup_recent.py scripts/tests/test_volume_buildup_prepare.py -q
make commands-check
make check-scripts
```

专项覆盖阴线严格边界、原量能/涨停门、复权、五个开放日、来源绑定、历史缺口、当前未知、旧 B 不复活、相对链接、安装备份/漂移/幂等，北交所与当日收盘跌停排除、缺价待核验、每股一行无旧名单重复，以及模拟交付成功去重、失败阻断和未知送达禁止重发；不以真实推送作为测试。
