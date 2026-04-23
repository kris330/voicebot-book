
from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class ASREngine(Protocol):
    """ASR 引擎接口"""

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[tuple[str, bool]]:
        """
        流式识别

        Yields:
            (text, is_final) 元组
            - text: 识别出的文字（中间结果或最终结果）
            - is_final: 是否是最终结果
        """
        ...

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        批量识别（一次性处理完整音频）

        Returns:
            识别出的文字
        """
        ...


@runtime_checkable
class LLMEngine(Protocol):
    """LLM 引擎接口"""

    async def generate_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        """
        流式生成

        Args:
            messages: 对话历史，格式 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词

        Yields:
            生成的 token（字符串片段）
        """
        ...

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> str:
        """
        批量生成（等待完整回复）

        Returns:
            完整的生成文字
        """
        ...


@runtime_checkable
class TTSEngine(Protocol):
    """TTS 引擎接口"""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        流式合成

        Yields:
            PCM 音频块（16-bit 有符号整数）
        """
        ...

    async def synthesize(self, text: str) -> bytes:
        """
        批量合成

        Returns:
            完整 PCM 音频数据
        """
        ...

    def get_sample_rate(self) -> int:
        """返回输出采样率（Hz）"""
        ...


@runtime_checkable
class TextRewriter(Protocol):
    """文本改写器接口（可选组件）"""

    async def rewrite(self, text: str) -> str:
        """
        对 LLM 输出进行后处理

        常见用途：去除特定内容、格式规范化
        """
        ...
