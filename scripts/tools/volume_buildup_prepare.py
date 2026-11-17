"""Install the versioned recent-shape adapter into the existing research job.

Explicit file hashes guard the local script edits. This does not install a
scheduler, collect quotes, send messages, or change archived market reports.
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.volume_buildup_recent import ROOT, VERSION, DEFINITION, atomic, encode, sha


SHIM = '''"""Versioned volume-buildup adapter; installed by volume_buildup_prepare."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
from tools import volume_buildup_recent as implementation

if __name__ == "__main__":
    implementation.main()
else:
    # Preserve the research module API and patch points for existing callers.
    sys.modules[__name__] = implementation
'''


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('未找到唯一交付接入锚点，拒绝修改未知版本')
    return text.replace(before, after, 1)


def wire_delivery(text):
    if 'def prepare_recent_shapes(' not in text:
        text = once(text, '\ndef main(run,input_by,send,supplement_only=False):', '''
def prepare_recent_shapes(run,r,input_by,base_report):
    from recent_shapes import prepare
    return prepare(run,r,input_by,base_report)

def main(run,input_by,send,supplement_only=False):''')
        text = once(text, "    r=load_verified(run);r['source_paths_comparison_md']=str(run/'comparison.md')",
                    '    r=load_verified(run)')
        text = once(text, "    r['_persistence']=persistence", '''    recent=None
    if not supplement_only:
        recent,full_report=prepare_recent_shapes(run,r,input_by,full_report)
    r['source_paths_comparison_md']=str(run/'comparison.md')
    r['_persistence']=persistence''')
        text = once(text, '    else:content=render(r)', '''    else:
        from recent_shapes import markdown
        content=markdown(recent,compact=True,persistence=persistence)''')
    # Upgrade the earlier same-day integration without sending its duplicated v2 list.
    old_mobile="""        old=render(r).replace('## 量能观察｜','## 原严格口径当日对照｜',1)
        content=markdown(recent,compact=True)+'\\n'+old"""
    if old_mobile in text:
        text=once(text,old_mobile,'        content=markdown(recent,compact=True,persistence=persistence)')
    required = (
        '    return prepare(run,r,input_by,base_report)',
        '    if not supplement_only:\n        recent,full_report=prepare_recent_shapes(run,r,input_by,full_report)',
        '        content=markdown(recent,compact=True,persistence=persistence)',
    )
    if any(text.count(part) != 1 for part in required):
        raise ValueError('近期模块接入不完整，拒绝覆盖')
    compile(text, 'deliver_enriched.py', 'exec')
    return text


def install(research, input_by, expected_delivery, expected_wrapper=None, apply=False, expected_contract=None):
    research=Path(research).resolve()
    if not input_by.strip() or research.name!='research' or research.parent.name!='volume-buildup':
        raise ValueError('须指定请求者和volume-buildup/research目录')
    target_module=research.parents[3]/'scripts/tools/volume_buildup_recent.py'
    module_digest=sha(Path(__file__).with_name('volume_buildup_recent.py'))
    if not target_module.is_file() or target_module.is_symlink() or sha(target_module)!=module_digest:
        raise ValueError('目标仓库尚未部署匹配版本的volume_buildup_recent.py，未安装')
    driver=research/'deliver_enriched.py'
    wrapper=research/'recent_shapes.py'
    if driver.is_symlink() or wrapper.is_symlink():
        raise ValueError('运行文件不得是符号链接')
    old=driver.read_bytes()
    if hashlib.sha256(old).hexdigest()!=expected_delivery:
        raise ValueError('交付脚本已变化，须重新核对SHA')
    old_wrapper=wrapper.read_bytes() if wrapper.exists() else None
    if old_wrapper is not None and old_wrapper!=SHIM.encode():
        if hashlib.sha256(old_wrapper).hexdigest()!=expected_wrapper:
            raise ValueError('已有近期脚本未获匹配SHA，拒绝覆盖')
    new=wire_delivery(old.decode()).encode()
    compile(SHIM,str(wrapper),'exec')
    contract=research.parent/'recurring-contract.md'
    old_contract=new_contract=None
    if expected_contract is not None:
        if contract.is_symlink():raise ValueError('合同不得是符号链接')
        old_contract=contract.read_bytes()
        if hashlib.sha256(old_contract).hexdigest()!=expected_contract:
            raise ValueError('合同已变化，须重新核对SHA')
        heading='## 2026-11-17 沪深范围、收盘跌停排除与手机精简'
        addition=f'''\n\n{heading}

本节优先于此前候选范围及手机展示约定，记录者：{input_by}；版本 `{VERSION}`。

- {DEFINITION}
- 手机只给一行汇总和每股一行，分今天仍满足/仅历史出现；完整保留通过最终过滤的名单、名称代码、板块、最近形态日及倍数。已核验当日A持续性可附5日量倍数，保留必要缺口和旧B失效标记。
- 手机不再拼接原v2名单或逐项规则；旧口径对照、全部出现日期、排除明细、完整持续性和板块证据只在.full.md及JSON附件中保留。
- research/recent_shapes.py兼容入口调用scripts/tools/volume_buildup_recent.py，既有deliver_enriched.py在发送前强制核验。旧归档只作证据，不覆盖原报告/发送回执。不得因本次规则或展示变化重发今天及历史消息，不新增调度、推送授权或业务写入。
'''
        new_contract=old_contract if heading in old_contract.decode() else old_contract+addition.encode()
    changed=new!=old or old_wrapper!=SHIM.encode() or old_contract!=new_contract
    receipt=dict(input_by=input_by,status='prepared' if changed else 'already_installed',
                 delivery_before_sha256=hashlib.sha256(old).hexdigest(),
                 wrapper_before_sha256=hashlib.sha256(old_wrapper).hexdigest() if old_wrapper else None,
                 delivery_after_sha256=hashlib.sha256(new).hexdigest(),
                 wrapper_after_sha256=hashlib.sha256(SHIM.encode()).hexdigest(),
                 module_sha256=module_digest,target_module=str(target_module),
                 contract_before_sha256=hashlib.sha256(old_contract).hexdigest() if old_contract is not None else None,
                 contract_after_sha256=hashlib.sha256(new_contract).hexdigest() if new_contract is not None else None,
                 research_dir=str(research),business_writes=False,sent=False)
    if apply and changed:
        digest=hashlib.sha256(encode(receipt).encode()).hexdigest()
        backup=research.parent/'runtime-installs'/digest
        backup.mkdir(parents=True,exist_ok=True)
        before={'delivery.before.py':old}
        if old_wrapper is not None:
            before['recent.before.py']=old_wrapper
        if old_contract is not None:
            before['contract.before.md']=old_contract
        for name,content in before.items():
            path=backup/name
            if path.exists():
                assert path.read_bytes()==content, 'backup_conflict'
            else:
                atomic(path,content.decode())
        if driver.read_bytes()!=old or (wrapper.read_bytes() if wrapper.exists() else None)!=old_wrapper:
            raise ValueError('运行文件并发变化，未安装')
        if sha(target_module)!=module_digest:
            raise ValueError('目标正式模块并发变化，未安装')
        if old_contract is not None:
            if contract.read_bytes()!=old_contract:raise ValueError('合同并发变化，未安装')
            history=contract.parent/'contract-history';history.mkdir(exist_ok=True)
            snapshot=history/(hashlib.sha256(old_contract).hexdigest()+'.md')
            if snapshot.exists():
                assert snapshot.read_bytes()==old_contract, 'contract_history_conflict'
            else:
                atomic(snapshot,old_contract.decode())
        # Install the standalone adapter before its consumer. Old delivery can
        # still operate if interrupted between these two atomic file writes.
        atomic(wrapper,SHIM)
        if new!=old:
            atomic(driver,new.decode())
        if old_contract!=new_contract:
            atomic(contract,new_contract.decode())
        receipt['status']='installed'
        atomic(backup/'receipt.json',encode(receipt))
        receipt['receipt']=str(backup/'receipt.json')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-dir',type=Path,default=ROOT/'data/reports/volume-buildup/research')
    parser.add_argument('--input-by',required=True)
    parser.add_argument('--expected-delivery-sha256',required=True)
    parser.add_argument('--expected-wrapper-sha256')
    parser.add_argument('--expected-contract-sha256',help='核对并追加最新规则，保留旧合同SHA快照；省略则不改合同')
    parser.add_argument('--apply',action='store_true',help='备份并安装；默认只预览')
    args=parser.parse_args()
    print(encode(install(args.research_dir,args.input_by,args.expected_delivery_sha256,args.expected_wrapper_sha256,args.apply,args.expected_contract_sha256)))


if __name__=='__main__':
    # Direct script invocation also works outside scripts/.
    main()
