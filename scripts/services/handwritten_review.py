"""只读核验手写复盘的原稿、整理稿及追加修订链。"""
from datetime import date, datetime
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

SECTIONS = ("前日锚点逐条验证", "固定对象跨日反馈", "次日具体条件", "原文待核事项")


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
