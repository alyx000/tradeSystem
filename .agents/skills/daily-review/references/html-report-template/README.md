# 盘后复盘 HTML 报告模板

配合 [`../multi-agent-review.md`](../multi-agent-review.md) 使用。产物 = `data/reports/复盘_YYYY-MM-DD.html`（只读，不写工作台/计划层）。

## 文件

- `review_style.css` — 报告样式（section.step / span.num / tag t-fact/t-judge / kpi / callout.risk/.note / tr.hl / span.up/.down/.warn / ul.clean / h3.blk / twocol / mono）。

## Body chunk 骨架（8 块）

分块写便于逐节迭代；每块是纯 HTML 片段（无 `<html>`/`<head>`/`<body>`）：

| chunk | 内容 |
|---|---|
| `head` | `<header class="top">` header + `<nav class="toc">` + `<div class="tldr">` 速览（KPI 网格 + 7 条 li） |
| `s0` | 第 0 节前日对照判分：0a 上一份报告预埋锚点逐条对账表 + 0b 老师 T-1 观点判分（✓/◐/✗） |
| `s1` | ①大盘：六指数全景 / 量能 / 择时（斐波那契+分型） / 广度期指 / 两融四维 / ETF 份额脉搏 |
| `s2` | ②板块：集中度 / 申万涨跌 / 资金流 / 相关性五命题 / 研报业绩（含中报季因子） |
| `proj` | 🔭推演（新第一命题 callout + 五分支推演表 + 观察优先级 + 证伪信号）+ ③情绪全节 |
| `s456` | ④风格 / ⑤龙头（中军坐标表 + 趋势池 + 健康度） / ⑥关键节点表 |
| `s7t` | ⑦持仓（流水 + active 持仓 thesis 核验 + 关注池）+ 🎓老师观点 + 📰行业信息 |
| `s8ops` | ⑧次日计划（核心锚点 + 同类日基率 + 观察清单）+ 🧠认知库对照 + 🔧数据缺口 + footer |

## 组装 + 标签配平校验脚本

写入 chunk 后，用下述 python（放 scratchpad）配平校验再组装。开闭标签数不等则拒绝落盘：

```python
import re
TMP = '<scratchpad>'          # chunk 所在目录
DATE = '2026-07-10'
order = ['head','s0','s1','s2','proj','s456','s7t','s8ops']
body = '\n\n'.join(open(f'{TMP}/b{DATE}_{n}.html').read() for n in order)  # 命名随实际
ok = True
for t in ['section','table','tr','td','th','ul','li','h1','h2','h3','div','p','span','b','nav','a','header','footer','small']:
    o = len(re.findall(rf'<{t}(\s|>)', body)); c = len(re.findall(rf'</{t}>', body))
    if o != c:
        ok = False; print(f'MISMATCH {t}: open={o} close={c}')
print('TAG BALANCE OK' if ok else 'FAIL — 修配平后再组装')
if ok:
    css = open(f'{TMP}/review_style.css').read()
    html = (f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>盘后复盘 · {DATE}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n'
            f'<div class="wrap">\n{body}\n</div>\n</body>\n</html>\n')
    open(f'/Users/alyx/tradeSystem/data/reports/复盘_{DATE}.html','w').write(html)
    print('WROTE report')
```

## 迭代要点

- 逐节增改后重跑配平 + 重建；用户新增证据（老师观点/持仓变动/派生报告补跑）就地织入对应 chunk 再重建。
- 报告全程 `[事实]`/`[判断]` 分标；红线见 `multi-agent-review.md`。
- 量能一律用镜像综指口径自算值（深市腿 399106，见 `multi-agent-review.md` 口径基线）。
