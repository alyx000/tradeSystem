import json
from pathlib import Path
import subprocess
import sys

import pytest

from services.handwritten_review import (
    SECTIONS, amend_entry, build_reader, digest, import_entry, load_entry, render_archive,
)


DAY = '2026-11-05'


def inputs(tmp_path, raw='1105\n样本甲+2.25；另记-0.75\n', body='未核验；不是0。'):
    source = tmp_path / 'input.txt'
    source.write_bytes(raw.encode('utf-8'))
    supplement = tmp_path / 'input.md'
    supplement.write_text('\n'.join(f'## {title}\n{body}\n' for title in SECTIONS), encoding='utf-8')
    return source, supplement


def test_round_trip_preserves_bytes_and_audit(tmp_path):
    raw = b'1105\r\n\xe7\xa4\xba\xe4\xbe\x8b42\r\n'
    source, supplement = inputs(tmp_path)
    source.write_bytes(raw)
    archive = tmp_path / 'archive'
    result = import_entry(archive, DAY, source, supplement, ' USER ')
    assert result['status'] == 'saved'
    assert (archive / DAY / 'source.txt').read_bytes() == raw
    saved = load_entry(archive / DAY)
    assert saved['meta']['input_by'] == 'USER'
    assert saved['meta']['verification'] == 'unverified'
    assert saved['meta']['supplement_kind'] == 'retrospective'
    assert saved['meta']['source_sha256'] == digest(raw)
    assert saved['meta']['recorded_at'].endswith('+08:00')


def test_idempotency_and_conflicting_source_do_not_overwrite(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    before = {p.name: p.read_bytes() for p in (archive / DAY).iterdir()}
    assert import_entry(archive, DAY, source, supplement, 'USER')['status'] == 'already_saved'
    source.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='拒绝覆盖'):
        import_entry(archive, DAY, source, supplement, 'USER')
    assert before == {p.name: p.read_bytes() for p in (archive / DAY).iterdir()}


@pytest.mark.parametrize('day', ['../../escape', '2026-2-1', '2026-02-30'])
def test_bad_dates_are_rejected_before_writes(tmp_path, day):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    with pytest.raises(ValueError):
        import_entry(archive, day, source, supplement, 'USER')
    assert not archive.exists()


def test_requester_required_on_every_write(tmp_path):
    source, supplement = inputs(tmp_path)
    with pytest.raises(ValueError, match='input-by'):
        import_entry(tmp_path / 'archive', DAY, source, supplement, ' ')
    with pytest.raises(ValueError, match='input-by'):
        build_reader(tmp_path / 'archive', '')


@pytest.mark.parametrize('kind', ['missing_section', 'empty_section', 'bad_table', 'empty_table', 'invalid_utf8'])
def test_invalid_input_never_publishes_partial_entry(tmp_path, kind):
    source, supplement = inputs(tmp_path)
    if kind == 'missing_section':
        supplement.write_text('## 原文待核事项\n缺失', encoding='utf-8')
    elif kind == 'empty_section':
        supplement.write_text('\n'.join('## ' + title for title in SECTIONS), encoding='utf-8')
    elif kind == 'bad_table':
        source, supplement = inputs(tmp_path, body='| A | B |\n| C |')
    elif kind == 'empty_table':
        source, supplement = inputs(tmp_path, body='| --- | --- |')
    else:
        source.write_bytes(b'\xff')
    archive = tmp_path / 'archive'
    with pytest.raises(ValueError):
        import_entry(archive, DAY, source, supplement, 'USER')
    assert not (archive / DAY).exists()


def test_corrupted_entry_is_explicit_failure_and_other_dates_survive(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    for day in ['2026-11-04', DAY]:
        import_entry(archive, day, source, supplement, 'USER')
    (archive / DAY / 'source.txt').write_text('篡改', encoding='utf-8')
    html, errors = render_archive(archive)
    assert len(errors) == 1 and '校验失败' in errors[0]
    assert 'id="day-2026-11-04"' in html
    assert 'id="day-2026-11-05"' not in html
    assert '篡改' not in html
    assert build_reader(archive, 'USER')['status'] == 'source_failed'


@pytest.mark.parametrize('metadata', [[], None, {'schema': 'handwritten-review-v1'}])
def test_corrupt_receipt_isolated_in_reader(tmp_path, metadata):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    (archive / DAY / 'receipt.json').write_text(json.dumps(metadata), encoding='utf-8')
    receipt = build_reader(archive, 'USER')
    assert receipt['status'] == 'source_failed'
    assert len(receipt['errors']) == 1


def test_html_escapes_source_and_table_text_and_keeps_unknowns(tmp_path):
    source, supplement = inputs(tmp_path, raw='<script>alert(1)</script>',
                                body='| 对象 | 结果 |\n| --- | --- |\n| <img src=x onerror=x> | 待核； +2.25／-0.75冲突 |')
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    receipt = build_reader(archive, 'USER')
    html = (archive / 'index.html').read_text(encoding='utf-8')
    assert '<script>' not in html and '<img' not in html
    assert '&lt;script&gt;' in html and '&lt;img' in html
    assert '事后整理' in html and '未独立核验' in html and '冲突' in html
    assert '<table>' in html and 'overflow-x:auto' in html
    assert receipt['html_sha256'] == digest(html.encode('utf-8'))
    assert json.loads((archive / 'reader-receipt.json').read_text())['input_by'] == 'USER'


def test_cli_import_build_and_no_requester(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    tool = Path(__file__).resolve().parents[1] / 'tools' / 'handwritten_review.py'
    cmd = [sys.executable, str(tool), '--archive-dir', str(archive)]
    args = ['import', '--date', DAY, '--source-file', str(source), '--supplement-file', str(supplement)]
    assert subprocess.run(cmd + args, capture_output=True).returncode != 0
    done = subprocess.run(cmd + args + ['--input-by', 'USER'], capture_output=True, text=True)
    assert done.returncode == 0 and json.loads(done.stdout)['status'] == 'saved'
    done = subprocess.run(cmd + ['build', '--input-by', 'USER'], capture_output=True, text=True)
    assert done.returncode == 0 and (archive / 'index.html').is_file()


@pytest.mark.parametrize('kind', ['file', 'dangling_symlink'])
def test_date_named_corruption_is_not_silently_skipped(tmp_path, kind):
    archive = tmp_path / 'archive'
    archive.mkdir()
    if kind == 'file':
        (archive / DAY).write_text('损坏', encoding='utf-8')
    else:
        (archive / DAY).symlink_to(archive / 'missing')
    assert build_reader(archive, 'USER')['status'] == 'source_failed'
    assert DAY in (archive / 'index.html').read_text(encoding='utf-8')


def test_amendment_keeps_originals_and_requires_current_version(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    folder = archive / DAY
    original = {p.name: p.read_bytes() for p in folder.iterdir()}
    old_sha = load_entry(folder)['meta']['supplement_sha256']
    supplement.write_text(supplement.read_text().replace('未核验；不是0。', '补回遗漏对象，仍待核验。'), encoding='utf-8')
    result = amend_entry(archive, DAY, supplement, old_sha, '补遗漏', 'USER')
    assert result['revision'] == 1
    for name, content in original.items():
        assert (folder / name).read_bytes() == content
    current = load_entry(folder)
    assert current['meta']['previous_supplement_sha256'] == old_sha
    assert current['meta']['revision'] == 1
    assert '补回遗漏' in current['sections'][SECTIONS[0]]
    with pytest.raises(ValueError, match='已变化'):
        amend_entry(archive, DAY, supplement, old_sha, '过期请求', 'USER')
    assert amend_entry(archive, DAY, supplement, current['meta']['supplement_sha256'], '相同', 'USER')['status'] == 'already_saved'
    supplement.write_bytes(original['supplement.md'])
    assert import_entry(archive, DAY, source, supplement, 'USER')['status'] == 'already_saved'
    assert load_entry(folder)['meta']['revision'] == 1  # 重导原始批次不会回滚。


def test_broken_revision_chain_fails_closed(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    old_sha = load_entry(archive / DAY)['meta']['supplement_sha256']
    supplement.write_text(supplement.read_text().replace('未核验', '仍未核验'), encoding='utf-8')
    amend_entry(archive, DAY, supplement, old_sha, '补充', 'USER')
    receipt_path = archive / DAY / 'amendments/000001/receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['previous_supplement_sha256'] = 'wrong'
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    assert build_reader(archive, 'USER')['status'] == 'source_failed'


def test_cli_amend_preserves_source(tmp_path):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    import_entry(archive, DAY, source, supplement, 'USER')
    old_sha = load_entry(archive / DAY)['meta']['supplement_sha256']
    supplement.write_text(supplement.read_text().replace('未核验', '待核验'), encoding='utf-8')
    tool = Path(__file__).resolve().parents[1] / 'tools/handwritten_review.py'
    cmd = [sys.executable, str(tool), '--archive-dir', str(archive), 'amend', '--date', DAY,
           '--supplement-file', str(supplement), '--expected-sha256', old_sha, '--reason', '修订', '--input-by', 'USER']
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['revision'] == 1
    assert (archive / DAY / 'source.txt').read_bytes() == source.read_bytes()


@pytest.mark.parametrize('field,value', [('revision', 1), ('previous_supplement_sha256', 'x'), ('amendment_reason', 'x')])
def test_original_receipt_cannot_impersonate_amendment(tmp_path, field, value):
    source, supplement = inputs(tmp_path)
    archive = tmp_path / 'archive'
    for day in ['2026-11-04', DAY]:
        import_entry(archive, day, source, supplement, 'USER')
    path = archive / DAY / 'receipt.json'
    meta = json.loads(path.read_text())
    meta[field] = value
    path.write_text(json.dumps(meta), encoding='utf-8')
    result = build_reader(archive, 'USER')
    assert result['status'] == 'source_failed'
    html = (archive / 'index.html').read_text(encoding='utf-8')
    assert 'id="day-2026-11-04"' in html
    assert 'id="day-2026-11-05"' not in html
