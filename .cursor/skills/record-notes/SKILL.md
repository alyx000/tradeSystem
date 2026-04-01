---
name: record-notes
description: 录入老师观点、行业板块信息、宏观经济信息到 SQLite 数据库；OpenClaw/Copaw 需先结构化提炼再写入（支持文字/图片/混合内容，适配 Discord/QQ/微信 channel 场景）
version: "1.1"
---

# Skill: 记录信息（老师观点 / 行业 / 宏观）

## 使用场景

当用户（或通过 channel 收到的消息）包含以下内容时，激活此 skill：
- 老师的分析观点、判断、复盘总结
- 行业/板块动态信息（研报、政策、数据）
- 宏观经济信息（货币政策、财政、外贸等）
- 上述内容可能同时携带图片附件

## 工作流程

### 1. 识别信息类型

根据内容判断属于哪种类型：

| 类型 | 判断依据 | 对应命令 |
|------|---------|---------|
| 老师观点 | 包含"老师说"/"小鲍"/"小张"等人名；主观判断类表述 | `db add-note` |
| 行业信息 | 涉及具体板块/行业的客观数据、政策、研报 | `db add-industry` |
| 宏观信息 | 货币政策/财政/外贸/宏观经济指标 | `db add-macro` |

混合内容（如老师引用了行业数据）：优先录入为老师观点，内容包含完整原文。

### 2. 处理图片附件（Channel 场景）

如果消息包含图片 URL 或文件路径：

```python
# 下载图片到临时目录
import requests, tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp())
img_path = tmp / "attachment.jpg"
resp = requests.get(image_url, timeout=30)
img_path.write_bytes(resp.content)
```

下载完成后，将本地路径传给 `--attachment` 参数。

### CLI 执行位置

所有 `python3 main.py db …` 命令必须在仓库的 `scripts/` 目录下执行（与 `market-tasks` skill 一致）。下面示例首行的路径请换成本机 `tradeSystem/scripts`。

### 3. OpenClaw / Copaw：录入前必做——结构化提炼

当录入方是 `openclaw` 或 `copaw` 时，**必须先把原始消息整理成结构化摘要，再调用 `db add-note`**。不要把截图、原文、口语化消息直接原样写库。

**强制顺序：**
1. 读取原文 / OCR / 图片内容，保留原始上下文
2. 提炼结构化字段
3. 向用户展示摘要并确认
4. 用户确认后，再执行 `python3 main.py db add-note ...`

**结构化提炼最低要求：**
- `--title`：生成简短、可检索的标题，避免使用“老师观点”“截图”等空泛标题
- `--key-points`：提炼成 JSON 数组，写 2-5 条独立要点，短句化、可搜索

**强烈建议同步提炼：**
- `--core-view`：一句话概括老师最核心的判断
- `--sectors`：能识别时写涉及板块
- `--tags`：提取题材、方法论、情绪周期等标签
- `--position-advice`：涉及仓位、节奏、风控时补充

**原文与提炼并存：**
- `--raw-content`：保存完整文字原文，图片场景可写 OCR 结果
- `--attachment`：保留原始截图/图片附件

如果内容不足以可靠提炼 `title` 或 `key-points`，先向用户追问或确认，不要臆造。

可参考以下录入前摘要模板。建议统一按“类型/动作 + 关键字段 + 待确认项/影响 + 确认语句”的结构展示：

```text
即将录入：
  类型: 老师观点
  动作: add-note
  录入方: openclaw
  老师: 小鲍
  日期: 2026-04-01
  标题: AI算力主线仍在，龙头首阴可观察
  核心观点: AI算力主线未结束，分歧后核心股首阴仍有价值
  要点:
  - 主线没有结束，分歧更像换手
  - 龙头首阴优先看承接，不追杂毛
  - 仓位不宜激进，等分歧确认后再加
  涉及板块: AI算力, CPO
  标签: 主线, 首阴, 仓位管理
  仓位建议: 先控仓，分歧转强再逐步加仓
  原文: 已保留到 raw-content
  附件: 1 张图片

确认录入？(是/否)
```

对 `cursor` / `manual` 等人工录入场景，仍建议先提炼，但不做强制要求。

### 4. 录入老师观点

```bash
cd /path/to/tradeSystem/scripts
# 纯文字
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "AI算力主线判断" \
  --core-view "AI算力主线未结束，龙头首阴有价值" \
  --key-points '["AI算力主线未结束","龙头首阴有观察价值","分歧日先看承接"]' \
  --sectors '["AI算力","CPO"]' \
  --tags '["主线","首阴","分歧"]' \
  --source-type text \
  --input-by openclaw

# 图片内容（一张图）
python3 main.py db add-note \
  --teacher "小张" \
  --date "2026-04-01" \
  --title "看盘截图" \
  --source-type image \
  --key-points '["截图以盘中强弱切换为主","需结合原图进一步确认细节"]' \
  --input-by openclaw \
  --attachment /tmp/screenshot.jpg

# 图文混合（多张图）
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "复盘总结" \
  --core-view "情绪高潮后需要降仓" \
  --key-points '["情绪高潮后先降仓","高位股更看分歧承接","次日不追一致性"]' \
  --source-type mixed \
  --raw-content "（完整文字内容）" \
  --tags '["情绪周期","仓位管理"]' \
  --input-by openclaw \
  --attachment /tmp/img1.jpg /tmp/img2.jpg /tmp/img3.jpg
```

**参数说明：**
- `--teacher`：老师名称（不存在则自动创建）
- `--date`：内容对应的日期（非录入日期）
- `--title`：简短标题，便于日后搜索；`openclaw` / `copaw` 录入前必须先提炼
- `--core-view`：核心观点摘要（可选，建议提炼）
- `--source-type`：`text` / `image` / `mixed`
- `--input-by`：录入方（自动化录入固定填 `openclaw` 或 `copaw`；人工可填 `cursor` / `manual`）
- `--tags`：标签 JSON 数组，如 `'["AI","连板"]'`
- `--key-points`：结构化要点 JSON 数组，如 `'["首阴有价值","控仓至3成"]'`；`openclaw` / `copaw` 建议作为必填提炼结果
- `--sectors`：涉及板块 JSON 数组，如 `'["AI算力","锂电"]'`
- `--position-advice`：仓位建议文字，如 `"控制至3成，跌破支撑减仓"`
- `--raw-content`：完整原始全文（图片 OCR 或文字内容）
- `--attachment`：附件本地路径，支持多个

### 5. 录入行业信息

```bash
cd /path/to/tradeSystem/scripts
python3 main.py db add-industry \
  --sector "AI算力" \
  --date "2026-04-01" \
  --content "英伟达 H100 出货量超预期，AI服务器需求持续旺盛" \
  --info-type "研报" \
  --source "华泰证券" \
  --confidence "高" \
  --tags '["AI","算力","英伟达"]'
```

**参数说明：**
- `--sector`：板块名称（如"AI算力"、"锂电池"、"新能源"）
- `--info-type`：研报 / 政策 / 数据 / 新闻 / 其他
- `--confidence`：高 / 中 / 低

### 6. 录入宏观信息

```bash
cd /path/to/tradeSystem/scripts
python3 main.py db add-macro \
  --category "货币政策" \
  --date "2026-04-01" \
  --title "央行全面降准25BP" \
  --content "2026年4月1日，央行宣布全面降准25个基点，预计释放流动性约6000亿元" \
  --source "新华社" \
  --impact "利好股市，降低融资成本，银行板块受益" \
  --tags '["降准","货币政策","流动性"]'
```

**`--category` 常用分类：**
货币政策 / 财政政策 / 外贸 / 汇率 / 通胀 / 就业 / 地产 / 海外宏观

## 用户确认流程

录入前，先展示结构化摘要让用户确认：

```
即将录入：
  类型: 老师观点
  老师: 小鲍
  日期: 2026-04-01
  标题: AI算力主线判断
  核心观点: AI算力主线未结束，龙头首阴有价值
  结构化要点:
  - AI算力主线未结束
  - 龙头首阴有观察价值
  - 分歧日先看承接
  涉及板块: AI算力, CPO
  标签: 主线, 首阴, 分歧
  仓位建议: 先控仓，确认分歧转强后再考虑加仓
  附件: 2 张图片

确认录入？(是/否)
```

若用户确认，执行命令并报告结果。

## 验证结果

成功输出示例：
```
✅ 已录入笔记 (id=42): 小鲍 - AI算力主线判断, 附件 2 个
```

失败时：
- 检查 `--date` 格式是否为 `YYYY-MM-DD`
- 检查 `--tags` 是否为合法 JSON 数组
- 检查 `--attachment` 文件路径是否存在

## 注意事项

- 附件文件会被复制到 `data/attachments/{date}/` 目录，临时文件可删除
- `--input-by` 用于追溯录入来源，请如实填写
- 一次消息可能包含多条信息，循环调用命令分别录入
- 图片识别内容建议用 OCR 或 vision API 提取后写入 `--raw-content`
- `openclaw` / `copaw` 在执行 `db add-note` 前，应先完成结构化提炼，再进入用户确认流程
