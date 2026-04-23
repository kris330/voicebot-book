
from abc import ABC, abstractmethod
from typing import AsyncIterator
from ..emotion import EmotionConfig


class BaseTTSEngine(ABC):
    """TTS 引擎的抽象基类"""

    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """
        流式合成文本，返回音频字节块。

        Args:
            text: 要合成的文本（不包含情感标记）
            emotion_config: 情感配置（速度、音色等）

        Yields:
            PCM 音频块
        """
        ...

    async def synthesize_all(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> bytes:
        """合成完整音频（非流式，用于短文本）"""
        chunks = []
        async for chunk in self.synthesize_stream(text, emotion_config):
            chunks.append(chunk)
        return b"".join(chunks)
