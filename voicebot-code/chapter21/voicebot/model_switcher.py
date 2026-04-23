
import asyncio
import logging
from typing import Any
from .config import EngineConfig
from .factory import create_tts_engine, create_llm_engine
from .engines.tts.base import BaseTTSEngine
from .engines.llm.base import BaseLLMEngine

logger = logging.getLogger(__name__)


class ModelSwitcher:
    """
    支持运行时切换 TTS / LLM 引擎。

    使用 asyncio.Lock 保证切换过程的原子性：
    - 新请求等待切换完成
    - 进行中的请求继续使用旧引擎直到完成
    """

    def __init__(
        self,
        initial_tts: BaseTTSEngine,
        initial_llm: BaseLLMEngine,
    ) -> None:
        self._tts = initial_tts
        self._llm = initial_llm
        self._lock = asyncio.Lock()

    @property
    def tts(self) -> BaseTTSEngine:
        return self._tts

    @property
    def llm(self) -> BaseLLMEngine:
        return self._llm

    async def switch_tts(self, new_config: EngineConfig) -> None:
        """切换 TTS 引擎（会等待 lock，不中断进行中的请求）"""
        async with self._lock:
            logger.info(f"Switching TTS to: {new_config.engine}")
            old_engine = self._tts
            self._tts = create_tts_engine(new_config)
            logger.info(f"TTS switched successfully")
            # 如果旧引擎有清理方法，调用它
            if hasattr(old_engine, "close"):
                await old_engine.close()

    async def switch_llm(self, new_config: EngineConfig) -> None:
        """切换 LLM 引擎"""
        async with self._lock:
            logger.info(f"Switching LLM to: {new_config.engine}")
            old_engine = self._llm
            self._llm = create_llm_engine(new_config)
            logger.info(f"LLM switched successfully")
            if hasattr(old_engine, "close"):
                await old_engine.close()
