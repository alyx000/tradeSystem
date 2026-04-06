# 记录信息详细规则

## 老师观点结构化提炼

对 `openclaw` / `copaw` / 其它自动化录入方，先做结构化提炼，再执行 `db add-note`。

最低要求：

- `--title`：简短、可检索
- `--key-points`：2 到 5 条独立要点

强烈建议补充：

- `--core-view`
- `--sectors`
- `--tags`
- `--position-advice`
- `--stocks`

不要把口语原文、截图标题或“老师观点”之类空泛字样直接当标题。

## 长文本入口

短文本可直接用：

```bash
python3 main.py db add-note --raw-content "短文本"
```

PDF / OCR / 超长原文优先用文件：

```bash
python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "课件长文提炼" \
  --raw-content-file /tmp/ocr.txt \
  --input-by openclaw
```

若内容来自管道或前一步脚本，走 `stdin`：

```bash
cat /tmp/ocr.txt | python3 main.py db add-note \
  --teacher "小鲍" \
  --date "2026-04-01" \
  --title "课件长文提炼" \
  --raw-content-file - \
  --input-by openclaw
```

`--raw-content` 与 `--raw-content-file` 互斥，不能同时传。

## 附件处理

- 图片或截图先落本地临时文件，再传给 `--attachment`
- CLI 会复制到 `data/attachments/{date}/`
- 回查 API 时应能看到 `attachments` 字段

## 用户确认模板

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
  原文: 已保留
  附件: 1 张图片

确认录入？(是/否)
```

## 关注池候选说明

若 `--stocks` 存在，CLI 会输出候选关注池列表。

- 候选是建议，不是自动加入
- 已在关注池里的标的会进入 `skipped`
- 需要实际加池时，再交给 `portfolio-manager`
