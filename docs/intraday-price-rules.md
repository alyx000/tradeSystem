# 盘中价位规则与检查回执

普通价位不用改代码。规则、运行状态由原来的盘中任务消费，不增加调度或消息类型。原有阈值、行情源、3 分钟节拍、首次命中、去重、重入和同日发送失败重试策略不变；没有登记操作时原规则全部保持原样。

## 管理

以下均为**预览**，不会修改正式规则。确认后移除 `--dry-run`。从生产监控使用的工作目录执行，先用 `intraday-monitor status --all` 查规则 ID；不确定股票身份时，先通过 `db stock-resolve` 核实名称与交易所代码。

```bash
python3 scripts/main.py intraday-monitor rule add --code 601398.SH --direction below --threshold 8.75 --days 5 --input-by USER --dry-run --json
python3 scripts/main.py intraday-monitor rule extend --rule-id icbc-below-8-88-20261125-1201 --days 3 --input-by USER --dry-run --json
python3 scripts/main.py intraday-monitor rule disable --rule-id icbc-below-8-88-20261125-1201 --input-by USER --dry-run --json
python3 scripts/main.py intraday-monitor status --all --json
```

- 新增支持已接入的指数和带完整交易所代码的 A 股；新股票还须给 `--name`。方向 `below/above` 默认严格小于/大于，`--inclusive` 含等号；`near` 需指定 `--proximity-pct 1` 等容差，含两端。均线、涨停板等特殊策略不由固定价登记入口创建。
- 有效期按完整的上交所日历计算，首尾均含。未收盘的开放日可算第一天；15:00 起从下一个开放日算。可用 `--start YYYY-MM-DD` 指定未来起算日。有效规则从原截止日后追加 N 个交易日；过期规则重新起算，不补发有效期空档。长期规则不需要续期；已下线 ID 不复用。
- 每次正式变更带请求者、时间、规则 ID 与起止日（如适用），原子追加到 `data/runs/intraday-monitor/price-rules.json`；与行情状态分开。部署保留此文件和 `state.json`，不要直接编辑。新建与既有同条件规则重叠、日历不全或登记簿损坏均报错，不冒险启用。查询、预览不写库、不推送。

## 怎样读状态

`status` 默认列当前有效期内的规则，`--all` 也列未来、已到期、已下线规则。逐条显示最后成功判断和有效行情时间；当天结果与发送情况分开：

| 看到的结果 | 含义 |
| --- | --- |
| 已检查、未触发 | 有可用行情和计算依据，本轮条件不成立。 |
| 已触发 / 条件成立、本轮未产生新提醒 | 出现新的提醒事件，或条件仍成立但不产生新事件（含只建基线）。真实发送成功、失败待重试另列，其他规则发出消息不计入本条。 |
| 行情缺失 / 计算依据不足 | 不能判断，保留最近一次成功信息；当天分别累计行情和计算缺口。失败时也可能重试发送更早已确认的事件。 |
| 无当日检查记录 | 未见当天正式检查。旧账本不伪造历史次数；非交易日、时段外、节流、预览、机器未运行都不当作一次检查。 |

统计自升级后第一轮正式检查开始，保留每条规则最近观测日的汇总，不是逐分钟全覆盖证明。检查从不为了显示状态额外取行情，也不补发历史消息。真实端到端推送仍须明确授权并显式传 `--confirm-real-push`。

普通价位阈值最多两位小数。需要安全重试续期时，加 `--expected-until YYYY-MM-DD` 绑定原截止日；如果上次已成功，重复命令会拒绝再次追加。

部署和回滚前，使用**当前运行版**的 `intraday_monitor_transition_guard.py` 检查目标提交与三份待发账本。有非空登记簿时，旧版不支持该格式、目标缺少被管理的内置规则、或目标不认识仍待发送的规则，均阻止切换。不要删除登记簿来绕过检查。部署窗口暂停人工规则变更。
