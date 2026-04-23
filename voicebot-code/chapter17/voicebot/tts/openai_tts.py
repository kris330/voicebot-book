
import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from ..config import TTSConfig

logger = logging.getLogger(__name__)


class OpenAITTS:
    """使用 OpenAI TTS API 将文字转换为语音。"""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key)

    async def synthesize_stream(
        self,
        text: str,
    ) -> AsyncGenerator[bytes, None]:
        """
        流式合成语音。

        Args:
            text: 要合成的文字

        Yields:
            音频数据块（PCM 或 MP3，取决于 response_format）
        """
        if not text.strip():
            return

        try:
            # OpenAI TTS API 支持流式响应
            async with self._client.audio.speech.with_streaming_response.create(
                model=self._config.model,
                voice=self._config.voice,
                input=text,
                response_format="pcm",  # 原始 PCM，方便客户端直接播放
                speed=self._config.speed,
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk

        except Exception as e:
            logger.error(f"TTS 合成失败: {e}", exc_info=True)
