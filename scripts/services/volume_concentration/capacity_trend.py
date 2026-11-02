"""成交榜容量趋势证据；出处 teacher_notes#1089，窗口为工程口径。

只观察当日 Top20，不授予中军身份、不改变选股/排名。MA 使用前复权收盘；
最近5根有效K线不等同连续5个市场交易日（停牌不补零）。
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
import math
import re
import sqlite3
from statistics import mean

from utils.qfq import apply_qfq

SCHEMA = "capacity-trend-v1"
KEY = "capacity_trend"
NOTE = ("[事实] 前复权收盘对比MA20；方向与上一根MA20比较；涨跌按相邻收盘比较。"
        "最近5根有效K线为工程观察窗口，末端连续次数最多5根，停牌不补零。"
        "覆盖限于来源返回的K线，未独立核验内部缺日是否停牌。"
        "复权源身份依赖单票请求绑定，未独立核验逐行身份。"
        "[判断] 成交额前排仅为容量观察对象，不自动认定中军或板块趋势；出处：鞠磊笔记#1089。")


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不是价格或因子")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("价格或因子非正/非有限")
    return number


def _stock_identity(value):
    text = str(value or "").strip().upper()
    bare, dot, suffix = text.partition(".")
    if len(bare) != 6 or not bare.isascii() or not bare.isdigit():
        raise ValueError("股票身份非法")
    if dot:
        if suffix not in {"SH", "SZ", "BJ"}:
            raise ValueError("股票交易所后缀非法")
    else:
        # 与当前 Tushare 单票入口的裸码归一口径一致。
        suffix = ("BJ" if bare.startswith(("43", "82", "83", "87", "88", "89", "92"))
                  else "SH" if bare.startswith(("60", "68", "90", "51", "52", "53", "56", "58"))
                  else "SZ")
    return f"{bare}.{suffix}"


def analyze(bars, factors, target_date):
    """不删脏行、不回退未复权；重复、未来、错序及陈旧行情均失败。"""
    try:
        date.fromisoformat(target_date)
        if not isinstance(bars, list) or not bars or not isinstance(factors, list) or not factors:
            raise ValueError("行情或复权因子缺失")
        dates = [date.fromisoformat(b["trade_date"]).isoformat() for b in bars]
        if dates != sorted(set(dates)) or dates[-1] != target_date:
            raise ValueError("行情重复、错序、未来或目标日缺失")
        factor_dates = [date.fromisoformat(f["trade_date"]).isoformat() for f in factors]
        if len(set(factor_dates)) != len(factor_dates) or any(d > target_date for d in factor_dates):
            raise ValueError("复权因子重复或包含未来日期")
        for b in bars:
            _positive(b["close"])
        for f in factors:
            _positive(f["adj_factor"])
        adjusted = apply_qfq(bars, factors, keys=("close",))
        if not adjusted:
            raise ValueError("复权因子不能完整匹配行情")
        # 25根提供最近5个涨跌、5根MA20及其上一根MA20。
        if len(adjusted) < 25:
            return dict(status="partial", reason="有效K线不足25根", available_bars=len(adjusted))
        closes = [_positive(b["close"]) for b in adjusted]
        ma_now, ma_prev = mean(closes[-20:]), mean(closes[-21:-1])
        moves = [(a > b) - (a < b) for a, b in zip(closes[-5:], closes[-6:-1])]
        last = moves[-1]
        streak = 0
        for move in reversed(moves):
            if move != last:
                break
            streak += 1
        position = "上方" if closes[-1] > ma_now else "下方" if closes[-1] < ma_now else "持平"
        direction = "向上" if ma_now > ma_prev else "向下" if ma_now < ma_prev else "持平"
        return dict(status="complete", price_basis="qfq", available_bars=len(closes),
                    window_dates=dates[-5:], previous_bar_date=dates[-2],
                    close_qfq=closes[-1], ma20=ma_now, ma20_previous=ma_prev,
                    ma20_position=position, ma20_direction=direction,
                    ma20_distance_pct=round((closes[-1]/ma_now-1)*100, 4),
                    up_count=moves.count(1), down_count=moves.count(-1), flat_count=moves.count(0),
                    end_run_direction={1: "连涨", -1: "连跌", 0: "连平"}[last], end_run_bars=streak)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return dict(status="source_failed", reason=str(exc))


def collect(registry, stocks, target_date):
    """最多20票；逐票先行情再因子，一票失败不影响其余票及集中度主报告。"""
    start = (date.fromisoformat(target_date)-timedelta(days=90)).isoformat()
    rows = []
    seen = set()
    for stock in stocks[:20]:
        code = str(stock.get("code") or "")
        row = {k: stock.get(k) for k in ("code", "name", "industry", "amount_billion")}
        try:
            identity = _stock_identity(code)
            if identity in seen:
                raise ValueError("股票身份缺失或重复")
            seen.add(identity)
            # 当前复权 provider 契约只返回日期/因子值，行情则带请求绑定的 ts_code。
            # 不能把复权契约允许的缺字段判作失败；其身份依赖单票请求，不宣称独立核验。
            # 但只要返回任一身份字段，就必须连同交易所完整匹配，两个字段冲突也拒绝。
            row["identity_basis"] = "single_stock_request_with_optional_row_check"
            quote = registry.call("get_stock_daily_range", code, start, target_date)
            row["quote_source"] = getattr(quote, "source", None)
            if not quote.success or not quote.data:
                raise ValueError("日线来源失败或为空")
            factor = registry.call("get_stock_adj_factor_range", code, start, target_date)
            row["factor_source"] = getattr(factor, "source", None)
            if not factor.success or not factor.data:
                raise ValueError("复权因子来源失败或为空")
            for data in (quote.data, factor.data):
                for item in data:
                    if data is quote.data and not any(field in item for field in ("ts_code", "code")):
                        raise ValueError("行情缺失证券身份")
                    for field in ("ts_code", "code"):
                        if field in item and _stock_identity(item[field]) != identity:
                            raise ValueError("行情或复权因子身份不匹配")
                    if item.get("trade_date", "") < start:
                        raise ValueError("返回日期早于请求范围")
            row.update(analyze(quote.data, factor.data, target_date))
        except Exception as exc:  # provider失败隔离；错误仍进入逐票收据
            row.update(status="source_failed", reason=str(exc))
        rows.append(row)
    complete = sum(r["status"] == "complete" for r in rows)
    status = ("complete" if rows and complete == len(rows) else
              "source_failed" if not rows or all(r["status"] == "source_failed" for r in rows) else "partial")
    return dict(schema=SCHEMA, trade_date=target_date, status=status,
                universe="当日成交额Top20", window_bars=5, ma_period=20,
                coverage_basis="returned_bars_only",
                source_note_id=1089, rows=rows, complete_count=complete, total_count=len(rows))


def preserve_complete(current, previous):
    """仅相同日期、相同观察对象的完整旧块可保留；当前失败收据不会被隐藏。"""
    def identities(block):
        return [(r.get("code"), r.get("industry"), r.get("name"), r.get("amount_billion"))
                for r in block.get("rows", [])]
    if (isinstance(previous, dict) and previous.get("schema") == SCHEMA
            and previous.get("trade_date") == current["trade_date"]
            and previous.get("status") in {"complete", "fallback_preserved"}
            and previous.get("complete_count") == previous.get("total_count")
            and identities(previous) == identities(current) and current["status"] != "complete"):
        return {**previous, "status": "fallback_preserved", "latest_attempt": current}
    return current


def from_record(record, target_date):
    source = record.get("source") if isinstance(record, dict) else None
    if source is not None and not isinstance(source, dict):
        return dict(status="source_failed", trade_date=target_date, rows=[], reason="来源收据格式非法")
    block = (source or {}).get(KEY)
    if KEY not in (source or {}):
        return dict(status="not_computed", trade_date=target_date, rows=[])
    if not isinstance(block, dict):
        return dict(status="source_failed", trade_date=target_date, rows=[], reason="趋势快照格式非法")
    try:
        _validate_block(block, target_date)
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return dict(status="source_failed", trade_date=target_date, rows=[], reason="快照版本、日期或内容不合法")
    return block


def _validate_block(block, target_date, *, allow_fallback=True):
    if (block['schema'] != SCHEMA or block['trade_date'] != target_date
            or block['window_bars'] != 5 or block['ma_period'] != 20):
        raise ValueError('快照口径不匹配')
    rows = block['rows']
    if not isinstance(rows, list) or not 0 < len(rows) <= 20:
        raise ValueError('观察对象为空或越界')
    complete = sum(row['status'] == 'complete' for row in rows)
    if block['total_count'] != len(rows) or block['complete_count'] != complete:
        raise ValueError('覆盖计数不一致')
    expected = ('complete' if complete == len(rows) else
                'source_failed' if all(r['status'] == 'source_failed' for r in rows) else 'partial')
    if block['status'] == 'fallback_preserved' and allow_fallback:
        if expected != 'complete':
            raise ValueError('回退结果不是完整快照')
        _validate_block(block['latest_attempt'], target_date, allow_fallback=False)
        if block['latest_attempt']['status'] == 'complete':
            raise ValueError('完整刷新不应标记回退')
    elif block['status'] != expected:
        raise ValueError('健康状态与逐票证据不一致')
    for row in rows:
        if row['status'] not in {'complete', 'partial', 'source_failed'}:
            raise ValueError('未知逐票状态')
        if row['status'] != 'complete':
            continue
        for key in ('close_qfq', 'ma20', 'ma20_previous'):
            _positive(row[key])
        # 持久化数值必须是JSON数值；不能校验时转float、渲染时却继续用原字符串。
        distance = row['ma20_distance_pct']
        if type(distance) not in (int, float) or not math.isfinite(distance):
            raise ValueError('MA20距离非法')
        if row['ma20_position'] not in {'上方', '下方', '持平'} or row['ma20_direction'] not in {'向上', '向下', '持平'}:
            raise ValueError('均线状态非法')
        counts = [row[k] for k in ('up_count', 'down_count', 'flat_count')]
        if any(type(n) is not int or not 0 <= n <= 5 for n in counts) or sum(counts) != 5:
            raise ValueError('涨跌计数非法')
        if (row['end_run_direction'] not in {'连涨', '连跌', '连平'}
                or type(row['end_run_bars']) is not int or not 1 <= row['end_run_bars'] <= 5):
            raise ValueError('末端连续计数非法')
        dates = row['window_dates']
        if len(dates) != 5 or dates != sorted(set(dates)) or dates[-1] != target_date or row['price_basis'] != 'qfq':
            raise ValueError('观察窗口非法')


def read_snapshot(target_date, db_path=None):
    """HTML组装只读目标日，不用最近快照替代、不触发网络或迁移。"""
    from db.connection import get_readonly_connection
    from .repo import get_concentration
    conn = None
    try:
        conn = get_readonly_connection(db_path) if db_path is not None else get_readonly_connection()
        return with_history(conn, get_concentration(conn, target_date), target_date)
    except (sqlite3.Error, ValueError, TypeError, OSError):
        return dict(status="source_failed", trade_date=target_date, rows=[], reason="目标日快照不可读")
    finally:
        if conn is not None:
            conn.close()


def with_history(conn, record, target_date, days=10):
    """只读派生，不把历史汇总回写逐股证据或覆盖旧审计。"""
    from .capacity_history import build
    return {**from_record(record, target_date),
            'history_view': build(conn, target_date, current_record=record, days=days)}


def _cells(row):
    identity = f"{row.get('name') or '名称缺失'} {row.get('code') or '代码缺失'}"
    industry = row.get("industry") or "未分类"
    if row.get("status") != "complete":
        return [identity, industry, "—", "—", "—", f"{row.get('status')}：{row.get('reason', '未计算')}"]
    return [identity, industry,
            f"{row['ma20_position']}（{row['ma20_distance_pct']:+.2f}%）", row["ma20_direction"],
            f"涨{row['up_count']}／跌{row['down_count']}／平{row['flat_count']}",
            f"{row['end_run_direction']}{row['end_run_bars']}根"]


def _summary(block, *, compact=False):
    status = block["status"]
    if status == "not_computed":
        return "成交榜容量趋势：未计算，旧快照没有此项，不代表趋势不成立。"
    if 'total_count' not in block:
        return f"成交榜容量趋势：{status}，数据不可用（{block.get('reason', '未核验')}）。"
    label = {'complete': '可计算', 'partial': '部分覆盖（partial），可计算',
             'source_failed': '来源失败（source_failed），可计算',
             'fallback_preserved': '沿用旧结果（fallback_preserved），可计算'}.get(status, status)
    prefix = label if compact else f'成交榜容量趋势：{status}，可计算'
    text = f"{prefix} {block.get('complete_count', 0)}/{block.get('total_count', 0)}。"
    valid = [r for r in block.get('rows', []) if r.get('status') == 'complete']
    if valid:
        rising = sum(r['ma20_position'] == '上方' and r['ma20_direction'] == '向上' for r in valid)
        falling = sum(r['ma20_position'] == '下方' and r['ma20_direction'] == '向下' for r in valid)
        text += (f"MA20线上且向上{rising}只｜线下且向下{falling}只。" if compact else
                 f"可计算样本中，线上且线向上{rising}只，线下且线向下{falling}只。")
    if status == "fallback_preserved":
        attempt = block.get("latest_attempt") or {}
        text += f"本次刷新{attempt.get('status', '失败')}，展示同日已有完整结果；本次可计算{attempt.get('complete_count', 0)}/{attempt.get('total_count', 0)}。"
        failed = [str(r.get('code') or '代码缺失') for r in attempt.get('rows', []) if r.get('status') != 'complete']
        if failed and not compact:
            text += "本次缺口：" + "、".join(failed) + "。"
    return text


def _history_markdown(view, cell, budget):
    from .capacity_history import tables, gaps
    footer = ('[事实] 日报历史为限长摘要；完整板块历史见 '
              f"volume-watch trend --date {view['current']['date']} --days {view['days']} 或正式复盘HTML。")
    footer = cell(footer) if budget is not None else ''
    parts = []

    def add(text):
        if budget is not None and len('\n'.join(['', *parts, text, footer]).encode('utf-8')) > budget:
            return False
        parts.append(text)
        return True

    if budget is not None and len(('\n' + footer).encode('utf-8')) > budget:
        return []
    add(cell(view['note']))
    if gaps(view):
        add(cell(gaps(view)))
    for title, headers, rows in tables(view):
        if budget is not None and title == '板块历史变化':
            rows = [r for r in rows if r[0] == view['current']['date']]
            title += '（仅最近一日对照）'
        heading = '\n#### ' + title
        if not rows:
            add(heading + '\n未计算／无可展示记录。')
            continue
        heading += '\n| ' + ' | '.join(map(cell, headers)) + ' |\n|' + '---|' * len(headers)
        # 表头与首行一起加入，不能留下貌似空结果的孤立表头。
        rendered = ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
        if not add(heading + '\n' + rendered[0]):
            break
        for row in rendered[1:]:
            if not add(row):
                break
    if footer:
        parts.append(footer)
    return ['', *parts]


def render_markdown(block, *, history_budget=None, compact=False):
    def cell(value):
        text = escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
        return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)
    if compact:
        from .capacity_digest import observations
        lines = ['', '### 成交榜容量股变化（MA20）', '', '- 观察日期：' + cell(block['trade_date'])]
        if block['status'] != 'complete':
            lines += ['- ⚠ ' + cell(_summary(block, compact=True))]
        lines += ['- ' + cell(line) for line in observations(block)]
        days = (block.get('history_view') or {}).get('days', 10)
        lines += ['', '仅成交榜样本、前复权口径；每类最多3只，新变化优先，其余按成交榜顺序。完整变化见复盘HTML或：',
                  cell(f"volume-watch trend --date {block['trade_date']} --days {days}")]
        return '\n'.join(lines)
    lines = ["", "### 成交榜容量趋势（MA20）", "", "- 观察日期：" + cell(block['trade_date']),
             "- [事实] " + cell(_summary(block, compact=compact))]
    lines += [NOTE]
    from .capacity_digest import observations
    lines += ['', '#### 完整个股变化', *['- ' + cell(line) for line in observations(block, limit=None)]]
    if block.get('history_view'):
        lines += _history_markdown(block['history_view'], cell, history_budget)
    if block.get("rows"):
        lines += ["", "| 股票 | 申万二级 | 收盘对MA20 | MA20方向 | 近5根涨跌 | 末端连续／缺口 |",
                  "|---|---|---|---|---|---|"]
        lines += ["| " + " | ".join(map(cell, _cells(row))) + " |" for row in block["rows"]]
    return "\n".join(lines)


def render_html(block):
    from .capacity_digest import observations
    summary = _summary(block, compact=True)
    html = [f'<div data-capacity-trend="v1" data-as-of="{escape(block["trade_date"], quote=True)}" data-source-status="{escape(block["status"], quote=True)}">',
            '<p><strong>成交榜容量股变化（MA20）</strong></p>',
            f'<p>观察日期：{escape(block["trade_date"])}</p>']
    if block['status'] != 'complete':
        html += [f'<p>⚠ {escape(summary)}</p>']
    html += ['<ul>' + ''.join(f'<li>{escape(line)}</li>' for line in observations(block)) + '</ul>',
             '<p>仅成交榜样本、前复权口径；每类最多3只，新变化优先，其余按成交榜顺序。完整变化可展开。</p>']
    html += ['<details><summary>完整个股变化</summary><ul>' +
             ''.join(f'<li>{escape(line)}</li>' for line in observations(block, limit=None)) + '</ul></details>']
    history_gap = None
    history_warning = None
    if block.get('history_view'):
        from .capacity_history import tables, gaps, gap_summary
        view = block['history_view']
        history_gap = gaps(view)
        history_warning = gap_summary(view)
        for title, headers, rows in tables(view):
            html += [f'<details><summary>{escape(title)}</summary>']
            if rows:
                html += ['<div style="overflow-x:auto;max-width:100%"><table><thead><tr>' +
                         ''.join(f'<th>{escape(h)}</th>' for h in headers) + '</tr></thead><tbody>']
                html += ['<tr>' + ''.join(f'<td>{escape(str(x))}</td>' for x in row) + '</tr>' for row in rows]
                html += ['</tbody></table></div>']
            else:
                html += ['<p>未计算／无可展示记录。</p>']
            html += ['</details>']
    if block.get("rows"):
        html += [f'<details><summary>成交榜容量趋势明细（{len(block["rows"])}只）</summary>',
                 '<div style="overflow-x:auto;max-width:100%"><table><thead><tr>' + ''.join(f'<th>{x}</th>' for x in
                 ('股票', '申万二级', '收盘对MA20', 'MA20方向', '近5根涨跌', '末端连续／缺口')) + '</tr></thead><tbody>']
        html += ['<tr>' + ''.join(f'<td>{escape(str(x))}</td>' for x in _cells(row)) + '</tr>' for row in block['rows']]
        html += ['</tbody></table></div></details>']
    html += ['<details><summary>历史缺口、口径与来源</summary>']
    html += [f'<p>{escape(_summary(block))}</p>']
    if history_gap:
        html += [f'<p>{escape(history_gap)}</p>']
    html += [f'<p>{escape(NOTE)}</p>']
    if block.get('history_view'):
        html += [f'<p>{escape(block["history_view"]["note"])}</p>']
    html += ['</details>']
    html += ['</div>']
    gap = '；'.join(filter(None, [summary if block['status'] != 'complete' else None, history_warning]))
    return '\n'.join(html), gap or None
