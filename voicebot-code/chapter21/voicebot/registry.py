
from typing import Any, TypeVar
import logging

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry:
    """
    模型注册表：把字符串名称映射到具体的类。

    用法：
        registry = Registry("ASR")
        registry.register("openai_whisper", OpenAIWhisperASR)
        asr_class = registry.get("openai_whisper")
        asr = asr_class(**config)
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._classes: dict[str, type] = {}

    def register(self, name: str, cls: type) -> None:
        if name in self._classes:
            logger.warning(
                f"[{self._name}] Overriding existing registration for '{name}'"
            )
        self._classes[name] = cls
        logger.debug(f"[{self._name}] Registered '{name}' -> {cls.__name__}")

    def get(self, name: str) -> type:
        if name not in self._classes:
            available = list(self._classes.keys())
            raise ValueError(
                f"[{self._name}] Unknown engine '{name}'. "
                f"Available: {available}"
            )
        return self._classes[name]

    def list_all(self) -> list[str]:
        return list(self._classes.keys())


# 全局注册表
asr_registry = Registry("ASR")
llm_registry = Registry("LLM")
tts_registry = Registry("TTS")
