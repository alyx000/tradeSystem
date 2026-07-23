"""formatter 单测:结构断言、预算截断、无表格、状态文案。"""
import datetime as dt

from services.macro_flash import formatter
from services.macro_flash.filter import FlashCandidate

W_START = dt.datetime(2026, 7, 22, 16, 30)
W_END = dt.datetime(2026, 7, 23, 16, 30)
TOPICS = ["货币政策", "财政债券"]


def _cand(iid, topic, content, important=0):
    return FlashCandidate(topic=topic, item={
        "id": iid, "time": "2026-07-23 10:05:00", "important": important,
        "data": {"content": content, "title": ""}})


def _digest(cands, status="complete", raw=100):
    return formatter.build_digest_markdown(
        cands, window_start=W_START, window_end=W_END,
        source_status=status, raw_count=raw, topic_order=TOPICS)


def test_digest_structure():
    md = _digest([_cand("a", "货币政策", "央行降准 0.5 个百分点", important=1),
                  _cand("b", "财政债券", "浙江调整地方债投标利率下限")])
    assert "宏观快讯速读 · 2026-07-23" in md
    assert "## 货币政策(1)" in md and "## 财政债券(1)" in md
    assert "⭐" in md            # important 标记
    assert "**10:05**" in md    # 时间前缀
    assert "|" not in md        # 钉钉兼容:禁表格


def test_topic_order_follows_declaration():
    md = _digest([_cand("b", "财政债券", "国债"), _cand("a", "货币政策", "央行")])
    assert md.index("货币政策") < md.index("财政债券")


def test_empty_result_message():
    md = _digest([], raw=42)
    assert "无命中宏观快讯" in md and "42" in md


def test_html_tags_stripped():
    md = _digest([_cand("a", "货币政策", "<b>央行</b><br>降准")])
    assert "<b>" not in md and "央行" in md


def test_push_within_budget_unchanged():
    md = _digest([_cand("a", "货币政策", "央行降准")])
    assert formatter.build_push_markdown(md, "data/runs/x/digest.md") == md


def test_push_over_budget_truncates_whole_blocks():
    """超预算按主题块整块截断,尾部提示完整文件路径。"""
    big = [_cand(f"a{i}", "货币政策", "央行公开市场操作详情" * 30) for i in range(200)]
    big += [_cand(f"b{i}", "财政债券", "地方债发行细节说明文本" * 30) for i in range(200)]
    md = _digest(big)
    out = formatter.build_push_markdown(md, "data/runs/x/digest.md")
    assert len(out.encode("utf-8")) <= formatter.PUSH_BODY_MAX_BYTES
    assert "data/runs/x/digest.md" in out          # 截断提示
    assert "## 财政债券" not in out                 # 整块被裁,不出现半块


def test_status_push_mentions_status():
    out = formatter.build_status_push("source_failed", window_start=W_START,
                                      window_end=W_END, error="timeout")
    assert "source_failed" in out and "timeout" in out
