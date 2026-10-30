"""Read-only projection of archived daily hot-theme rankings, not constituents."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def _stock_details(raw, day, themes):
    """Keep sampled membership separate from cause-tag matches and original counts."""
    from services.review_identity_flows import _source
    stamp = day.replace('-', '')
    result = dict(status='partial', members={}, unverified_members={}, reasons={}, membership_status='missing-data',
                  reason_status='missing-data', sampled_stocks=0,
                  reason_rule='按 + 分隔的原因标签精确匹配题材名；机器人概念另匹配机器人、人形机器人、焊接机器人、机器人电机、智元机器人；风电另匹配海上风电、风电设备。原因标签不是完整题材成分。')
    try:
        up = {r['ts_code']: r for r in _source(raw, stamp+'_U', 'limit_list_d', stamp, limit_type='U')}
        if any(not isinstance(r.get('name'), str) or not r['name'].strip() for r in up.values()):
            raise ValueError('涨停股名称缺失')
        result['up_total'] = len(up)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return dict(result, status='source_failed', reason=str(exc))
    sources = raw.get(stamp+'_theme_member_samples')
    if sources is not None:
        try:
            if not isinstance(sources, list) or not sources:
                raise ValueError('当日成员抽样归档为空')
            seen, members, unverified = set(), {}, {}
            for source in sources:
                code = source.get('params', {}).get('con_code')
                fetched = datetime.fromisoformat(source.get('fetched_at', ''))
                if (source.get('api') != 'ths_member' or source.get('status') != 'complete' or
                        fetched.tzinfo is None or fetched.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() != day or
                        code not in up or code in seen):
                    raise ValueError('成员抽样日期、来源或股票身份不符')
                rows, pages = source.get('rows'), source.get('pages')
                if (not isinstance(rows, list) or not isinstance(pages, list) or not pages or
                        any(type(n) is not int or n < 0 or n > 2000 for n in pages) or
                        pages[-1] != 0 or any(n == 0 for n in pages[:-1]) or sum(pages) != len(rows)):
                    raise ValueError('成员抽样分页未闭合')
                theme_codes = set()
                for row in rows:
                    theme = row.get('ts_code')
                    if (row.get('con_code') != code or row.get('con_name') != up[code]['name'] or
                            not isinstance(theme, str) or not re.fullmatch(r'\d{6}\.TI', theme) or theme in theme_codes):
                        raise ValueError('成员抽样联结或名称冲突')
                    theme_codes.add(theme)
                    current = row.get('is_new')
                    if current not in (None, '', 'Y', 'N'):
                        raise ValueError('成员有效状态非法')
                    # Capture date proves when the response was seen, not whether each relationship is active.
                    if current != 'N':
                        target = members if current == 'Y' else unverified
                        target.setdefault(theme, []).append(dict(code=code, name=up[code]['name']))
                seen.add(code)
            result.update(members=members, unverified_members=unverified, sampled_stocks=len(seen), membership_status='partial')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            result.update(membership_status='source_failed', membership_reason=str(exc))
    if stamp+'_theme_causes' in raw:
        try:
            rows = _source(raw, stamp+'_theme_causes', 'limit_list_ths', stamp, limit_type='涨停池')
            if any(r.get('limit_type') != '涨停池' or not isinstance(r.get('lu_desc'), str) or
                   not r['lu_desc'].strip() or not isinstance(r.get('name'), str) or not r['name'].strip() for r in rows):
                raise ValueError('涨停原因明细类型或名称缺失')
            reasons = {}
            aliases = {'机器人概念': {'机器人', '人形机器人', '焊接机器人', '机器人电机', '智元机器人'},
                       '风电': {'海上风电', '风电设备'}}
            for row in rows:
                code = row['ts_code']
                if code not in up:
                    continue  # Only display stocks independently present in the same-day U pool.
                if row['name'] != up[code]['name']:
                    raise ValueError('双来源股票名称冲突')
                tags = {t.strip() for t in row['lu_desc'].split('+')}
                for theme in themes:
                    matches = sorted(tags & ({theme['name']} | aliases.get(theme['name'], set())))
                    if matches:
                        reasons.setdefault(theme['code'], []).append(dict(code=code, name=row['name'],
                            cause=row['lu_desc'], matched_tags=matches))
            result.update(reasons=reasons, reason_status='complete', cause_rows=len(rows),
                          cause_only_codes=sorted({r['ts_code'] for r in rows}-up.keys()),
                          no_cause_codes=sorted(up.keys()-{r['ts_code'] for r in rows}))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            result.update(reason_status='source_failed', cause_reason=str(exc))
    return result


def _integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d+', value):
        return int(value)
    raise ValueError('题材榜计数或排名非法')


def _ranking(source, day):
    if not isinstance(source, dict) or source.get('status') != 'success':
        raise ValueError('题材榜原始归档缺失或失败')
    if (source.get('interface_name') != 'limit_cpt_list' or source.get('provider') != 'tushare:limit_cpt_list' or
            source.get('biz_date') != day or source.get('target_date') != day):
        raise ValueError('题材榜来源或事实日期不符')
    serialized = source.get('payload_json')
    if not isinstance(serialized, str) or hashlib.sha256(serialized.encode()).hexdigest() != source.get('payload_hash'):
        raise ValueError('题材榜原始内容哈希不符')
    payload = json.loads(serialized)
    if (payload.get('biz_date') != day or payload.get('interface_name') != 'limit_cpt_list' or
            payload.get('provider') != 'tushare:limit_cpt_list' or payload.get('params', {}).get('trade_date') != day.replace('-', '')):
        raise ValueError('题材榜信封日期或请求不符')
    rows = payload.get('rows')
    if not isinstance(rows, list) or not rows or type(source.get('row_count')) is not int or source['row_count'] != len(rows) or payload.get('summary',{}).get('row_count') != len(rows):
        raise ValueError('题材榜行数缺失或不一致')
    records = []
    for row in rows:
        code, name = row.get('ts_code'), row.get('name')
        if (not isinstance(code, str) or not re.fullmatch(r'\d{6}\.TI',code) or
                not isinstance(name,str) or not name.strip() or row.get('trade_date') != day.replace('-','')):
            raise ValueError('题材榜身份或行日期非法')
        count, consecutive, rank = (_integer(row.get(k)) for k in ('up_nums','cons_nums','rank'))
        if consecutive > count or rank < 1:
            raise ValueError('题材连板数或排名冲突')
        records.append(dict(code=code,name=name.strip(),count=count,consecutive=consecutive,rank=rank))
    if len({r['code'] for r in records}) != len(records) or sorted(r['rank'] for r in records) != list(range(1,len(records)+1)):
        raise ValueError('题材榜重复身份或排名不连续')
    return sorted(records,key=lambda r:r['rank'])


def theme_heatmap(raw, manifest, day):
    if not isinstance(raw,dict) or not any(k.endswith('_theme_ranking') for k in raw):
        return dict(status='missing-data',reason='缺少逐日热门题材榜归档；不以当前成分回填历史')
    try:
        dates=manifest.get('trade_dates',[])
        if manifest.get('as_of') != day or len(dates)<5 or dates[-1]!=day or dates != sorted(set(dates)):
            raise ValueError('题材五日窗口缺少严格开放日依据')
        current=_ranking(raw.get(day.replace('-','')+'_theme_ranking'),day)
        days=[]
        for target in dates[-5:]:
            try:
                source=raw.get(target.replace('-','')+'_theme_ranking')
                records=_ranking(source,target)
                days.append(dict(date=target,status='complete',counts={r['code']:r['count'] for r in records},
                                 entries={r['code']:r for r in records},row_count=len(records),receipt_id=source.get('id')))
            except (KeyError,ValueError,TypeError,AttributeError) as exc:
                days.append(dict(date=target,status='source_failed',counts=None,entries={},row_count=None,reason=str(exc)))
            days[-1]['stock_details'] = _stock_details(raw, target, current)
        # Even a valid Top-N ranking cannot establish zeros for absent themes or a full-universe ranking.
        return dict(status='partial',date=day,scope='archived-ranking-only',dates=dates[-5:],days=days,
                    sectors=[dict(code=r['code'],name=r['name'],rank=r['rank']) for r in current],
                    selected=[r['code'] for r in current[:10]],constituents_available=False,
                    source_url='https://tushare.pro/document/2?doc_id=357')
    except (KeyError,ValueError,TypeError,AttributeError) as exc:
        return dict(status='source_failed',reason=str(exc))
