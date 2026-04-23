
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 工厂函数类型：接收 dict 配置，返回组件实例
FactoryFn = Callable[[dict], Any]


class ComponentRegistry:
    """
    组件注册表

    把字符串名称映射到工厂函数，
    工厂函数负责根据配置创建组件实例。
    """

    def __init__(self) -> None:
        self._factories: dict[str, FactoryFn] = {}

    def register(self, name: str, factory: FactoryFn) -> None:
        """注册组件工厂函数"""
        self._factories[name] = factory
        logger.debug(f"注册组件：{name}")

    def create(self, name: str, config: dict) -> Any:
        """
        根据名称和配置创建组件实例

        Args:
            name: 组件名称（必须已注册）
            config: 传递给工厂函数的配置字典

        Raises:
            KeyError: 未找到组件名称
        """
        factory = self._factories.get(name)
        if factory is None:
            available = ", ".join(sorted(self._factories.keys()))
            raise KeyError(
                f"未知组件：{name!r}。"
                f"可用组件：{available}"
            )
        logger.debug(f"创建组件：{name}，配置：{config}")
        return factory(config)

    def available_names(self) -> list[str]:
        return sorted(self._factories.keys())


# 全局注册表
_asr_registry = ComponentRegistry()
_llm_registry = ComponentRegistry()
_tts_registry = ComponentRegistry()


def register_asr(name: str) -> Callable:
    """装饰器：注册 ASR 引擎"""
    def decorator(cls):
        _asr_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def register_llm(name: str) -> Callable:
    """装饰器：注册 LLM 引擎"""
    def decorator(cls):
        _llm_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def register_tts(name: str) -> Callable:
    """装饰器：注册 TTS 引擎"""
    def decorator(cls):
        _tts_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def create_asr(name: str, config: dict) -> Any:
    return _asr_registry.create(name, config)


def create_llm(name: str, config: dict) -> Any:
    return _llm_registry.create(name, config)


def create_tts(name: str, config: dict) -> Any:
    return _tts_registry.create(name, config)
