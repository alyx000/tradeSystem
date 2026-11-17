import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import subprocess
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import volume_buildup_prepare as P


LEGACY=Path(__file__).parent/'fixtures/volume_buildup_legacy_delivery.py'


def research_at(tmp_path):
    root=tmp_path/'data/reports/volume-buildup/research'
    root.mkdir(parents=True)
    driver=root/'deliver_enriched.py'
    driver.write_text(LEGACY.read_text())
    module=tmp_path/'scripts/tools/volume_buildup_recent.py';module.parent.mkdir(parents=True)
    module.write_bytes(Path(P.__file__).with_name('volume_buildup_recent.py').read_bytes())
    return root,driver


def test_preview_install_idempotence_and_backups(tmp_path):
    root,driver=research_at(tmp_path);old=driver.read_bytes();h=P.sha(driver)
    receipt=P.install(root,'USER',h)
    assert receipt['status']=='prepared' and driver.read_bytes()==old
    assert not (root/'recent_shapes.py').exists()
    receipt=P.install(root,'USER',h,apply=True)
    assert receipt['status']=='installed'
    assert Path(receipt['receipt']).with_name('delivery.before.py').read_bytes()==old
    assert (root/'recent_shapes.py').read_text()==P.SHIM
    assert P.install(root,'USER',P.sha(driver),apply=True)['status']=='already_installed'
    assert (root/'deliver_enriched.py').read_text().count('def prepare_recent_shapes(')==1


def test_existing_wrapper_needs_explicit_sha(tmp_path):
    root,driver=research_at(tmp_path);wrapper=root/'recent_shapes.py'
    wrapper.write_text('custom = True\n')
    with pytest.raises(ValueError,match='已有近期脚本'):P.install(root,'USER',P.sha(driver),apply=True)
    before=wrapper.read_bytes()
    receipt=P.install(root,'USER',P.sha(driver),P.sha(wrapper),apply=True)
    assert Path(receipt['receipt']).with_name('recent.before.py').read_bytes()==before


def test_shim_preserves_the_existing_module_api(tmp_path,monkeypatch):
    from tools import volume_buildup_recent
    root,driver=research_at(tmp_path)
    P.install(root,'USER',P.sha(driver),apply=True)
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.delitem(sys.modules,'recent_shapes',raising=False)
    module=__import__('recent_shapes')
    assert module is volume_buildup_recent
    assert module.shape_a is volume_buildup_recent.shape_a
    monkeypatch.delitem(sys.modules,'recent_shapes')


def test_installed_shim_imports_target_module_in_a_fresh_process(tmp_path):
    root,driver=research_at(tmp_path)
    P.install(root,'USER',P.sha(driver),apply=True)
    result=subprocess.run([sys.executable,'-c',
        'import sys;sys.path.insert(0,sys.argv[1]);import recent_shapes;print(recent_shapes.__file__)',str(root)],
        cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==str(tmp_path/'scripts/tools/volume_buildup_recent.py')


@pytest.mark.parametrize('missing',[True,False])
def test_target_module_must_exist_and_match_before_install(tmp_path,missing):
    root,driver=research_at(tmp_path);before=driver.read_bytes()
    module=tmp_path/'scripts/tools/volume_buildup_recent.py'
    if missing:module.unlink()
    else:module.write_text('stale = True\n')
    with pytest.raises(ValueError,match='目标仓库尚未部署匹配版本'):
        P.install(root,'USER',P.sha(driver),apply=True)
    assert driver.read_bytes()==before and not (root/'recent_shapes.py').exists()


def test_target_module_drift_during_backup_prevents_install(tmp_path,monkeypatch):
    root,driver=research_at(tmp_path);original=P.atomic
    def drift(path,text):
        original(path,text)
        if path.name=='delivery.before.py':
            (tmp_path/'scripts/tools/volume_buildup_recent.py').write_text('changed = True\n')
    monkeypatch.setattr(P,'atomic',drift)
    with pytest.raises(ValueError,match='目标正式模块并发变化'):
        P.install(root,'USER',P.sha(driver),apply=True)
    assert not (root/'recent_shapes.py').exists()


def test_drift_and_unknown_driver_cannot_partially_write(tmp_path):
    root,driver=research_at(tmp_path)
    with pytest.raises(ValueError,match='交付脚本已变化'):P.install(root,'USER','bad',apply=True)
    driver.write_text('print("unknown")\n')
    with pytest.raises(ValueError,match='唯一交付接入锚点'):P.install(root,'USER',P.sha(driver),apply=True)
    assert not (root/'recent_shapes.py').exists()


def test_contract_append_preserves_previous_hash_and_is_idempotent(tmp_path):
    root,driver=research_at(tmp_path)
    contract=root.parent/'recurring-contract.md';contract.write_text('previous rules\n')
    old=contract.read_bytes();h=P.sha(contract)
    with pytest.raises(ValueError,match='合同已变化'):
        P.install(root,'USER',P.sha(driver),apply=True,expected_contract='changed')
    assert not (root/'recent_shapes.py').exists()
    receipt=P.install(root,'USER',P.sha(driver),apply=True,expected_contract=h)
    assert (root.parent/'contract-history'/(h+'.md')).read_bytes()==old
    assert Path(receipt['receipt']).with_name('contract.before.md').read_bytes()==old
    assert contract.read_bytes().startswith(old) and '收盘跌停' in contract.read_text()
    again=P.install(root,'USER',P.sha(driver),apply=True,expected_contract=P.sha(contract))
    assert again['status']=='already_installed'


def test_symbolic_link_refused(tmp_path):
    root,driver=research_at(tmp_path)
    elsewhere=tmp_path/'owned.py';elsewhere.write_text('private')
    (root/'recent_shapes.py').symlink_to(elsewhere)
    with pytest.raises(ValueError,match='符号链接'):P.install(root,'USER',P.sha(driver),apply=True)
    assert elsewhere.read_text()=='private'


def load_driver(tmp_path,monkeypatch):
    root,driver=research_at(tmp_path)
    P.install(root,'USER',P.sha(driver),apply=True)
    stub=types.ModuleType('enrich')
    for name in ('ROOT','VERSION','sha','read','dump','verified_base','f','rank','precise'):
        setattr(stub,name,None)
    stub.ROOT=tmp_path;stub.sha=P.sha
    stub.read=lambda p:json.loads(Path(p).read_text())
    stub.dump=lambda p,r:Path(p).write_text(json.dumps(r))
    monkeypatch.setitem(sys.modules,'enrich',stub)
    mobile=types.ModuleType('mobile_render');mobile.render=lambda r:'## 量能观察｜11-16\n旧名单\n'
    monkeypatch.setitem(sys.modules,'mobile_render',mobile)
    recent=types.ModuleType('recent_shapes')
    recent.prepare=lambda run,r,actor,base:({'date':r['date']},base)
    recent.markdown=lambda r,compact,persistence:'近期新名单\n'
    monkeypatch.setitem(sys.modules,'recent_shapes',recent)
    spec=importlib.util.spec_from_file_location('test_driver',driver)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    run=root.parent/'runs/2026-11-16';out=run/'enhanced-v2';out.mkdir(parents=True)
    (out/'report.json').write_text('{}')
    module.load_verified=lambda run:{'date':run.name}
    module.prepare_persistence=lambda run,r,actor:({},str(out/'report.json'))
    sender=Mock();sender.initialize.return_value=True;sender.send_markdown.return_value=True
    dotenv=types.ModuleType('dotenv');dotenv.load_dotenv=lambda *a,**kw:None
    monkeypatch.setitem(sys.modules,'dotenv',dotenv)
    push=types.ModuleType('pushers.dingtalk_pusher');push.DingTalkPusher=lambda **kw:sender
    monkeypatch.setitem(sys.modules,'pushers.dingtalk_pusher',push)
    return module,run,sender


def test_new_report_has_no_duplicate_old_list_and_success_deduplicates(tmp_path,monkeypatch):
    driver,run,sender=load_driver(tmp_path,monkeypatch)
    with contextlib.redirect_stdout(io.StringIO()):
        driver.main(run,'USER',True)
        driver.main(run,'USER',True)
    assert sender.send_markdown.call_count==1
    body=sender.send_markdown.call_args.args[1]
    assert body=='近期新名单\n' and '旧名单' not in body


def test_earlier_verbose_adapter_is_upgraded():
    text=P.wire_delivery(LEGACY.read_text())
    old="""        old=render(r).replace('## 量能观察｜','## 原严格口径当日对照｜',1)
        content=markdown(recent,compact=True)+'\\n'+old"""
    text=text.replace('        content=markdown(recent,compact=True,persistence=persistence)',old)
    assert P.wire_delivery(text).count('content=markdown(recent,compact=True,persistence=persistence)')==1
    assert 'old=render(r)' not in P.wire_delivery(text)


def test_failed_recent_generation_prevents_any_send(tmp_path,monkeypatch):
    driver,run,sender=load_driver(tmp_path,monkeypatch)
    driver.prepare_recent_shapes=Mock(side_effect=ValueError('source failed'))
    with pytest.raises(ValueError):driver.main(run,'USER',True)
    sender.initialize.assert_not_called()


def test_unknown_delivery_blocks_resend(tmp_path,monkeypatch):
    driver,run,sender=load_driver(tmp_path,monkeypatch)
    sender.send_markdown.return_value=False
    with pytest.raises(SystemExit),contextlib.redirect_stdout(io.StringIO()):driver.main(run,'USER',True)
    with pytest.raises(RuntimeError,match='unknown'):driver.main(run,'USER',True)
    assert sender.send_markdown.call_count==1


def test_original_load_verified_gate_unchanged():
    import ast
    old=LEGACY.read_text();new=P.wire_delivery(old)
    fn=lambda text:next(x for x in ast.parse(text).body if isinstance(x,ast.FunctionDef) and x.name=='load_verified')
    assert ast.dump(fn(old),include_attributes=False)==ast.dump(fn(new),include_attributes=False)


def test_unrelated_runtime_code_is_unchanged():
    old=LEGACY.read_text();new=P.wire_delivery(old)
    # Sending, accounting, and supplementary-only logic remain byte-for-byte.
    assert new[new.index('    with (out/\'.lock\')'):]==old[old.index('    with (out/\'.lock\')'):]
