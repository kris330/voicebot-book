
from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class TTSEngine(Protocol):
    """TTS 引擎统一接口"""

    async def synthesize(self, text: str) -> bytes:
        """
        批量合成

        Args:
            text: 要合成的文字（已经过预处理）

        Returns:
            PCM 音频数据（16-bit 有符号整数）
        """
        ...

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        流式合成

        Yields:
            PCM 音频块（16-bit 有符号整数）
        """
        ...

    def get_sample_rate(self) -> int:
        """返回输出采样率（Hz）"""
        ...
