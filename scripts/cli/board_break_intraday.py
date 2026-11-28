"""board-break tail/open-review：独立批次、严格时点与显式请求者。"""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sys

from db.connection import get_readonly_connection
from services.board_break_intraday import data, service, store

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT/'data/reports/board-break-intraday'


def register(sub):
    probe = sub.add_parser('minute-check',help='只测试分钟源并归档回执；不生成候选或推送')
    probe.add_argument('--date',required=True)
    probe.add_argument('--code',required=True)
    probe.add_argument('--source',choices=['historical','realtime'],required=True)
    probe.add_argument('--cutoff',choices=['09:40','14:50'],default='09:40')
    probe.add_argument('--input-by',required=True)
    probe.add_argument('--json',action='store_true')
    for stage, help_text in [('tail','14:50断板反包观察'),('open-review','次日09:40同批复核')]:
        p = sub.add_parser(stage,help=help_text)
        p.add_argument('--date',default=None)
        p.add_argument('--input-by',required=True)
        p.add_argument('--rehearsal',action='store_true',help='历史分钟证据演练，禁止推送')
        p.add_argument('--no-llm',action='store_true')
        pushes=p.add_mutually_exclusive_group()
        pushes.add_argument('--push',action='store_true',help='只发送完整实时观察结果')
        pushes.add_argument('--no-push',action='store_true',help='只落盘（默认）')
        p.add_argument('--json',action='store_true')
        if stage=='open-review':
            p.add_argument('--batch',default=None,help='尾盘批次manifest.json；默认严格上一开放日最新批次')


def handle(args):
    if not args.input_by.strip():
        raise SystemExit('--input-by不能为空')
    if args.board_break_command=='minute-check':
        return minute_check(args)
    if args.rehearsal and args.push:
        raise SystemExit('历史演练禁止推送')
    if args.no_llm and args.push:
        raise SystemExit('关闭LLM时不能推送正式观察')
    day=args.date or datetime.now(data.TZ).date().isoformat()
    try:
        # 先验证格式，禁止日期字符串成为任意文件路径。
        parsed=datetime.strptime(day,'%Y-%m-%d').date()
        if parsed.isoformat()!=day:
            raise ValueError()
    except ValueError:
        raise SystemExit('日期必须为YYYY-MM-DD') from None
    mode='rehearsal' if args.rehearsal else 'live'
    try:
        with store.lock(DEFAULT_OUTPUT,mode+'-'+day+'-'+args.board_break_command):
            with closing(get_readonly_connection()) as conn:
                result=service.run(conn,stage=args.board_break_command,day=day,mode=mode,
                                   input_by=args.input_by,root=DEFAULT_OUTPUT,
                                   handwritten_root=ROOT/'data/handwritten-review',
                                   batch=getattr(args,'batch',None),no_llm=args.no_llm)
            manifest=store.save(DEFAULT_OUTPUT,result,service.render(result))
            response=dict(status=result['status'],manifest=str(manifest),candidate_count=result['candidate_count'],
                          selected_codes=result['selected_codes'],gaps=result['gaps'],input_by=args.input_by)
            standings = result['pk']
            response['pk'] = {key: standings[key] for key in ('status', 'reason', 'rows', 'total')}
            if args.push:
                response['delivery']=(store.deliver(DEFAULT_OUTPUT,manifest,args.input_by)
                                      if result['status']=='complete' else
                                      dict(status='not_sent',reason='报告非完整结果：'+result['status']))
        print(json.dumps(response,ensure_ascii=False) if args.json else service.render(result)+'\n批次：'+str(manifest))
        if result['status'] not in {'complete','skipped'} or response.get('delivery',{}).get('status') in {'failed','already_attempted'}:
            raise SystemExit(1)
    except (data.EvidenceError,OSError) as exc:
        # 不输出网络凭据；本层只接固定证据错误与文件异常类型。
        print(json.dumps(dict(status='failed',input_by=args.input_by,
                              error=str(exc) if isinstance(exc,data.EvidenceError) else type(exc).__name__),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1) from None


def minute_check(args):
    import re
    from datetime import date
    if not re.fullmatch(r'\d{6}\.(SH|SZ)',args.code):
        raise SystemExit('仅接受沪深股票标准代码')
    if date.fromisoformat(args.date).isoformat()!=args.date:
        raise SystemExit('日期必须为YYYY-MM-DD')
    result=dict(schema='board-break-intraday-v1',stage='minute-check',mode='diagnostic',
                date=args.date,input_by=args.input_by,started_at=datetime.now(data.TZ).isoformat(),
                cutoff=args.date+'T'+args.cutoff+':00+08:00',status='source_failed',
                candidates=[],excluded=[],unverified=[],candidate_count=None,candidate_coverage=False,
                selected_codes=[],gaps=[],evaluation={'status':'not_run'},source_kind=args.source)
    try:
        source=data.MarketSource()
        realtime = args.source == 'realtime'
        result['minute']=source.minutes(args.code, args.date, args.cutoff, 'live' if realtime else 'rehearsal',
                                        realtime_diagnostic=realtime)
        result['status']='complete'
    except data.EvidenceError as exc:
        result['gaps'].append(str(exc))
        result['source_attempts'] = getattr(exc, 'attempts', [])
    result['finished_at']=datetime.now(data.TZ).isoformat()
    text='# 断板反包分钟源诊断（非扫描结果）\n\n'+json.dumps(result,ensure_ascii=False,indent=2)
    manifest=store.save(DEFAULT_OUTPUT,result,text)
    print(json.dumps(dict(status=result['status'],manifest=str(manifest),gaps=result['gaps'],
                          expected=(result.get('minute') or {}).get('expected'),input_by=args.input_by),ensure_ascii=False))
    if result['status']!='complete':
        raise SystemExit(1)
