"""
采集阶段临时关闭标准 HTTP 代理环境变量。

Tushare / AkShare 等国内数据源应直连；shell 里常见的 Clash 代理（如 127.0.0.1:7897）
会导致 urllib3 超时。推送 Discord/企业微信时仍可使用代理，故用上下文在采集后恢复环境。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def use_http_proxy_for_data_collection() -> bool:
    """为真时保留代理（与默认行为相反，供特殊网络环境使用）。"""
    v = os.environ.get("TRADESYSTEM_USE_HTTP_PROXY", "").strip().lower()
    return v in ("1", "true", "yes", "on")


@contextmanager
def without_standard_http_proxy():
    """临时移除常见代理变量，退出时恢复。"""
    if use_http_proxy_for_data_collection():
        yield
        return
    saved = {k: os.environ.pop(k) for k in _PROXY_KEYS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)
