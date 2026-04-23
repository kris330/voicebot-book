
import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from ..config import LLMConfig

logger = logging.getLogger(__name__)


class OpenAILLM:
    """使用 OpenAI Chat API 生成回复（流式输出）。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key)

    async def generate_stream(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        """
        流式生成回复。

        Args:
            messages: OpenAI 格式的对话历史

        Yields:
            文字片段（token by token）
        """
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error(f"LLM 生成失败: {e}", exc_info=True)
            yield "抱歉，我现在有点问题，请稍后再试。"
