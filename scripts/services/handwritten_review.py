"""用户手写复盘档案：CLI 入档、原文校验和独立静态阅读器。

不把用户记录升级为行情事实，不评分、不访问市场源或业务库。
"""
from datetime import date, datetime
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import shutil
import tempfile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = ROOT / "data" / "handwritten-review"
SECTIONS = ("前日锚点逐条验证", "固定对象跨日反馈", "次日具体条件", "原文待核事项")
NOTICE = "用户手写记录，尚未逐项核验行情；补充内容为事后整理，不代表当时已定义的规则或已验证的市场事实。"


def digest(data):
    return sha256(data).hexdigest()


def valid_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期须为 YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def split_supplement(text):
    """只接受四个固定章节；正文按纯文本/表格渲染，不执行 HTML。"""
    parts = re.split(r"(?m)^## (.+?)\s*$", text)
    if parts[0].strip() or parts[1::2] != list(SECTIONS):
        raise ValueError("补充稿须按顺序完整包含四个固定二级标题")
    sections = dict(zip(parts[1::2], parts[2::2]))
    if any(not body.strip() for body in sections.values()):
        raise ValueError("补充章节不能为空；不适用或未核验须明确说明")
    return sections


def _load_version(folder, day, raw):
    meta = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError("回执必须为对象")
    supplement = (folder / "supplement.md").read_bytes()
    if (meta.get("schema") != "handwritten-review-v1" or
            valid_date(meta["date"]) != day or
            meta.get("source_sha256") != digest(raw) or
            meta.get("supplement_sha256") != digest(supplement)):
        raise ValueError(f"{folder.name} 原文、补充稿或回执校验失败")
    if not isinstance(meta.get("input_by"), str) or not meta["input_by"].strip():
        raise ValueError("缺少录入请求者")
    if (meta.get('source_type') != 'user_handwritten' or meta.get('verification') != 'unverified' or
            meta.get('supplement_kind') != 'retrospective' or
            not isinstance(meta.get('date_basis'), str) or not meta['date_basis']):
        raise ValueError("来源或事后整理标签非法")
    datetime.fromisoformat(meta['recorded_at'])
    sections = split_supplement(supplement.decode("utf-8"))
    for body in sections.values():
        _body_html(body)
    return dict(meta=meta, raw=raw.decode("utf-8"), sections=sections)


def load_entry(folder):
    folder = Path(folder)
    raw = (folder / 'source.txt').read_bytes()
    current = _load_version(folder, folder.name, raw)
    if {'revision', 'previous_supplement_sha256', 'amendment_reason'} & current['meta'].keys():
        raise ValueError('原始版回执不得含修订字段')
    amendments = folder / 'amendments'
    if amendments.exists() or amendments.is_symlink():
        versions = sorted(p for p in amendments.iterdir() if not p.name.startswith('.'))
        for ordinal, version in enumerate(versions, 1):
            if version.name != f'{ordinal:06d}' or not version.is_dir():
                raise ValueError('修订序列缺失或损坏')
            updated = _load_version(version, folder.name, raw)
            meta = updated['meta']
            if (meta.get('revision') != ordinal or
                    meta.get('previous_supplement_sha256') != current['meta']['supplement_sha256'] or
                    not isinstance(meta.get('amendment_reason'), str) or not meta['amendment_reason'].strip()):
                raise ValueError('修订来源链或修订原因无效')
            current = updated
    return current


def load_entry_before(folder, cutoff):
    """核验完整修订链，返回截止前连续有效的最后一版及被排除的版本号。"""
    folder = Path(folder)
    latest = load_entry(folder)
    raw = (folder / "source.txt").read_bytes()
    versions = [folder]
    amendments = folder / "amendments"
    if amendments.exists():
        versions.extend(sorted(p for p in amendments.iterdir() if not p.name.startswith(".")))
    selected, ignored = None, []
    for version in versions:
        candidate = _load_version(version, folder.name, raw)
        recorded = datetime.fromisoformat(candidate["meta"]["recorded_at"])
        if recorded.tzinfo is None or cutoff.tzinfo is None:
            raise ValueError("手写录入时间缺时区")
        if recorded >= cutoff:
            ignored.append(candidate["meta"].get("revision", 0))
        elif not ignored:
            selected = candidate
    if selected is None:
        raise ValueError("原稿在开盘后录入，未纳入事前名单")
    return selected, latest, ignored


def import_entry(archive, day, source_file, supplement_file, input_by):
    valid_date(day)
    if not input_by or not input_by.strip():
        raise ValueError("input-by 不得为空")
    raw = Path(source_file).read_bytes()
    supplement = Path(supplement_file).read_bytes()
    if not raw.decode("utf-8").strip():
        raise ValueError("原文不能为空")
    for body in split_supplement(supplement.decode("utf-8")).values():
        _body_html(body)
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / day
    if target.exists():
        # 重复导入原始批次只核原始版，不回滚或覆盖后续修订。
        load_entry(target)
        saved = _load_version(target, day, (target / 'source.txt').read_bytes())
        if (saved["meta"]["source_sha256"] != digest(raw) or
                saved["meta"]["supplement_sha256"] != digest(supplement)):
            raise ValueError("已有同日档案，拒绝覆盖原文或历史整理；请保留旧档后另行处理修订")
        return dict(status="already_saved", date=day, path=str(target))
    meta = dict(schema="handwritten-review-v1", date=day,
                date_basis="日期由录入者指定；原文无年份时按会话年份归档，原始日期标题保留",
                recorded_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                input_by=input_by.strip(), source_type="user_handwritten",
                verification="unverified", supplement_kind="retrospective",
                source_sha256=digest(raw), supplement_sha256=digest(supplement))
    stage = Path(tempfile.mkdtemp(prefix=".import-", dir=archive))
    try:
        (stage / "source.txt").write_bytes(raw)
        (stage / "supplement.md").write_bytes(supplement)
        (stage / "receipt.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 原子发布完整目录；并发同日导入不得覆盖已发布的非空目录。
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return dict(status="saved", date=day, path=str(target))


def amend_entry(archive, day, supplement_file, expected_sha256, reason, input_by):
    valid_date(day)
    if not input_by or not input_by.strip() or not reason or not reason.strip():
        raise ValueError('修订必须提供非空input-by与reason')
    folder = Path(archive) / day
    current = load_entry(folder)
    if current['meta']['supplement_sha256'] != expected_sha256:
        raise ValueError('整理稿已变化，拒绝基于旧版本修订')
    supplement = Path(supplement_file).read_bytes()
    for body in split_supplement(supplement.decode('utf-8')).values():
        _body_html(body)
    if digest(supplement) == expected_sha256:
        return dict(status='already_saved', date=day, path=str(folder))
    ordinal = current['meta'].get('revision', 0) + 1
    meta = dict(current['meta'])
    meta.update(revision=ordinal, previous_supplement_sha256=expected_sha256,
                amendment_reason=reason.strip(), input_by=input_by.strip(),
                recorded_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                supplement_sha256=digest(supplement))
    amendments = folder / 'amendments'
    amendments.mkdir(exist_ok=True)
    target = amendments / f'{ordinal:06d}'
    stage = Path(tempfile.mkdtemp(prefix='.amend-', dir=amendments))
    try:
        (stage / 'supplement.md').write_bytes(supplement)
        (stage / 'receipt.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # 同一版本并发修订竞争相同非空目标目录；失败的一方不能覆盖。
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return dict(status='saved', date=day, revision=ordinal, path=str(target))


def _body_html(text):
    """受限 Markdown：段落、列表、表格；所有用户文本先转义。"""
    output, table = [], []

    def flush():
        if not table:
            return
        rows = [row for row in table if not all(re.fullmatch(r":?-+:?", x.strip()) for x in row)]
        if not rows:
            raise ValueError("表格缺少表头和内容")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("表格列数不一致")
        output.append('<div class="table-scroll"><table>')
        for i, row in enumerate(rows):
            tag = "th" if i == 0 else "td"
            output.append("<tr>" + "".join(f"<{tag}>{escape(cell)}</{tag}>" for cell in row) + "</tr>")
        output.append("</table></div>")
        table.clear()

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            table.append([cell.strip() for cell in line[1:-1].split("|")])
        else:
            flush()
            if line:
                output.append("<p>" + escape(line) + "</p>")
    flush()
    return "".join(output)


CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#203047;font:16px/1.75 system-ui,-apple-system,sans-serif}
main{max-width:1240px;margin:auto;padding:32px 24px}h1{font-size:30px;line-height:1.3}h2{margin-top:0}h3{border-left:4px solid #297b83;padding-left:12px}
.intro,.notice{color:#526176}.notice{background:#eef5f4;border-radius:10px;padding:14px}nav{display:flex;gap:10px;flex-wrap:wrap;margin:24px 0}a{color:#17626a}nav a{padding:8px 18px;background:white;border:1px solid #d5e2e4;border-radius:20px;text-decoration:none}
article{background:white;padding:28px;margin:24px 0;border-radius:16px;border:1px solid #dfe5ec;scroll-margin-top:20px}.meta{font-size:13px;color:#66778c;overflow-wrap:anywhere}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px;min-width:760px}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #e2e9ee}th{background:#f0f5f7;white-space:nowrap}td{min-width:110px}details{margin:20px 0;background:#f7f9fc;padding:16px;border-radius:8px}summary{cursor:pointer;font-weight:650}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.9 system-ui,sans-serif}.status{color:#9b532a}footer{font-size:13px;color:#64748b}
@media(max-width:600px){main{padding:18px 12px}article{padding:18px 12px}h1{font-size:25px}table{font-size:13px}}
"""


def render_archive(archive):
    archive = Path(archive)
    entries, errors = [], []
    for folder in sorted(archive.iterdir()):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", folder.name):
            continue
        try:
            if not folder.is_dir():
                raise ValueError('日期档案不是有效目录')
            entries.append(load_entry(folder))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{folder.name}：来源校验失败，未展示该档内容（{type(exc).__name__}）")
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>我的手写复盘</title><style>' + CSS + '</style><main>',
             '<h1>我的手写复盘</h1><p class="intro">原文归档 · 昨日锚点 · 固定对象 · 明日条件</p>',
             '<p class="notice">' + escape(NOTICE) + '</p>', '<nav aria-label="按日期阅读">']
    for entry in entries:
        day = entry['meta']['date']
        parts.append(f'<a href="#day-{day}">{day}</a>')
    parts.append('</nav>')
    for error in errors:
        parts.append('<p class="status">' + escape(error) + '</p>')
    if not entries:
        parts.append('<p>暂无通过来源校验的手写档案；不代表没有复盘。</p>')
    for entry in entries:
        meta = entry['meta']
        parts.extend([f'<article id="day-{meta["date"]}"><h2>{meta["date"]}</h2>',
                      '<p class="meta">录入／整理时间：' + escape(meta['recorded_at']) +
                      ' · 请求者：' + escape(meta['input_by']) + ' · 原记录行情未独立核验</p>',
                      '<details><summary>查看完整手写原文（保留原称、数字及原有表述）</summary><pre>' +
                      escape(entry['raw']) + '</pre></details>'])
        if meta.get('revision'):
            parts.append('<p class="meta">整理稿修订 ' + str(meta['revision']) + '：' +
                         escape(meta['amendment_reason']) + '；原文和此前整理版本均保留。</p>')
        for title, body in entry['sections'].items():
            parts.append('<h3>' + title + '</h3>' + _body_html(body))
        parts.append('<details><summary>归档依据</summary><p class="meta">' + escape(meta['date_basis']) +
                     '<br>原文 SHA-256：' + meta['source_sha256'] + '<br>整理稿 SHA-256：' + meta['supplement_sha256'] + '</p></details></article>')
    parts.append('<footer>此模块独立保存用户记录。未写老师观点、人工最票、关注池或交易计划；历史正式复盘保持原档。</footer></main></html>')
    return "".join(parts), errors


def build_reader(archive, input_by):
    if not input_by or not input_by.strip():
        raise ValueError("input-by 不得为空")
    html, errors = render_archive(archive)
    archive = Path(archive)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".reader-", suffix=".html", dir=archive, delete=False) as handle:
        temp = Path(handle.name)
        handle.write(html)
    try:
        temp.replace(archive / "index.html")
    finally:
        temp.unlink(missing_ok=True)
    receipt = dict(status="source_failed" if errors else "complete", path=str((archive / 'index.html').resolve()),
                   errors=errors, input_by=input_by.strip(),
                   built_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), html_sha256=digest(html.encode('utf-8')))
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.receipt-', dir=archive, delete=False) as handle:
        receipt_temp = Path(handle.name)
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
    try:
        receipt_temp.replace(archive / 'reader-receipt.json')
    finally:
        receipt_temp.unlink(missing_ok=True)
    return receipt
