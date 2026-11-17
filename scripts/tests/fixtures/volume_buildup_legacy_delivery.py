"""Explicitly authorized DingTalk delivery of verified v2 observation and peers."""
import argparse, datetime as dt, fcntl, hashlib, json, sys
from pathlib import Path
from zoneinfo import ZoneInfo
from enrich import ROOT, VERSION, sha, read, dump, verified_base, f, rank, precise

def load_verified(run):
    out=run/'enhanced-v2';r=read(out/'report.json');v=read(out/'validation.json')
    verified_base(run)
    assert r['date']==run.name==dt.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    assert r['rule_version']==VERSION and r['status'] in ('complete','partial')
    assert v['status']=='passed' and v['report_sha256']==sha(out/'report.json') and v['markdown_sha256']==sha(out/'report.md')
    assert r['script_sha256']==sha(Path(__file__).with_name('enrich.py'))
    for path,h in r['source_sha256'].items():assert sha(path)==h,path
    return r

from mobile_render import render

def chunks_of(text,limit=12500):
    chunks=[];part=''
    for block in text.splitlines(keepends=True):
        assert len(block.encode())<=limit,'single line too long; preserve content and revise renderer'
        if part and len((part+block).encode())>limit:chunks.append(part);part=''
        part+=block
    if part:chunks.append(part)
    assert ''.join(chunks)==text
    return chunks

def prepare_persistence(run,r,input_by):
    from persistence import prepare
    return prepare(run,r,input_by)

def main(run,input_by,send,supplement_only=False):
    r=load_verified(run);r['source_paths_comparison_md']=str(run/'comparison.md')
    persistence,full_report=prepare_persistence(run,r,input_by)
    r['_persistence']=persistence
    if supplement_only:
        from persistence import mobile_section
        content=mobile_section(persistence,True,compact=True)
    else:content=render(r)
    chunks=chunks_of(content)
    out=run/'enhanced-v2'/('dingtalk-persistence' if supplement_only else 'dingtalk');out.mkdir(exist_ok=True);digest=hashlib.sha256(content.encode()).hexdigest();body=out/(digest+'.md')
    if body.exists():assert body.read_text()==content
    else:body.write_text(content)
    if not send:print(json.dumps(dict(status='prepared',parts=len(chunks),body=str(body),sha256=digest,full_report=full_report),ensure_ascii=False));return
    with (out/'.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for old in out.glob('*.json'):
            previous=read(old)
            if previous.get('status') in ('sending','delivery_unknown'):raise RuntimeError('prior delivery unknown; no blind resend')
        path=out/(digest+'.json');ledger=read(path) if path.exists() else dict(date=r['date'],input_by=input_by,status='pending',content_sha256=digest,report_sha256=sha(run/'enhanced-v2/report.json'),full_report=full_report,full_report_sha256=sha(full_report),delivery_kind='persistence_supplement' if supplement_only else 'daily',total_parts=len(chunks),part_sha256=[hashlib.sha256(x.encode()).hexdigest() for x in chunks],sent_parts=[],attempts=[])
        assert ledger['part_sha256']==[hashlib.sha256(x.encode()).hexdigest() for x in chunks]
        if ledger['status']=='sent':print('already_sent');return
        from dotenv import load_dotenv
        from pushers.dingtalk_pusher import DingTalkPusher
        load_dotenv(ROOT/'scripts/.env');sender=DingTalkPusher(config={})
        if not sender.initialize():ledger['status']='failed_configuration';dump(path,ledger);raise SystemExit(2)
        for i,chunk in enumerate(chunks):
            if i in ledger['sent_parts']:continue
            ledger['status']='sending';attempt=dict(part=i,result='unknown',started_at=dt.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat());ledger['attempts'].append(attempt);dump(path,ledger)
            try: ok=sender.send_markdown(f"{'量能持续性补充' if supplement_only else '量能2倍观察'} · {r['date']}（{i+1}/{len(chunks)}）",(f"## 量能观察 · {r['date']}（{i+1}/{len(chunks)}）\n\n" if len(chunks)>1 else "")+chunk)
            except Exception as e:ok=False;attempt['error_type']=type(e).__name__
            if not ok:ledger['status']='delivery_unknown';dump(path,ledger);print('delivery_unknown');raise SystemExit(2)
            ledger['sent_parts'].append(i);attempt['result']='confirmed_success';ledger['status']='sent' if len(ledger['sent_parts'])==len(chunks) else 'partial';dump(path,ledger)
        print(json.dumps(dict(status=ledger['status'],parts=ledger['total_parts'],sent_parts=ledger['sent_parts'],receipt=str(path)),ensure_ascii=False))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--input-by',required=True);p.add_argument('--send',action='store_true');p.add_argument('--supplement-only',action='store_true');a=p.parse_args()
    assert a.input_by=='codex_automation';assert a.run_dir.resolve().parent==ROOT/'data/reports/volume-buildup/runs';main(a.run_dir.resolve(),a.input_by,a.send,a.supplement_only)
