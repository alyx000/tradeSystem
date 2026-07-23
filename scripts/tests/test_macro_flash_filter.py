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
