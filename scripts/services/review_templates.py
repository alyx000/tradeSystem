"""只读复盘教学模板：保留原文，按日期接收有来源的分析，不产生交易计划。"""

from __future__ import annotations

import base64
from functools import lru_cache
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path


ASSETS = (Path(__file__).resolve().parents[2] / ".agents/skills/daily-review/"
          "references/html-report-template/template_assets")
SOURCE_NAME = "板块推演填空和龙头接力模版加思维导图.docx"
SOURCE_REFS = {
    "s0": "前日对照", "s1": "大势", "s2": "板块", "s3": "情绪",
    "s4": "风格", "s5": "龙头", "s6": "节点", "s7": "持仓",
    "teachers": "老师观点", "industry": "行业", "cognition": "认知",
    "s8": "次日核验", "ops": "数据缺口",
}
# 保留原模板所有栏目；交易动作栏目在只读报告中只记录用户已给出的内容。
FIELDS = {
    "sector": (
        ("scan", "一、全市场板块扫描", "当前大势；今日热门板块清单。"),
        ("cycle", "二、各板块周期定性", "逐板块记录小周期（启动/发酵/高潮/分歧/衰退）、大周期（将成龙/主升/震荡/二波/衰退）、连接点加强或减弱、是否充分演绎。"),
        ("priority", "三、板块优先级筛选", "A 日K强且短线走强；B 近期活跃、调整后趋势尚在并止跌；C 新启动、逻辑及个股趋势好；D 新启动、联动强；E 有逻辑的超跌止跌。列重点方向及证据。"),
        ("character", "四、强势板块主动定性", "大势支持、驱动逻辑/基本面、分歧不死或超预期走强、大主线潜质；将成龙/主升初期/主升后期/充分演绎对应的模式适配证据。"),
        ("return", "五、板块回流机会评估", "最强板块是否转弱；前期活跃方向的调整时长；启动高潮后分化的承接、替代主线与额外催化。"),
        ("exhaustion", "六、充分演绎板块生死甄别", "反复演绎、高位分布、逻辑熟知程度；高辨识度票创新高或走弱、弹性新票与结束风险。"),
        ("logic", "七、逻辑审美打分", "增量空间、落地期限、走势阶段、驱动力量、题材具体度和容量；中期分歧后的反弹是否逐级抬高。"),
        ("rolling", "八、格局与滚动计划", "是否已有正反馈、计划标的、滚动/锁仓/短线选择及负反馈失效条件；操作选择由用户填写。"),
        ("auction", "九、集合竞价盘前校验", "次日核心发动机是否超预期/符合/低于预期，板块批量开盘表现；收盘时保持待盘前验证。"),
        ("risk", "十、风险与仓位", "主要风险；计划总仓位和试错仓位由用户填写。"),
        ("reflection", "十一、收盘复盘反思", "前次推演与本次盘面是否匹配、偏差与经验；须有此前推演和实际反馈。"),
    ),
    "relay": (
        ("market", "一、市场总览", "大盘状态；主线高位分歧/第1至3次加速/老主线回落与新题材试错/多题材混战；连板晋级率及连续2天龙头晋级失败、跌停反馈未修复的核验。"),
        ("choice", "二、盘后三选一决策", "A 高标接力：板块前三高度核心、三项加持与同梯队逻辑对照；B 高低切：主线内部补涨或全新题材、大小题材及接替核心证据；C 趋势分歧：逻辑、阶段及模式条件。缺证据可待判断，不自动选交易动作。"),
        ("auction", "三、盘前集合竞价预期校验", "次日核心发动机与板块批量开盘；新题材是否批量一字/高开联动；未开盘不填已发生结果。"),
        ("risk", "四、风险提示与仓位计划", "系统性风险、板数密集竞争、新题材后排和纯筹码高标风险；总仓位、试错仓位与小概率穿越标的仓位由用户填写。"),
        ("reflection", "五、当日总结", "用户是否执行/放弃、原因、模式匹配与得失反思；无执行记录不推定。"),
    ),
}
TITLES = {"sector": "板块推演填空", "relay": "情绪周期龙头接力"}
STATUSES = {"complete": "已整理", "partial": "部分待补充", "missing-data": "待补充",
            "source_failed": "来源失败", "pending": "待验证", "user-input": "待用户填写"}


@dataclass(frozen=True)
class Answer:
    text: str
    status: str
    refs: tuple[str, ...]


class _AnswerParser(HTMLParser):
    """答案文件仅接受纯文本 p；不执行/透传来源 HTML、链接或脚本。"""

    def __init__(self, report_date: str):
        super().__init__(convert_charrefs=True)
        self.report_date = report_date
        self.answers: dict[str, Answer] = {}
        self.active: dict[str, str] | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        allowed = {"data-template-field", "data-as-of", "data-status", "data-source-refs"}
        values = dict(attrs)
        if tag != "p" or self.active is not None or set(values) != allowed or len(attrs) != 4:
            raise ValueError("模板答案只接受带四个规定属性的纯文本 p")
        keys = {f"{kind}.{key}" for kind, fields in FIELDS.items() for key, _, _ in fields}
        key = values["data-template-field"]
        if key not in keys or key in self.answers:
            raise ValueError(f"未知或重复模板字段：{key}")
        if values["data-as-of"] != self.report_date:
            raise ValueError(f"模板答案日期不匹配：{key}")
        if values["data-status"] not in STATUSES:
            raise ValueError(f"模板答案状态非法：{key}")
        refs = (values["data-source-refs"] or "").split()
        if any(ref not in SOURCE_REFS for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError(f"模板答案来源锚点非法：{key}")
        if values["data-status"] in {"complete", "partial"} and not refs:
            raise ValueError(f"已有分析必须带来源：{key}")
        self.active, self.parts = values, []

    def handle_endtag(self, tag):
        if tag != "p" or self.active is None:
            raise ValueError("模板答案标签不完整")
        text = "".join(self.parts).strip()
        if not text or len(text) > 1400 or not text.startswith(("[事实]", "[判断]")):
            raise ValueError("模板答案须含事实/判断标签及1至1400字正文")
        attrs = self.active
        self.answers[attrs["data-template-field"]] = Answer(
            text, attrs["data-status"], tuple((attrs["data-source-refs"] or "").split()))
        self.active = None

    def handle_data(self, data):
        if self.active is not None:
            self.parts.append(data)
        elif data.strip():
            raise ValueError("模板答案不接受 p 之外的正文")

    def handle_comment(self, data):
        raise ValueError("模板答案不接受注释")

    def handle_decl(self, decl):
        raise ValueError("模板答案不接受文档声明")

    def handle_pi(self, data):
        raise ValueError("模板答案不接受处理指令")


def load_answers(directory: Path, report_date: str) -> dict[str, Answer]:
    path = directory / f"review_template_answers_{report_date}.html"
    if not path.exists():
        return {}
    if path.stat().st_size > 100_000:
        raise ValueError("模板答案文件过大")
    parser = _AnswerParser(report_date)
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    if parser.active is not None:
        raise ValueError("模板答案标签未闭合")
    return parser.answers


@lru_cache(maxsize=2)
def _assets(kind: str) -> tuple[str, str]:
    return ((ASSETS / f"{kind}_original.html").read_text(encoding="utf-8"),
            base64.b64encode((ASSETS / f"{kind}_mindmap.png").read_bytes()).decode("ascii"))


def render_templates(directory: Path, report_date: str, *,
                     answers: dict[str, Answer] | None = None) -> tuple[dict[str, str], str]:
    if answers is None:
        answers = load_answers(directory, report_date)
    fragments: dict[str, str] = {}
    total_missing = 0
    for kind, fields in FIELDS.items():
        cards = []
        incomplete = 0
        for key, title, prompt in fields:
            answer = answers.get(f"{kind}.{key}", Answer(
                "[事实] 待补充：本报告未提供该项专项核验，不能据此认定条件成立或不成立。",
                "missing-data", ()))
            incomplete += answer.status != "complete"
            links = " · ".join(f'<a href="#{ref}">{SOURCE_REFS[ref]}</a>' for ref in answer.refs)
            cards.append(
                f'<div class="template-card" data-template-field="{kind}.{key}" '
                f'data-source-status="{answer.status}" data-source-refs="{" ".join(answer.refs)}"><h4>{title}</h4>'
                f'<p class="template-prompt">{escape(prompt)}</p>'
                f'<p class="template-answer">{escape(answer.text)}</p>'
                f'<p class="template-status">{STATUSES[answer.status]}'
                f'{" · 依据：" + links if links else ""}</p></div>')
        total_missing += incomplete
        original, encoded = _assets(kind)
        status = "partial" if incomplete else "complete"
        count = len(fields)
        fragments[kind] = (
            f'<div class="review-template" data-review-template="{kind}" data-as-of="{report_date}" '
            f'data-source-status="{status}"><h3>{TITLES[kind]}</h3>'
            f'<p class="sub">[事实] 已整理 {count - incomplete}/{count} 项；其余 {incomplete} 项保留待补充、待验证或用户填写。</p>'
            f'<details class="evidence" data-as-of="{report_date}" data-items="{count}" '
            f'data-evidence-kind="review-template-{kind}"><summary>展开{TITLES[kind]}（{count} 项）</summary>'
            f'<div class="evidence-body"><div class="template-grid">{"".join(cards)}</div></div></details>'
            f'<details class="evidence" data-as-of="{report_date}" data-items="2">'
            '<summary>查看模板原文与思维导图（2 项）</summary><div class="evidence-body">'
            f'<p class="sub">[事实] 来源：用户提供《{SOURCE_NAME}》。以下是教学原文，不代表当日事实或本报告的操作指令；未据此生成买卖、仓位或正式计划。</p>'
            '<p class="sub">原文与图中口径差异保留：正文三选一为高标接力/高低切/趋势分歧，图中第三项为高低切细分；不混为已确认结论。</p>'
            f'<div class="template-original">{original}</div>'
            '<details class="template-map-zoom"><summary>放大思维导图，可横向滚动查看原图</summary>'
            f'<div class="template-map-scroll" tabindex="0" role="region" aria-label="{TITLES[kind]}思维导图原图">'
            f'<img src="data:image/png;base64,{encoded}" alt="{TITLES[kind]}原始思维导图" loading="lazy"></div></details>'
            '</div></details></div>')
    gap = (f"[事实] 新增模板共16项，其中{total_missing}项仍有待补充、待验证或用户填写内容；"
           "详见②板块推演与⑤龙头接力，未填不等于条件不成立。") if total_missing else ""
    return fragments, gap


class _RenderedParser(HTMLParser):
    """提取模块原始切片，重渲染逐字对账后才允许单独的模板预算。"""

    def __init__(self, html: str, hidden_classes: frozenset[str]):
        super().__init__(convert_charrefs=True)
        self.html = html
        self.hidden_classes = hidden_classes
        self.parents: list[tuple[str, str, bool]] = []
        self.gaps = 0
        self.offsets = [0]
        for line in html.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.sections: list[str] = []
        self.depth = 0
        self.kind = ""
        self.start = 0
        self.modules: dict[str, str] = {}
        self.answers: list[str] = []
        self.card: dict[str, str] = {}
        self.capture = False
        self.parts: list[str] = []

    def source_offset(self):
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def _require_visible_section_child(self, section: str):
        # 预算按独立的官方片段计量，因此实际插入点必须保留同样的显示上下文。
        # 拒绝额外折叠/隐藏包装，避免把隐藏的模板文字额度借给其他正文。
        if (not self.parents or self.parents[-1][:2] != ("section", section)
                or any(hidden for _, _, hidden in self.parents)):
            raise ValueError("模板及缺口必须位于指定章节的默认可见直接子节点")

    def handle_starttag(self, tag, attrs):
        values = {key: value or "" for key, value in attrs}
        if tag == "section":
            self.sections.append(values.get("id", ""))
        if "data-review-template" in values:
            kind = values["data-review-template"]
            section = next((value for value in reversed(self.sections) if value in SOURCE_REFS), "")
            if (tag != "div" or self.depth or kind in self.modules
                    or kind not in FIELDS or section != {"sector": "s2", "relay": "s5"}[kind]):
                raise ValueError("模板模块重复、嵌套或所在章节不正确")
            self._require_visible_section_child({"sector": "s2", "relay": "s5"}[kind])
            self.kind, self.start = kind, self.source_offset()
        if "data-review-template-gap" in values:
            if tag != "p" or values != {"data-review-template-gap": "v1"}:
                raise ValueError("模板缺口标记非法")
            self._require_visible_section_child("ops")
            self.gaps += 1
        style = "".join(values.get("style", "").lower().split())
        hidden = (tag == "details" or "hidden" in values
                  or values.get("aria-hidden", "").strip().lower() == "true"
                  or "display:none" in style or "visibility:hidden" in style
                  or bool(set(values.get("class", "").split()) & self.hidden_classes))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.parents.append((tag, values.get("id", ""), hidden))
        if self.kind and tag == "div":
            self.depth += 1
        if self.kind and "data-template-field" in values:
            self.card = values
        if self.kind and tag == "p" and values.get("class") == "template-answer":
            self.capture, self.parts = True, []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.capture:
            self.capture = False
            self.answers.append(
                f'<p data-template-field="{escape(self.card.get("data-template-field", ""))}" '
                f'data-as-of="DATE" data-status="{escape(self.card.get("data-source-status", ""))}" '
                f'data-source-refs="{escape(self.card.get("data-source-refs", ""))}">'
                f'{escape("".join(self.parts))}</p>')
        if self.kind and tag == "div":
            self.depth -= 1
            if self.depth == 0:
                end = self.html.index(">", self.source_offset()) + 1
                self.modules[self.kind] = self.html[self.start:end]
                self.kind = ""
        if tag == "section":
            if self.sections:
                self.sections.pop()
        for index in range(len(self.parents) - 1, -1, -1):
            if self.parents[index][0] == tag:
                del self.parents[index:]
                break


def validated_fragments(html: str, report_date: str, *,
                        hidden_classes: frozenset[str] = frozenset()) -> str:
    """旧报告可无模板；有模板则两份齐全，所有结构/原文/图片与官方渲染一致。"""
    parser = _RenderedParser(html, hidden_classes)
    parser.feed(html)
    parser.close()
    if not parser.modules and not parser.kind:
        if parser.gaps:
            raise ValueError("模板缺口不能脱离模板出现")
        return ""
    if set(parser.modules) != set(FIELDS) or parser.kind:
        raise ValueError("两套模板必须同时完整出现")
    answers = _AnswerParser(report_date)
    answers.feed("\n".join(parser.answers).replace('data-as-of="DATE"', f'data-as-of="{report_date}"'))
    answers.close()
    expected, gap = render_templates(Path(), report_date, answers=answers.answers)
    if parser.modules != expected:
        raise ValueError("模板内容、状态、日期、来源或图片与正式渲染不一致")
    gap_html = f'<p data-review-template-gap="v1">{escape(gap)}</p>' if gap else ""
    if (parser.gaps != bool(gap)
            or html.count('data-review-template-gap="v1"') != bool(gap)
            or (gap and gap_html not in html)):
        raise ValueError("模板缺口汇总不一致")
    return "".join(expected.values()) + gap_html
