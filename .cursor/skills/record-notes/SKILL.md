---
name: record-notes
description: 录入老师观点、行业板块信息、宏观经济信息到 SQLite 数据库（支持文字/图片/混合内容，适配 Discord/QQ/微信 channel 场景）
version: "1.0"
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

### 3. 录入老师观点

```bash
cd /path/to/tradeSystem/scripts
# 纯文字
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "AI算力主线判断" \
  --core-view "AI算力主线未结束，龙头首阴有价值" \
  --source-type text \
  --input-by openclaw

# 图片内容（一张图）
python3 main.py db add-note \
  --teacher "小张" \
  --date "2026-04-01" \
  --title "看盘截图" \
  --source-type image \
  --input-by openclaw \
  --attachment /tmp/screenshot.jpg

# 图文混合（多张图）
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "复盘总结" \
  --core-view "情绪高潮后需要降仓" \
  --source-type mixed \
  --raw-content "（完整文字内容）" \
  --tags '["情绪周期","仓位管理"]' \
  --input-by openclaw \
  --attachment /tmp/img1.jpg /tmp/img2.jpg /tmp/img3.jpg
```

**参数说明：**
- `--teacher`：老师名称（不存在则自动创建）
- `--date`：内容对应的日期（非录入日期）
- `--title`：简短标题，便于日后搜索
- `--core-view`：核心观点摘要（可选，建议提炼）
- `--source-type`：`text` / `image` / `mixed`
- `--input-by`：录入方（固定填 `openclaw` 或 `copaw`）
- `--tags`：标签 JSON 数组，如 `'["AI","连板"]'`
- `--attachment`：附件本地路径，支持多个

### 4. 录入行业信息

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

### 5. 录入宏观信息

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

录入前，展示摘要让用户确认：

```
即将录入：
  类型: 老师观点
  老师: 小鲍
  日期: 2026-04-01
  标题: AI算力主线判断
  核心观点: AI算力主线未结束，龙头首阴有价值
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
