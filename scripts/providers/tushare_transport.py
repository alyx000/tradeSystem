"""固定镜像根路径的 Tushare 协议客户端，避免 SDK 升级改变请求路由。"""
from __future__ import annotations

from functools import partial

import pandas as pd
import requests


class TushareMirrorClient:
    def __init__(self, token: str, timeout: int = 30):
        self._token = token
        self._timeout = timeout

    def query(self, api_name: str, fields: str = "", **kwargs) -> pd.DataFrame:
        # 该镜像按 body.api_name 路由；SDK 1.4.25 追加 /api_name 会失败。
        response = requests.post(
            "http://tushare.xyz",
            json={"api_name": api_name, "token": self._token,
                  "params": kwargs, "fields": fields},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "code" not in payload:
            raise ValueError("invalid Tushare response envelope")
        if payload["code"] != 0:
            raise RuntimeError(payload.get("msg") or "Tushare source error")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list):
            raise ValueError("invalid Tushare response data")
        return pd.DataFrame(data["items"], columns=data["fields"])

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return partial(self.query, name)
