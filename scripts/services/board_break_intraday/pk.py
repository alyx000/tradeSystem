"""固定时点断板候选两两比较。模型裁决对局，程序核对证据并计算名次。"""
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations, islice
import json
import subprocess
import tempfile
import time

from services.board_break.pk import rank_matches
from utils.llm_cli import build_prompt_command, resolve_config
from .data import digest


POLICY = 'intraday-pairwise-v1'
BATCH_SIZE = 15
WORKERS = 2
METRICS = {
    'height': '原连板高度', 'open_pct': '开盘涨幅', 'pct_chg': '截止涨幅',
    'from_open_pct': '开盘至截止涨幅', 'high_pct': '窗口最高涨幅',
    'low_pct': '窗口最低涨幅', 'from_high_pct': '较窗口高点涨幅',
    'from_low_pct': '较窗口低点涨幅', 'last_5m_pct': '最后5分钟涨幅',
}
ADVANTAGES = {
    'height': '原连板高度更高', 'open_pct': '开盘相对昨收表现更强', 'pct_chg': '截止相对昨收表现更强',
    'from_open_pct': '开盘至截止表现更强', 'high_pct': '窗口内触及的相对高点更高',
    'low_pct': '窗口最低点相对昨收更高', 'from_high_pct': '从窗口高点回落更少',
    'from_low_pct': '从窗口低点反弹更多', 'last_5m_pct': '最后5分钟表现更强',
}
PROMPT = """根据输入时点已经核验的证据，逐场比较两只断板候选在该时点的相对强弱。
尾盘比较至14:50的走势；开盘复核使用今天09:31—09:40的独立走势，原连板高度仅作历史背景。
先看当时表现、开盘至截止的修复或走弱、高点回落与尾段变化，再结合原连板高度衡量分歧。
高点回落幅度较大表示留存较弱，低点反弹大可能来自先大幅下挫，不能孤立断言更强。
不同股票的绝对价格、成交额大小不能直接衡量承接，不从行业名称推断强势或带动。
只用给定事实，不预测后续行情，不引用盘后评分、其他场次名次或手写准入作为名次，不给交易建议。
path 中每个时刻数值是该根闭合分钟收盘相对当日参考昨收的涨跌幅%，first_high_time/first_low_time为窗口首次高低点。
height和industry的事实日均为baseline_date；开盘时不将原连板高度说成昨日连板。
每场选1—2个有具体差异、能解释取舍的指标键；第1个是决定胜负的指标，胜方的该指标数值必须严格更大。
第2个可列败方的反向优势或胜方佐证。当指标有明显冲突时优先列败方最重要的优势，程序将明确写出双方的实际取舍。
from_high_pct 是负数或零，更大意味着从高点回落更少。from_low_pct 与 high_pct 仅作补充，不得单独决定胜负。
若时点证据相同或不足以区分，winner填undetermined，fields置空，不凭名称或代码决定胜负。
只输出严格JSON，其中evidence_sha256原样回传；matches内每项只有id、winner（A或B或undetermined）和fields数组。
例如 {"id":1,"winner":"A","fields":["pct_chg","last_5m_pct"]}。每个指定id恰好一次，程序按原值撰写交手理由。"""


def _model_runner(config):
    def run_batch(request, timeout):
        prompt = PROMPT + '\n' + json.dumps(request, ensure_ascii=False, allow_nan=False)
        command = build_prompt_command(config, prompt, sandbox=True, mode='plan')
        if config.provider in {'openai', 'lmstudio'}:
            command[-2:] = ['--prompt-stdin']
            kwargs = {'input': prompt}
        else:
            kwargs = {'stdin': subprocess.DEVNULL}
        with tempfile.TemporaryDirectory(prefix='board-break-pk-') as cwd:
            result = subprocess.run(command, text=True, capture_output=True, cwd=cwd,
                                    timeout=min(config.timeout_seconds, timeout), **kwargs)
        if result.returncode:
            raise ValueError('模型进程失败')
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise ValueError('模型未返回有效JSON') from None
    return run_batch


def _request(evidence, cards, pairs):
    codes = {code for pair in pairs for code in (pair['a'], pair['b'])}
    request = {key: evidence[key] for key in ('date', 'stage', 'mode', 'cutoff', 'previous_trade_day',
                                               'baseline_date', 'baseline_fields')}
    request.update(policy=POLICY, metrics=METRICS, candidates=[cards[code] for code in sorted(codes)], pairs=pairs)
    request['evidence_sha256'] = digest(request)
    return request


def _value(key, value):
    return f'{value:g}板' if key == 'height' else f'{value:+g}%'


def _verdict(item, pair, cards):
    if not isinstance(item, dict) or set(item) != {'id', 'winner', 'fields'}:
        raise ValueError('裁决字段错误')
    if item['winner'] == 'undetermined':
        raise ValueError('证据不足以区分双方')
    if item['winner'] not in ('A', 'B'):
        raise ValueError('胜方非法')
    fields = item['fields']
    if (not isinstance(fields, list) or not 1 <= len(fields) <= 2
            or not all(isinstance(key, str) and key in METRICS for key in fields)
            or len(set(fields)) != len(fields) or fields[0] in {'from_low_pct', 'high_pct'}):
        raise ValueError('缺少可核验的决定项或比较指标')
    a, b = cards[pair['a']], cards[pair['b']]
    winner = pair[item['winner'].lower()]
    reasons = []
    for index, key in enumerate(fields):
        av, bv = a.get(key), b.get(key)
        if type(av) not in (int, float) or type(bv) not in (int, float) or av == bv:
            raise ValueError('比较指标无数值或无法区分双方')
        stronger = a if av > bv else b
        if index == 0 and stronger['code'] != winner:
            raise ValueError('胜方与决定项的实际数值方向矛盾')
        role = '决定项' if index == 0 else ('佐证' if stronger['code'] == winner else '对手优势')
        values = f'{a["name"]} {_value(key, av)}、{b["name"]} {_value(key, bv)}'
        reasons.append(f'{role}为{METRICS[key]}，{stronger["name"]}{ADVANTAGES[key]}（{values}）')
    return dict(pair, state='valid', winner=winner, fields=fields, reason='；'.join(reasons))


def _validate(request, response, cards):
    if (not isinstance(response, dict) or set(response) != {'matches', 'evidence_sha256'}
            or response['evidence_sha256'] != request['evidence_sha256']
            or not isinstance(response['matches'], list)):
        raise ValueError('裁决格式或证据哈希不符')
    pairs = {p['id']: p for p in request['pairs']}
    by_id = {}
    for item in response['matches']:
        idx = item.get('id') if isinstance(item, dict) else None
        if type(idx) is not int or idx not in pairs or idx in by_id:
            raise ValueError('对局编号重复或超出本批')
        by_id[idx] = item
    matches, errors = [], []
    for idx, pair in pairs.items():
        try:
            if idx not in by_id:
                raise ValueError('模型漏判对局')
            matches.append(_verdict(by_id[idx], pair, cards))
        except ValueError as exc:
            errors.append(dict(pair, reason=str(exc)))
    return matches, errors


def _play_batch(request, cards, runner, deadline, clock):
    receipt = dict(first_pair=request['pairs'][0]['id'], count=len(request['pairs']),
                   evidence_sha256=request['evidence_sha256'])
    try:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError
        response = runner(request, remaining)
        if clock() >= deadline:
            raise TimeoutError
        matches, errors = _validate(request, response, cards)
        receipt.update(response_sha256=digest(response), errors=errors)
        if errors:
            receipt['error'] = errors[0]['reason']
        return matches, receipt
    except (TimeoutError, subprocess.TimeoutExpired):
        receipt['error'] = 'PK超时'
    except OSError:
        receipt['error'] = '模型启动失败'
    except (ValueError, TypeError) as exc:
        receipt['error'] = str(exc)
    return [], receipt


def _rank_rows(cards, matches):
    wins = dict.fromkeys(cards, 0)
    for match in matches:
        wins[match['winner']] += 1
    ranks = rank_matches(list(cards), wins, matches)
    rows = []
    for code in sorted(cards, key=lambda c: (ranks[c], c)):
        opponents = {}
        for match in matches:
            if code in (match['a'], match['b']):
                opponent = match['b'] if match['a'] == code else match['a']
                opponents[opponent] = match
        same_wins = [other for other in opponents if wins[other] == wins[code]]
        direct = sum(opponents[other]['winner'] == code for other in same_wins)
        losses = len(opponents) - wins[code]
        reason = f'共{wins[code]}胜{losses}负'
        if not opponents:
            reason = '该时点仅此一只数值候选，列第1，无对手可比较'
        elif same_wins:
            reason += f'，同胜场组{len(same_wins) + 1}只、组内{direct}胜'
            if list(ranks.values()).count(ranks[code]) > 1:
                reason += '，组内胜场亦相同，并列'
        # 分别列与自己名次最接近的一场胜负；完整对局另行展示。
        beaten = [other for other, match in opponents.items() if match['winner'] == code]
        lost = [other for other, match in opponents.items() if match['winner'] != code]
        for action, group in [('胜', beaten), ('负于', lost)]:
            if group:
                other = min(group, key=lambda c: (abs(ranks[c] - ranks[code]), c))
                reason += f'。{action}{cards[other]["name"]}：{opponents[other]["reason"]}'
        rows.append(dict(code=code, name=cards[code]['name'], rank=ranks[code], wins=wins[code],
                         losses=losses, direct_wins=direct, reason=reason))
    return rows


def run(evidence, *, runner=None, budget_seconds=180, clock=time.monotonic):
    """只接受 pk_evidence 生成的证据卡。runner(request, timeout) 返回结构化裁决。"""
    cards = {card['code']: card for card in evidence['candidates']}
    total = len(cards) * (len(cards) - 1) // 2
    result = dict(policy=POLICY, status='incomplete', reason='', total=total, matches=[], rows=[], batches=[],
                  evidence=evidence, evidence_sha256=digest(evidence), prompt_sha256=digest(PROMPT))
    if len(cards) != len(evidence['candidates']):
        result['reason'] = '数值候选代码重复'
        return result
    if not cards:
        result.update(status='not_needed', reason='数值候选为0只，无对局')
        return result
    if len(cards) == 1:
        result.update(status='complete', rows=_rank_rows(cards, []), reason='仅一只候选，无需两两比较')
        return result
    if runner is None:
        config = resolve_config(default_timeout=180)
        result.update(provider=config.provider, model=config.model)
        runner = _model_runner(config)
    deadline = clock() + budget_seconds
    pair_iter = (dict(id=i, a=a, b=b) for i, (a, b) in enumerate(combinations(sorted(cards), 2), 1))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while len(result['matches']) < total:
            if clock() >= deadline:
                result['reason'] = 'PK超时'
                return result
            jobs = []
            for _ in range(WORKERS):
                pairs = list(islice(pair_iter, BATCH_SIZE))
                if not pairs:
                    break
                request = _request(evidence, cards, pairs)
                jobs.append(pool.submit(_play_batch, request, cards, runner, deadline, clock))
            for job in jobs:
                matches, receipt = job.result()
                result['matches'].extend(matches)
                result['batches'].append(receipt)
            errors = [batch['error'] for batch in result['batches'] if 'error' in batch]
            if errors:
                result['reason'] = '对局未完整核验：' + errors[0]
                return result
    result.update(status='complete', rows=_rank_rows(cards, result['matches']), reason='全体数值候选逐对完成比较')
    return result
