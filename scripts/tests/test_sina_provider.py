"""SinaProvider 实时行情单测（mock 隔离真网）"""
import os

import pytest

from providers.sina_provider import SinaProvider, _normalize_code, _to_sina_symbol


def _line(symbol: str, name: str, o: str, pre: str, price: str, hi: str, lo: str,
          vol: str, amt: str, date: str = "2026-07-10", tm: str = "15:36:00") -> str:
    """构造 33 字段标准新浪行情行（字段6/7=买卖价、10-29=五档，填 0）"""
    fields = [name, o, pre, price, hi, lo, "0", "0", vol, amt] + ["0"] * 20 + [date, tm, "00"]
    return f'var hq_str_{symbol}="{",".join(fields)}";'


MAOTAI = _line("sh600519", "贵州茅台", "1182.200", "1182.190", "1204.980",
               "1204.980", "1170.280", "5221255", "6223343642.000")
SH_INDEX = _line("sh000001", "上证指数", "4031.5373", "4036.5879", "3996.1616",
                 "4074.8280", "3995.8072", "627450065", "1563108691543",
                 tm="15:30:39")


class FakeResp:
    def __init__(self, content: bytes = b"", ok: bool = True):
        self.content = content
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("500 Server Error")


class FakeSession:
    """按序返回预设响应；记录请求 URL"""

    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)


@pytest.fixture
def prov():
    p = SinaProvider()
    p.initialize()
    return p


# --- 代码归一化 / 转换 / 输入校验 ---

def test_normalize_bare_codes():
    assert _normalize_code("600519") == "600519.SH"
    assert _normalize_code("000001") == "000001.SZ"
    assert _normalize_code("430047") == "430047.BJ"
    assert _normalize_code(" 600519.sh ") == "600519.SH"
    assert _normalize_code("") == ""


def test_to_sina_symbol():
    assert _to_sina_symbol("600519.SH") == "sh600519"
    assert _to_sina_symbol("399006.SZ") == "sz399006"
    assert _to_sina_symbol("430047.BJ") == "bj430047"
    assert _to_sina_symbol("932000.CSI") is None
    # 非 6 位纯数字一律拒绝（URL 注入面）
    assert _to_sina_symbol("600000,sz000001.SH") is None
    assert _to_sina_symbol("60051.SH") is None
    assert _to_sina_symbol("ABCDEF.SH") is None


def test_unsupported_codes_rejected_into_note(prov, monkeypatch):
    """CSI 与注入类畸形串同走「拒绝进 note、不发请求」路径（分类逻辑由
    test_to_sina_symbol 纯函数覆盖，此处只留一个全路径集成冒烟）"""
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI])
    result = prov.get_realtime_quotes(["600519.SH", "932000.CSI", "600000,sz000001.SH"])
    assert result.success
    assert len(result.data) == 1
    assert "932000.CSI(新浪不支持或代码非法)" in result.note
    assert "600000,SZ000001.SH(新浪不支持或代码非法)" in result.note


# --- capability 声明 / 基类默认桩 ---

def test_capabilities_declared(prov):
    assert prov.get_capabilities() == ["get_realtime_quotes"]
    assert prov.supports("get_realtime_quotes")


def test_base_provider_default_stub():
    from providers.tushare_provider import TushareProvider

    p = TushareProvider()
    assert not p.get_realtime_quotes(["600519.SH"]).success
    assert not p.supports("get_realtime_quotes")


# --- 解析主路径 ---

def test_quotes_parse_success(prov, monkeypatch):
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI, SH_INDEX])
    result = prov.get_realtime_quotes(["600519.SH", "000001.SH"])
    assert result.success
    assert len(result.data) == 2
    q = {r["code"]: r for r in result.data}
    mt = q["600519.SH"]
    assert mt["name"] == "贵州茅台"
    assert mt["price"] == 1204.98
    assert mt["pre_close"] == 1182.19
    assert mt["pct_chg"] == pytest.approx(1.9278, abs=1e-3)
    assert mt["quote_date"] == "2026-07-10"
    assert mt["quote_time"] == "15:36:00"
    idx = q["000001.SH"]
    assert idx["name"] == "上证指数"
    assert idx["price"] == pytest.approx(3996.1616)
    assert result.source == "sina"
    assert result.timeliness.value == "[实时]"


def test_bare_code_input_normalized(prov, monkeypatch):
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI])
    result = prov.get_realtime_quotes(["600519"])
    assert result.success
    assert result.data[0]["code"] == "600519.SH"


# --- 单码脏值跳过 + note ---

@pytest.mark.parametrize("dirty_line", [
    pytest.param('var hq_str_sz000001="";', id="empty-body"),
    pytest.param('var hq_str_sz000001="平安银行,1,2,3";', id="short-fields"),
    pytest.param(_line("sz000001", "平安银行", "abc", "1.0", "1.0", "1.0", "1.0", "0", "0"),
                 id="numeric-garbage"),
])
def test_dirty_single_code_skipped_into_note(prov, monkeypatch, dirty_line):
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI, dirty_line])
    result = prov.get_realtime_quotes(["600519.SH", "000001.SZ"])
    assert result.success
    assert len(result.data) == 1
    assert "000001.SZ" in result.note


def test_missing_response_line_noted(prov, monkeypatch):
    """请求了两码，响应只回一行——缺行不静默吞，进 note"""
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI])
    result = prov.get_realtime_quotes(["600519.SH", "000002.SZ"])
    assert result.success
    assert len(result.data) == 1
    assert "000002.SZ(响应缺失)" in result.note


def test_suspended_price_zero_skipped(prov, monkeypatch):
    """停牌股新浪返回完整布局但 price=0（真机 430047.BJ 实测）——跳过进 note，
    不返回 price=0 + pct_chg=-100% 的误导行"""
    suspended = _line("bj430047", "诺思兰德", "0.000", "2.610", "0.000", "0.000",
                      "0.000", "0", "0.000", date="2026-06-25", tm="12:20:47")
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [MAOTAI, suspended])
    result = prov.get_realtime_quotes(["600519.SH", "430047.BJ"])
    assert result.success
    assert len(result.data) == 1
    assert "430047.BJ(停牌或无最新价)" in result.note


def test_pre_close_zero_pct_none(prov, monkeypatch):
    new_stock = _line("sh688999", "新股", "10.0", "0.000", "11.0", "11.5", "9.8",
                      "1000", "11000")
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [new_stock])
    result = prov.get_realtime_quotes(["688999.SH"])
    assert result.success
    assert result.data[0]["pct_chg"] is None


# --- 非法入参 ---

def test_empty_codes_error(prov):
    result = prov.get_realtime_quotes([])
    assert not result.success


def test_all_invalid_codes_error(prov):
    result = prov.get_realtime_quotes(["932000.CSI", ""])
    assert not result.success
    assert "932000.CSI" in result.note


def test_all_bodies_empty_error(prov, monkeypatch):
    dead = 'var hq_str_sh600519="";'
    monkeypatch.setattr(prov, "_fetch_raw", lambda symbols: [dead])
    result = prov.get_realtime_quotes(["600519.SH"])
    assert not result.success


# --- HTTP 层（headers / GBK / 分片 / 失败语义） ---

def test_session_headers_have_referer(prov):
    session = prov._make_session()
    assert session.headers["Referer"] == "https://finance.sina.com.cn"
    assert "User-Agent" in session.headers


def test_http_error_returns_error(prov, monkeypatch):
    def boom(symbols):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(prov, "_fetch_raw", boom)
    result = prov.get_realtime_quotes(["600519.SH"])
    assert not result.success
    assert "403" in result.error


def test_fetch_raw_decodes_gbk(prov, monkeypatch):
    session = FakeSession([FakeResp(MAOTAI.encode("gbk"))])
    monkeypatch.setattr(prov, "_make_session", lambda: session)
    lines = prov._fetch_raw(["sh600519"])
    assert any("贵州茅台" in line for line in lines)


def test_batching_over_800(prov, monkeypatch):
    session = FakeSession([FakeResp(MAOTAI.encode("gbk")), FakeResp(MAOTAI.encode("gbk"))])
    monkeypatch.setattr(prov, "_make_session", lambda: session)
    codes = [f"{600000 + i}.SH" for i in range(801)]
    prov.get_realtime_quotes(codes)
    assert len(session.calls) == 2


def test_partial_chunk_failure_whole_error(prov, monkeypatch):
    """第 2 分片失败 → 整体 error 且带分片定位，不返回第 1 片残缺数据"""
    session = FakeSession([FakeResp(MAOTAI.encode("gbk")), FakeResp(ok=False)])
    monkeypatch.setattr(prov, "_make_session", lambda: session)
    codes = [f"{600000 + i}.SH" for i in range(801)]
    result = prov.get_realtime_quotes(codes)
    assert not result.success
    assert "分片 2/2" in result.error
    assert result.data is None


# --- registry 路由 / 生产注册路径 ---

def test_registry_routes_realtime_quotes(monkeypatch):
    from providers import ProviderRegistry, SinaProvider as ExportedSina

    registry = ProviderRegistry()
    p = ExportedSina()
    p.initialize()
    monkeypatch.setattr(p, "_fetch_raw", lambda symbols: [MAOTAI])
    registry.register(p)
    result = registry.call("get_realtime_quotes", ["600519.SH"])
    assert result.success
    assert result.source == "sina"


def test_setup_providers_registers_sina():
    """生产注册路径：漏改 main.py/config 时此测试必须红"""
    import main as main_module

    registry = main_module.setup_providers({
        "providers": {
            "sina": {"enabled": True, "priority": 3},
            "tdx": {"enabled": False},
        }
    })
    sina = registry.get_provider("sina")
    assert sina is not None
    assert sina.priority == 3
    assert sina.supports("get_realtime_quotes")


# --- 真机抽检（SINA_SMOKE=1 时执行；周末返回收盘快照亦可验证布局） ---

@pytest.mark.skipif(not os.getenv("SINA_SMOKE"), reason="真机抽检：SINA_SMOKE=1 pytest -k real_network 显式执行")
def test_real_network_smoke():
    p = SinaProvider()
    p.initialize()
    result = p.get_realtime_quotes(["600519.SH", "000001.SH", "430047.BJ"])
    assert result.success
    got = {r["code"]: r for r in result.data}
    # 指数实时是核心承诺：必须显式命中，且 30/31 字段布局假设成立
    assert "000001.SH" in got
    idx = got["000001.SH"]
    assert idx["price"] > 0
    assert idx["quote_date"] and idx["quote_time"]
    assert all(r["price"] > 0 for r in result.data)
