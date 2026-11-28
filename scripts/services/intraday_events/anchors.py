"""从上一交易日晚的已校验手写复盘提取固定对象；身份仅作精确匹配，不猜简称。"""
from datetime import datetime
from pathlib import Path
import re

from analyzers.strong_stock_feedback import _code
from services.handwritten_review import DEFAULT_ARCHIVE, load_entry_before
from .levels import LEVEL_PARSER_VERSION, extract_levels


def _plain_code(cell):
    return re.sub(r'`(\d{6}\.(?:SH|SZ|BJ))`$', r'\1', cell)


def _fixed_columns(header):
    if len(header) >= 2 and header[1] in ('固定对象', '固定对象／原称'):
        return 1, 0
    if len(header) >= 2 and header[0] in ('对象（名称＋已核验代码）', '对象（名称及已核验代码）'):
        return 0, 1
    if header[:3] in (['优先级', '观察组', '新增股票'], ['优先级', '观察组', '核心股票']):
        return 2, 1
    return None


def _next_session_targets(section):
    targets = {}
    in_table = False
    for line in section.splitlines():
        if not line.lstrip().startswith('|'):
            in_table = False
            continue
        first = line.strip().strip('|').split('|', 1)[0].strip()
        if first == 'ID／对象':
            in_table = True
        elif in_table:
            match = re.fullmatch(r'(?:\S+\s+)?([^\s|]+)\s+(\d{6}\.(?:SH|SZ|BJ))', _plain_code(first))
            if match:
                targets[match.groups()] = line
    return targets


def load_anchors(basic_rows, day, previous_date, *, root=DEFAULT_ARCHIVE):
    result = dict(status='partial', stocks=[], key_levels=[], level_parser_version=LEVEL_PARSER_VERSION,
                  unresolved=[], sources=[], errors=[],
                  expected_source_date=previous_date, scope=f'仅跟踪上一交易日（{previous_date}）晚间复盘中的固定对象',
                  condition_status='未自动判定原文条件；仅记录价格异动')
    cutoff = datetime.fromisoformat(day+'T09:30:00+08:00')
    folder = Path(root) / previous_date
    if not folder.is_dir():
        result['errors'].append(f'{previous_date} 上一交易日手写复盘缺失')
        return result
    try:
        # 先校验整条链，最新损坏时不能回退。完整链中的晚修订则按as-of选择。
        entry, latest, ignored = load_entry_before(folder, cutoff)
        if ignored:
            result['errors'].append(f'{folder.name}开盘后版本{ignored}未采用，沿用事前版本{entry["meta"].get("revision",0)}')
        result['sources'].append(dict(date=folder.name, path=str(folder),
            source_sha256=entry['meta']['source_sha256'],
            supplement_sha256=entry['meta']['supplement_sha256'],
            recorded_at=entry['meta']['recorded_at'], revision=entry['meta'].get('revision', 0),
            latest_recorded_at=latest['meta']['recorded_at'], ignored_late_revisions=ignored,
            raw=entry['raw'], fixed_objects=entry['sections']['固定对象跨日反馈']))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['errors'].append(f'{folder.name}：{exc}')
        return result
    # 整理表负责定位固定对象，原文负责证明曾点名；两者保留不同来源语义。
    names = {}
    for row in basic_rows:
        name = row.get('name')
        if isinstance(name, str) and name:
            names.setdefault(name, set()).add(_code(row.get('ts_code') or row.get('code')))
    by_code, unresolved = {}, {}
    table_seen = False
    lines = entry['sections']['固定对象跨日反馈'].splitlines()
    targets = _next_session_targets(entry['sections']['次日具体条件'])
    columns = None
    for i, line in enumerate(lines):
        if not line.strip().startswith('|'):
            columns = None
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if i+1 < len(lines) and re.fullmatch(r'\s*\|[-: |]+\|\s*', lines[i+1]):
            columns = _fixed_columns(cells)
            table_seen = table_seen or columns is not None
            continue
        if not columns or len(cells) <= max(columns) or re.fullmatch(r'[-: ]+', cells[0]):
            continue
        role = cells[columns[1]]
        for original_name in re.split(r'[、／/；;]', cells[columns[0]]):
            token = _plain_code(original_name.strip())
            token = re.sub(r'（代码均?待核）$', '', token).strip()
            code_match = re.search(r'(\d{6}\.(?:SH|SZ|BJ))$', token)
            name = token[:code_match.start()].strip() if code_match else token
            if not name:
                continue
            evidence = [
                dict(date=previous_date, quote=raw_line, source_sha256=entry['meta']['source_sha256'])
                for raw_line in entry['raw'].splitlines() if name in raw_line
            ]
            matching = names.get(name, set())
            # 科创板行情源可能省略表决权差异标识。仅在显式代码和基础全称同时吻合时接受。
            if not matching and code_match and name.endswith('-W') and code_match.group(1).endswith('.SH'):
                if names.get(name[:-2]) == {code_match.group(1)}:
                    matching = {code_match.group(1)}
            conflict = code_match and matching != {code_match.group(1)}
            target_row = targets.get((name, code_match.group(1))) if code_match else None
            if len(matching) != 1 or not (evidence or target_row) or conflict:
                key = (name, previous_date)
                unresolved[key] = dict(original_name=name, source_date=previous_date,
                    role=role, status='identity_unverified',
                    reason='名称代码不一致' if conflict else '名称未精确唯一匹配证券宇宙' if len(matching)!=1 else '上一交易日手写原文未点名',
                    table_row=line, evidence=evidence)
                continue
            code = next(iter(matching))
            item = by_code.setdefault(code, dict(code=code, name=name,
                source='user_handwritten' if evidence else 'handwritten_review_supplement',
                source_date=previous_date, roles=[], references=[]))
            if role not in item['roles']:
                item['roles'].append(role)
            item['references'].append(dict(table_date=previous_date, table_row=line,
                supplement_sha256=entry['meta']['supplement_sha256'],
                supplement_kind='retrospective', original_mentions=evidence, next_session_target=target_row))
    if not table_seen:
        result['errors'].append(previous_date+'固定对象表头未识别，保留缺口')
    result['stocks'] = sorted(by_code.values(), key=lambda row:row['code'])
    result['key_levels'] = extract_levels(entry['sections']['次日具体条件'],
                                          result['stocks'], targets, result['sources'][0], day)
    result['unresolved'] = list(unresolved.values())
    result['status'] = 'partial' if result['errors'] or unresolved else 'complete'
    result['latest_source_date'] = previous_date
    return result
