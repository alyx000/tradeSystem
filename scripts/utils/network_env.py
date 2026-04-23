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

_NO_PROXY_KEYS = (
    "NO_PROXY",
    "no_proxy",
)


def use_http_proxy_for_data_collection() -> bool:
    """为真时保留代理（与默认行为相反，供特殊网络环境使用）。"""
    v = os.environ.get("TRADESYSTEM_USE_HTTP_PROXY", "").strip().lower()
    return v in ("1", "true", "yes", "on")


@contextmanager
def without_standard_http_proxy():
    """临时移除常见代理变量，退出时恢复。

    仅删除环境变量还不够：requests/urllib 在 macOS 上还可能从系统网络配置
    回退解析代理，因此这里额外强制 `NO_PROXY=*`，确保采集阶段一律直连。
    """
    if use_http_proxy_for_data_collection():
        yield
        return
    restore_keys = _PROXY_KEYS + _NO_PROXY_KEYS
    saved = {k: os.environ.get(k) for k in restore_keys if k in os.environ}
    for key in _PROXY_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        for key in restore_keys:
            os.environ.pop(key, None)
        os.environ.update(saved)
