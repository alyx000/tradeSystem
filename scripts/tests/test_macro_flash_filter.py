"""filter 单测:关键词归组顺序、important 强制入选、空配置 fail fast。"""
import pytest

from services.macro_flash import filter as flash_filter

KW_CONFIG = {"macro_flash": {"keywords": {
    "货币政策": ["央行", "降准"],
    "财政债券": ["地方债", "国债"],
}}}


def _item(iid, content, important=0, title=""):
    return {"id": iid, "time": "2026-07-23 10:00:00", "important": important,
            "data": {"content": content, "title": title}}


def test_load_config_ok():
    kw = flash_filter.load_keyword_config(KW_CONFIG)
    assert list(kw) == ["货币政策", "财政债券"]  # 保序:归组按声明顺序


@pytest.mark.parametrize("bad", [
    {},                                        # 整段缺失
    {"macro_flash": {}},                       # keywords 缺失
    {"macro_flash": {"keywords": {}}},         # 空 dict
    {"macro_flash": {"keywords": {"a": []}}},  # 词表全空
])
def test_load_config_fail_fast(bad):
    with pytest.raises(ValueError):
        flash_filter.load_keyword_config(bad)


def test_first_topic_wins():
    """跨主题命中按声明顺序取首个:内容同时含 央行+国债 → 货币政策。"""
    kw = flash_filter.load_keyword_config(KW_CONFIG)
    out = flash_filter.filter_items([_item("a", "央行下场买国债")], kw)
    assert out[0].topic == "货币政策"


def test_title_also_matched():
    kw = flash_filter.load_keyword_config(KW_CONFIG)
    out = flash_filter.filter_items([_item("a", "正文无词", title="浙江地方债定价调整")], kw)
    assert out[0].topic == "财政债券"


def test_important_forced_into_other_topic():
    """无关键词命中但 important → 强制入选「其他要闻」。"""
    kw = flash_filter.load_keyword_config(KW_CONFIG)
    out = flash_filter.filter_items([_item("a", "特斯拉暴跌", important=1)], kw)
    assert out[0].topic == flash_filter.OTHER_TOPIC


def test_no_hit_excluded():
    kw = flash_filter.load_keyword_config(KW_CONFIG)
    assert flash_filter.filter_items([_item("a", "某公司发布新手机")], kw) == []


def test_real_config_disambiguates_overseas_vs_domestic():
    """真实 config.yaml:货币政策不再含宽词「降息」,美联储/欧央行降息不再被误分到货币政策。"""
    import pathlib

    import yaml

    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    kw = flash_filter.load_keyword_config(cfg)
    fed = flash_filter.filter_items([_item("a", "美联储降息预期升温")], kw)
    assert fed[0].topic == "海外宏观"
    pboc = flash_filter.filter_items([_item("b", "央行宣布降息 0.25 个百分点")], kw)
    assert pboc[0].topic == "货币政策"


def test_real_config_overseas_central_banks_not_shadowed():
    """真实 config.yaml:欧央行/日央行含「央行」子串,最长匹配优先 → 仍归海外宏观,不被货币政策遮蔽。"""
    import pathlib

    import yaml

    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    kw = flash_filter.load_keyword_config(cfg)
    ecb = flash_filter.filter_items([_item("a", "欧央行降息预期升温")], kw)
    assert ecb[0].topic == "海外宏观"
    boj = flash_filter.filter_items([_item("b", "日央行加息概率上升")], kw)
    assert boj[0].topic == "海外宏观"


def test_real_config_declaration_priority_and_substring_shadow():
    """回归用例(门2 codex 第3轮指出):"最长匹配全局胜出"矫枉过正,压过了声明顺序契约。
    央行+地方债/财政部 同时出现时,央行是真实命中(非子串误命中),应按声明顺序归货币政策,
    不应因「地方债」/「财政部」字面更长就被判给财政债券。"""
    import pathlib

    import yaml

    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    kw = flash_filter.load_keyword_config(cfg)

    mixed = flash_filter.filter_items([_item("a", "央行与财政部讨论地方债支持安排")], kw)
    assert mixed[0].topic == "货币政策"

    ecb = flash_filter.filter_items([_item("b", "欧央行降息预期升温")], kw)
    assert ecb[0].topic == "海外宏观"

    boj = flash_filter.filter_items([_item("c", "日央行加息概率上升")], kw)
    assert boj[0].topic == "海外宏观"

    fed = flash_filter.filter_items([_item("d", "美联储降息预期升温")], kw)
    assert fed[0].topic == "海外宏观"

    pboc = flash_filter.filter_items([_item("e", "央行宣布降息 0.25 个百分点")], kw)
    assert pboc[0].topic == "货币政策"


def test_real_config_overseas_central_bank_full_names():
    """海外央行全称(欧洲央行/日本央行/英国央行)须归海外宏观:
    全称含「央行」子串,靠最长匹配盖过 货币政策 的裸「央行」。"""
    import pathlib

    import yaml

    cfg_path = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    kw = flash_filter.load_keyword_config(cfg)
    for iid, text in [("a", "欧洲央行考虑提高准备金要求"),
                      ("b", "日本央行行长植田和男发表讲话"),
                      ("c", "英国央行维持利率不变")]:
        got = flash_filter.filter_items([_item(iid, text)], kw)
        assert got and got[0].topic == "海外宏观", f"{text!r} → {got[0].topic if got else None}"
    # 国内央行不受影响
    pboc = flash_filter.filter_items([_item("d", "央行宣布降准0.5个百分点")], kw)
    assert pboc[0].topic == "货币政策"
