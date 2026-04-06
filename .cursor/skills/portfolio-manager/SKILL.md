---
name: portfolio-manager
description: 管理持仓池、关注池、黑名单、交易记录，提供标准化的增删改查接口供 AI Agent 调用
version: "1.1"
---

# Skill: 投资组合管理

## 使用场景

当用户说出以下类型的话时，激活此 skill：

- **持仓操作**：「我买了...」「加仓了...」「卖掉...了」「清仓...」
- **关注池操作**：「关注一下...」「加到关注池」「去掉...的关注」「把...升级到核心关注」
- **查询操作**：「看一下我的持仓」「当前关注的票」「列出关注池」
- **交易记录**：「记录一笔交易」「买入/卖出记录」
- **黑名单**：「...加黑名单」「...回避一下」

## 工作流程

## 优先入口

若仓库根目录 `Makefile` 可用，查询类操作优先使用：

```bash
make holdings-open
make watchlist-open
make holdings
make watchlist
```

写操作因参数较多，默认继续使用底层 `python3 main.py db ...`。

### Step 1：理解意图

分析用户说话的动作类型：

| 动作 | 关键词 | 对应操作 |
|------|-------|---------|
| 新建持仓 | 买了、建仓、加仓、开仓 | `holdings-add` |
| 清空持仓 | 卖掉、卖出、清仓、出掉 | `holdings-remove` |
| 查看持仓 | 看持仓、我的仓位、当前持有 | `holdings-list` |
| 加关注 | 关注、放到关注池、盯一下 | `watchlist-add` |
| 去关注 | 去掉、移除、不看了 | `watchlist-remove` |
| 升级关注 | 升级、改成核心、调层级 | `watchlist-update` |
| 查关注 | 看关注池、当前关注 | `watchlist-list` |
| 录交易 | 记录、交易记录 | `add-trade` |
| 加黑名单 | 黑名单、回避、不碰 | `blacklist-add` |

### Step 2：提取信息

从用户的话中提取：
- **股票代码**：6 位数字（如 300750）
- **股票名称**：中文名（如 宁德时代）
- **数量/价格**：如 200 股、85 块
- **板块**：锂电、AI、银行等
- **关注层级**（watchlist）：
  - `tier1_core`：核心关注/重仓候选
  - `tier2_watch`：观察/备选
  - `tier3_sector`：板块跟踪

若信息不完整，向用户询问。

### Step 3：执行前先做结构化确认

凡是会写入或更新数据库的操作（`holdings-add` / `holdings-remove` / `watchlist-add` / `watchlist-remove` / `watchlist-update` / `add-trade` / `blacklist-add`），**都先展示结构化解析结果，再等待用户确认**。不要从自然语言里直接猜测字段后立刻写库。

**强制顺序：**
1. 识别动作类型
2. 提取结构化字段
3. 展示即将执行的操作摘要
4. 用户确认后，再执行命令

**禁止猜测的字段：**
- 股票代码
- 股票名称
- 价格、股数
- `tier`、`status`
- 交易方向（`buy` / `sell`）
- 黑名单到期日

如果用户一句话里包含多个动作，要先拆分再确认。例如“今天买了宁德时代，顺便放进核心关注”应拆为：
- `holdings-add`
- `add-trade`
- `watchlist-add` 或 `watchlist-update`

可参考以下确认模板。建议统一按“类型/动作 + 关键字段 + 影响/待确认项 + 确认语句”的结构展示：

```text
即将执行：
  类型: 投资组合写操作
  动作: holdings-add + add-trade
  股票: 300750 宁德时代
  股数: 300
  价格: 86.0
  板块: 锂电
  备注/理由: 锂电龙头
  影响: 将新增持仓并写入一笔买入交易

确认执行？(是/否)
```

关注池场景可展示：

```text
即将执行：
  类型: 关注池写操作
  动作: watchlist-add
  股票: 688041 海光信息
  层级: tier1_core
  原因: 半导体龙头
  板块: 国产算力
  影响: 将新增一条关注池记录

确认执行？(是/否)
```

只有查询类操作（如 `holdings-list`、`watchlist-list`）可直接执行，无需确认。

### Step 4：执行命令

查询类操作优先在仓库根目录执行：

```bash
cd /path/to/tradeSystem
make holdings-open
make watchlist-open
make holdings
make watchlist
```

以下写操作命令在仓库的 `scripts/` 目录下执行（请先 `cd` 到本机 `tradeSystem/scripts`，与 `market-tasks` skill 一致）。

#### 持仓管理

```bash
cd /path/to/tradeSystem/scripts
# 新增持仓
python3 main.py db holdings-add \
  --code 300750 \
  --name "宁德时代" \
  --shares 200 \
  --price 85.0 \
  --sector "锂电" \
  --stop-loss 80.0 \
  --market A股

# 移除持仓（置 closed，保留历史）
python3 main.py db holdings-remove --code 300750

# 查看当前持仓
python3 main.py db holdings-list
```

#### 关注池管理

```bash
cd /path/to/tradeSystem/scripts
# 添加到关注池
python3 main.py db watchlist-add \
  --code 300750 \
  --name "宁德时代" \
  --tier tier1_core \
  --reason "锂电龙头，板块主升期关注" \
  --sector "锂电"

# 从关注池移除
python3 main.py db watchlist-remove --code 300750

# 更新层级或状态
python3 main.py db watchlist-update --code 300750 --tier tier1_core
python3 main.py db watchlist-update --code 300750 --status tracking --note "进入跟踪期"

# 列出关注池（按层级过滤）
python3 main.py db watchlist-list
python3 main.py db watchlist-list --tier tier1_core
python3 main.py db watchlist-list --status watching
```

**`--tier` 说明：**
- `tier1_core`：核心关注，重仓候选，板块龙头
- `tier2_watch`：二线关注，观察位，备选
- `tier3_sector`：板块跟踪，行情时关注

**`--status` 说明：**
- `watching`：正在关注（默认）
- `tracking`：跟踪中（已建仓或深度关注）
- `removed`：已移除

#### 交易记录

```bash
cd /path/to/tradeSystem/scripts
# 买入记录
python3 main.py db add-trade \
  --code 300750 \
  --name "宁德时代" \
  --direction buy \
  --price 85.0 \
  --date 2026-04-01 \
  --shares 200 \
  --sector "锂电" \
  --reason "板块龙头首阴，情绪启动期建仓"

# 卖出记录（含盈亏）
python3 main.py db add-trade \
  --code 300750 \
  --name "宁德时代" \
  --direction sell \
  --price 92.0 \
  --date 2026-04-03 \
  --shares 200 \
  --pnl-pct 8.2 \
  --reason "主升结束，情绪分歧，减仓"
```

#### 黑名单

```bash
cd /path/to/tradeSystem/scripts
# 永久黑名单
python3 main.py db blacklist-add \
  --code 000001 \
  --name "某问题股" \
  --reason "财务造假嫌疑，长期回避"

# 临时回避（设定到期日）
python3 main.py db blacklist-add \
  --code 000002 \
  --name "解禁压力股" \
  --reason "大规模解禁，短期回避" \
  --until 2026-06-30
```

### Step 5：汇报结果

执行成功后，用中文告知用户：

```
✅ 已添加持仓：宁德时代 (300750)，200股，成本 85.0，止损 80.0

当前持仓 (2只):
  300750 宁德时代 | 200股 | 成本 85.0 | 锂电 止损 80.0
  688041 海光信息 | 100股 | 成本 220.0 | 国产算力
```

## 常见对话示例

**用户**：「今天买了300股宁德时代，价格 86 块，锂电龙头」

**Agent**：
1. 识别动作：holdings-add + add-trade（买入）
2. 提取信息：code=300750, name=宁德时代, shares=300, price=86, sector=锂电
3. 先确认：
   ```text
   即将执行：
     动作: holdings-add + add-trade
     股票: 300750 宁德时代
     股数: 300
     价格: 86
     板块: 锂电

   确认执行？(是/否)
   ```
4. 执行：
   ```bash
   cd /path/to/tradeSystem/scripts
   python3 main.py db holdings-add --code 300750 --name "宁德时代" --shares 300 --price 86 --sector 锂电
   python3 main.py db add-trade --code 300750 --name "宁德时代" --direction buy --price 86 --date 2026-04-01 --shares 300 --sector 锂电
   ```
5. 回复：「已录入持仓和买入记录：宁德时代(300750) 300股 @86.0」

---

**用户**：「把海光信息加到核心关注，半导体龙头」

**Agent**：
1. 识别动作：watchlist-add，tier1_core
2. 先确认：
   ```text
   即将执行：
     动作: watchlist-add
     股票: 688041 海光信息
     层级: tier1_core
     原因: 半导体龙头
     板块: 国产算力

   确认执行？(是/否)
   ```
3. 执行：
   ```bash
   cd /path/to/tradeSystem/scripts
   python3 main.py db watchlist-add --code 688041 --name "海光信息" --tier tier1_core --reason "半导体龙头" --sector 国产算力
   ```

## 注意事项

- 持仓操作和交易记录是两回事：买入需同时执行 `holdings-add` + `add-trade`，卖出执行 `holdings-remove` + `add-trade`
- 若不确定股票代码，可查询「股票名称 + 代码」后再录入，不要猜
- `holdings-remove` 不会物理删除，只标记 `closed`，历史保留
- 关注池和持仓是独立的，建仓后可将状态更新为 `tracking`
- 写操作先确认，再执行；查询类操作可直接执行
