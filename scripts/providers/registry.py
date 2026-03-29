"""
数据源注册中心
负责管理所有 provider，按优先级自动降级
"""
from __future__ import annotations

import logging
from .base import DataProvider, DataResult

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    数据源注册中心。

    - 注册多个 provider，按优先级排序
    - 调用数据方法时，自动尝试优先级最高的 provider
    - 如果失败，自动降级到下一个 provider
    - 记录每次调用的来源，保证数据溯源
    """

    def __init__(self):
        self._providers: list[DataProvider] = []

    def register(self, provider: DataProvider) -> None:
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)
        logger.info(f"注册数据源: {provider.name} (优先级: {provider.priority})")

    def get_provider(self, name: str) -> DataProvider | None:
        for p in self._providers:
            if p.name == name:
                return p
        return None

    @property
    def providers(self) -> list[DataProvider]:
        return self._providers

    def initialize_all(self) -> dict[str, bool]:
        """初始化所有 provider，返回各自状态"""
        results = {}
        for p in self._providers:
            try:
                ok = p.initialize()
                results[p.name] = ok
                if ok:
                    logger.info(f"  ✓ {p.name} 初始化成功")
                else:
                    logger.warning(f"  ✗ {p.name} 初始化失败")
            except Exception as e:
                results[p.name] = False
                logger.error(f"  ✗ {p.name} 初始化异常: {e}")
        return results

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        """
        按优先级调用 provider 的方法，自动降级。

        示例: registry.call("get_limit_up_list", "20260329")
        """
        errors = []
        for provider in self._providers:
            if not provider.supports(method_name):
                continue
            method = getattr(provider, method_name, None)
            if method is None:
                continue
            try:
                result = method(*args, **kwargs)
                if result.success:
                    logger.debug(f"{method_name} 成功 (来源: {provider.name})")
                    return result
                else:
                    errors.append(f"{provider.name}: {result.error}")
                    logger.warning(f"{method_name} 失败 ({provider.name}): {result.error}，尝试下一个")
            except Exception as e:
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"{method_name} 异常 ({provider.name}): {e}，尝试下一个")

        return DataResult(
            data=None,
            source="registry",
            error=f"所有数据源均失败: {'; '.join(errors)}"
        )

    def call_specific(self, provider_name: str, method_name: str, *args, **kwargs) -> DataResult:
        """指定数据源调用，不降级"""
        provider = self.get_provider(provider_name)
        if provider is None:
            return DataResult(data=None, source=provider_name, error=f"未找到数据源: {provider_name}")
        method = getattr(provider, method_name, None)
        if method is None:
            return DataResult(data=None, source=provider_name, error=f"{provider_name} 不支持 {method_name}")
        return method(*args, **kwargs)
