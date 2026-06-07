"""Raindrop Workshop 链路追踪的极薄包装（best-effort，绝不影响主流程）。

设计要点：
- 仅当运行时装了 raindrop-ai（Python ≥3.10，见仓库根 ``.venv-raindrop``）才真正埋点；
  生产 3.9.6 运行时 ``import raindrop.analytics`` 失败 → 全程 no-op，被埋点的命令
  行为与未埋点完全一致。
- 任何 raindrop 调用异常都被吞掉并降级，采集 / 生成 / 推送主流程永远继续。
- 本机 Workshop 通过环境变量 ``RAINDROP_LOCAL_DEBUGGER``（或自动探测 ``localhost:5899``）
  镜像上报，无需 ``RAINDROP_WRITE_KEY``。注意：SDK 用 ``requests`` POST 到 localhost，
  会受 ``ALL_PROXY`` 影响，调用方运行时需置 ``no_proxy=localhost,127.0.0.1``。
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

try:  # 3.9 prod / 未装 raindrop-ai → 全程降级为 no-op
    import raindrop.analytics as _raindrop
except Exception:  # noqa: BLE001 - 任何 import 失败都降级，不得影响主流程
    _raindrop = None

_initialized = False


class _NoopInteraction:
    """raindrop 不可用时的占位 interaction，finish 为空操作。"""

    def finish(self, *, output: Optional[str] = None, **_extra: Any) -> None:
        pass


class _NoopSpan:
    """raindrop 不可用时的占位 span，record_* 均为空操作。"""

    def record_input(self, data: Any) -> None:
        pass

    def record_output(self, data: Any) -> None:
        pass

    def set_properties(self, props: Dict[str, Any]) -> None:
        pass


class _SafeSpan:
    """包装真实 SDK span，确保 record_* 异常不会影响业务流程。"""

    def __init__(self, span: Any):
        self._span = span

    def record_input(self, data: Any) -> None:
        try:
            self._span.record_input(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("raindrop record_input 跳过：%s", exc)

    def record_output(self, data: Any) -> None:
        try:
            self._span.record_output(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("raindrop record_output 跳过：%s", exc)

    def set_properties(self, props: Dict[str, Any]) -> None:
        try:
            self._span.set_properties(props)
        except Exception as exc:  # noqa: BLE001
            logger.debug("raindrop set_properties 跳过：%s", exc)


def _ensure_init() -> bool:
    """惰性初始化 raindrop（仅一次）。返回 raindrop 是否可用。"""
    global _initialized
    if _raindrop is None:
        return False
    if _initialized:
        return True
    try:
        # 本机 Workshop 模式：tracing_enabled=True + endpoint 指向 Workshop，
        # 才能让 tool_span 等子 span 进入本机 Workshop（OTEL 导出路径）。
        # api_key 给占位值即可（本机 daemon 不校验云端凭据）；auto_instrument=False
        # 关掉 requests/urllib3 等自动埋点，避免 SDK 自身 POST 产生噪声 span。
        local_url = os.getenv("RAINDROP_LOCAL_DEBUGGER") or "http://localhost:5899/v1/"
        _raindrop.init(
            api_key=os.getenv("RAINDROP_WRITE_KEY") or "local-workshop",
            tracing_enabled=True,
            auto_instrument=False,
            endpoint=local_url,
            local_workshop_url=local_url,
        )
        _initialized = True
    except Exception as exc:  # noqa: BLE001 - 初始化失败不应中断主流程
        logger.debug("raindrop init 跳过：%s", exc)
        return False
    return True


def begin(
    event: str,
    *,
    user_id: str,
    input: str,
    properties: Optional[Dict[str, Any]] = None,
) -> Any:
    """开启一次 interaction；raindrop 不可用时返回 no-op 占位对象。"""
    if not _ensure_init():
        return _NoopInteraction()
    try:
        return _raindrop.begin(
            user_id=user_id,
            event=event,
            input=input,
            properties=properties or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("raindrop begin 跳过：%s", exc)
        return _NoopInteraction()


def finish(
    interaction: Any,
    *,
    output: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """结束 interaction 并 flush（短生命周期 CLI 进程退出前确保上报）。"""
    try:
        if error:
            try:
                interaction.finish(output=f"Error: {error}", error=error)
            except TypeError:
                interaction.finish(output=f"Error: {error}")
        else:
            interaction.finish(output=output)
    except Exception as exc:  # noqa: BLE001
        logger.debug("raindrop finish 跳过：%s", exc)
    if _raindrop is not None and _initialized:
        try:
            _raindrop.flush()
        except Exception:  # noqa: BLE001 - flush 失败不影响主流程
            pass


@contextmanager
def tool_span(name: str) -> Iterator[Any]:
    """工具 / 模型调用边界的 span（best-effort）。

    raindrop 不可用、或进入 span 失败时 yield no-op span；**绝不吞掉被包裹代码块
    自身抛出的异常**（关闭 span 时传递异常信息，仍不 suppress）。
    """
    if not _ensure_init():
        yield _NoopSpan()
        return
    cm = None
    span: Any = _NoopSpan()
    try:
        cm = _raindrop.tool_span(name)
        span = cm.__enter__()
    except Exception as exc:  # noqa: BLE001 - 进入 span 失败则降级为 no-op
        logger.debug("raindrop tool_span(%s) 进入跳过：%s", name, exc)
        cm = None
        span = _NoopSpan()
    try:
        yield _SafeSpan(span)
    finally:
        if cm is not None:
            try:
                cm.__exit__(*sys.exc_info())
            except Exception:  # noqa: BLE001 - 关闭 span 失败不影响主流程
                pass
