"""Read-only, source-attributed teacher anchors for the daily HTML report."""
from contextlib import closing
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
import sqlite3


KINDS = {'anchor': '老师核心锚点', 'level': '老师提到的指数关键点位'}
STATUSES = {'pending': '待盘面验证', 'historical': '历史观点', 'uncertain': '待核原文', 'not-stated': '未给具体点位'}
START, END = '<!-- teacher-anchors:start -->', '<!-- teacher-anchors:end -->'


def _text(value):
    return re.sub(r'\s+', '', value or '')


class _Input(HTMLParser):
    """Only plain p elements are accepted; the source is never passed through."""
    def __init__(self, report_date):
        super().__init__(convert_charrefs=True)
        self.day, self.rows, self.active, self.parts = report_date, [], None, []

    def handle_starttag(self, tag, attrs):
        fields = {'data-kind', 'data-report-date', 'data-note-id', 'data-source-date',
                  'data-subject', 'data-level', 'data-status', 'data-quote'}
        row = dict(attrs)
        if tag != 'p' or self.active is not None or set(row) != fields or len(attrs) != len(fields):
            raise ValueError('老师锚点输入仅接受带八项属性的纯文本 p')
        if not all(isinstance(value, str) for value in row.values()):
            raise ValueError('老师锚点属性不得省略取值')
        if row['data-kind'] not in KINDS or row['data-report-date'] != self.day:
            raise ValueError('老师锚点类型或复核日不符')
        if (not row['data-note-id'].isdigit() or not row['data-subject'].strip() or
                row['data-status'] not in STATUSES or not row['data-quote'].strip()):
            raise ValueError('老师锚点缺少来源、对象、状态或原文')
        if not 0 < int(row['data-note-id']) <= 9223372036854775807:
            raise ValueError('老师记录 ID 超出有效范围')
        source_day = date.fromisoformat(row['data-source-date'])
        if source_day > date.fromisoformat(self.day):
            raise ValueError('老师观点晚于复核日')
        if row['data-kind'] == 'level' and not row['data-level'].strip():
            raise ValueError('指数点位须填写数值或明确未提供/待核')
        self.active, self.parts = row, []

    def handle_data(self, data):
        if self.active is not None:
            self.parts.append(data)
        elif data.strip():
            raise ValueError('老师锚点输入含表外文本')

    def handle_endtag(self, tag):
        if tag != 'p' or self.active is None:
            raise ValueError('老师锚点标签未配对')
        summary = ''.join(self.parts).strip()
        if not summary:
            raise ValueError('老师锚点必须保留确认条件或缺口')
        self.rows.append(dict(self.active, summary=summary))
        self.active = None

    def handle_comment(self, data):
        raise ValueError('老师锚点输入不接受注释')


def load(path, report_date, db_path):
    """Verify attribution and quoted evidence against the selected read-only DB.

    Quote matching proves provenance, not semantic entailment: the author/reviewer
    must still check that the summary preserves the teacher's conditions.
    """
    date.fromisoformat(report_date)
    if not Path(path).is_file():
        return dict(report_date=report_date, status='missing-data', rows=[], reason='本次尚未整理老师锚点来源')
    try:
        parser = _Input(report_date)
        parser.feed(Path(path).read_text(encoding='utf-8'))
        parser.close()
        if parser.active is not None or not parser.rows:
            raise ValueError('老师锚点文件为空或未闭合')
        rows, seen = [], set()
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            for item in parser.rows:
                key = (item['data-kind'], item['data-note-id'], item['data-subject'])
                if key in seen:
                    raise ValueError('同一来源和观察对象重复')
                seen.add(key)
                note = conn.execute('SELECT n.date,t.name,n.raw_content FROM teacher_notes n '
                                    'JOIN teachers t ON t.id=n.teacher_id WHERE n.id=?',
                                    (int(item['data-note-id']),)).fetchone()
                if note is None or note[0] != item['data-source-date']:
                    raise ValueError('老师记录不存在或归档日不符')
                if _text(item['data-quote']) not in _text(note[2]):
                    raise ValueError('老师锚点引文与原始记录不符')
                rows.append(dict(kind=item['data-kind'], note_id=int(item['data-note-id']),
                                 date=note[0], teacher=note[1], subject=item['data-subject'],
                                 level=item['data-level'], status=item['data-status'],
                                 quote=item['data-quote'], summary=item['summary']))
        return dict(report_date=report_date, status='partial', rows=rows,
                    reason='老师观点摘录；不代表覆盖全部观点，条件尚待核验')
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
        return dict(report_date=report_date, status='source_failed', rows=[], reason=str(exc))


def render(payload, report_date):
    payload = payload or dict(report_date=report_date, status='missing-data', rows=[], reason='本次尚未整理老师锚点来源')
    if payload.get('report_date') != report_date:
        raise ValueError('老师锚点复核日不符')
    rows = payload.get('rows', [])
    out = [START, '<div class="teacher-observations" data-teacher-observations="v1">',
           '<p class="sub">[老师观点] '+escape(report_date)+' 复核摘要；条件尚待验证，点位仅为来源观察。</p>']
    for kind, title in KINDS.items():
        selected = [row for row in rows if row['kind'] == kind]
        marker = 'data-teacher-core-anchors' if kind == 'anchor' else 'data-teacher-index-levels'
        out.append('<h3>'+title+'</h3>')
        block_status = payload['status'] if selected or not rows else 'missing-data'
        attrs = f'{marker}="v1" data-as-of="{report_date}" data-source-status="{escape(block_status)}"'
        if not selected:
            reason = '本次尚未整理此项来源' if rows else payload.get('reason') or '本次来源未整理此项'
            out.append(f'<p {attrs}>[事实] '+escape(reason)+'；缺失不等于老师没有观点或没有关键点位。</p>')
            continue
        # 按来源顺序摘录每组首条完整摘要，不截断条件，也不宣称代表全部观点。
        groups = {}
        unstated = []
        for row in selected:
            if kind == 'level' and row['status'] == 'not-stated':
                unstated.append(row)
                continue
            key = row['teacher'] if kind == 'anchor' else row['subject'].split('·', 1)[0]
            groups.setdefault(key, []).append(row)
        out.append('<div role="list" data-teacher-summary="'+kind+'">')
        for group, observations in groups.items():
            row = observations[0]
            source_label = row['teacher']+' · '+STATUSES[row['status']]
            if row['date'] != report_date:
                source_label += ' · 历史归档 '+row['date']
            out.append('<div role="listitem" style="display:flex;flex-wrap:wrap;gap:4px 16px;padding:8px 0;border-bottom:1px solid #cbd5e1;overflow-wrap:anywhere">'+
                       '<div style="flex:1 1 170px;min-width:0"><strong>'+escape(group)+
                       '</strong><div style="font-size:12px">'+escape(source_label)+
                       f' · 摘录1/{len(observations)}条</div></div>'+
                       '<div style="flex:3 1 280px;min-width:0"><strong>'+escape(row['subject'])+'</strong><br>')
            if kind == 'level':
                out.append('<strong>'+escape(row['level'])+'</strong><br>')
            out.append(escape(row['summary']))
            if len(observations) > 1:
                others = '、'.join(dict.fromkeys(r['teacher'] for r in observations[1:]))
                out.append('<div style="font-size:12px">'+escape(f'另{len(observations)-1}条来源观察（{others}），可展开完整对照；'+
                           ('不合并点位或确认条件。' if kind == 'level' else '摘录不代表全部观点。'))+'</div>')
            out.append('</div></div>')
        if unstated:
            names = '、'.join(dict.fromkeys(row['teacher'] for row in unstated))
            out.append('<div role="listitem" style="padding:8px 0">'+escape(names)+
                       f'：所列来源未给具体点位（{len(unstated)}条，原因见完整对照）。</div>')
        out.append('</div>')
        headers = ['老师 / 归档日', '观察对象', '核心锚点与确认条件'] if kind == 'anchor' else ['老师 / 归档日', '指数 / 点位', '性质、条件与状态']
        out.append(f'<details class="evidence" data-evidence-kind="teacher-{kind}" data-as-of="{report_date}" data-items="{len(selected)}"><summary>{title}完整出处（{len(selected)}项）</summary><div class="evidence-body">')
        out.append(f'<div class="table-scroll-shell"><table {attrs}><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>')
        for row in selected:
            ref = f'teacher-anchor-source-{row["note_id"]}'
            age = '当日归档' if row['date'] == report_date else '历史观点'
            subject = row['subject'] + ('：'+row['level'] if kind == 'level' else '')
            out.append('<tr><td>'+escape(row['teacher'])+'<br>'+escape(row['date'])+' · '+age+
                       f' <a href="#{ref}">#{row["note_id"]}</a></td><td>'+escape(subject)+
                       '</td><td>'+escape(row['summary'])+' <small>〔'+STATUSES[row['status']]+'〕</small></td></tr>')
        out.append('</tbody></table></div></div></details>')
    if rows:
        out.append(f'<details class="evidence" data-as-of="{report_date}" data-items="{len(rows)}"><summary>老师锚点原文依据（{len(rows)}项）</summary><div class="evidence-body">')
        out.append('<p>[老师观点] 以下保留来源条件，未视为已兑现事实；归档日不等于原始发言日。点位是来源观察，不是系统目标价。</p>')
        ids = set()
        for row in rows:
            ident = '' if row['note_id'] in ids else f' id="teacher-anchor-source-{row["note_id"]}"'
            ids.add(row['note_id'])
            out.append(f'<p{ident}>[老师原文] '+escape(row['teacher']+' · '+row['date']+' · '+row['subject'])+
                       f' · teacher_notes#{row["note_id"]}：'+escape(row['quote'])+'</p>')
        out.append('</div></details>')
    out.extend(['</div>', END])
    return '\n'.join(out)


def validate(html, report_date, payload=None):
    matches = re.findall(re.escape(START)+'.*?'+re.escape(END), html, re.S)
    if len(matches) != 1 or matches[0] != render(payload, report_date):
        raise ValueError('老师核心锚点与指数点位须与本次核验来源一致且只出现一次')
