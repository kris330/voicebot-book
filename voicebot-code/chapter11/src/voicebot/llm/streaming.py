
import asyncio
import logging
import re
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

logger = logging.getLogger(__name__)

# 句子边界的正则表达式
# 匹配中文句末标点和英文句末标点
SENTENCE_BOUNDARY = re.compile(r'[。！？!?\.…]+')


async def stream_llm_response(
    client: AsyncOpenAI,
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> AsyncGenerator[str, None]:
    """
    流式调用 LLM，按句子边界拆分输出

    不是逐 token 输出，而是等到句子结束再 yield，
    这样 TTS 可以拿到完整的句子来合成，语调更自然。

    Yields:
        str: 每次 yield 一个完整的句子（或半句，如果句子很长）
    """
    model = model or getattr(client, "_default_model", "gpt-4o-mini")
    buffer = ""

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in stream:
            delta = _extract_delta(chunk)
            if delta is None:
                continue

            buffer += delta

            # 检查 buffer 里是否有完整的句子
            while True:
                match = SENTENCE_BOUNDARY.search(buffer)
                if not match:
                    break

                # 找到句子边界，提取这个句子
                end_pos = match.end()
                sentence = buffer[:end_pos].strip()
                buffer = buffer[end_pos:]

                if sentence:
                    logger.debug(f"[LLM] 输出句子: {sentence!r}")
                    yield sentence

        # 处理 buffer 里剩余的内容（最后一段可能没有标点）
        if buffer.strip():
            yield buffer.strip()

    except asyncio.CancelledError:
        logger.info("[LLM] 流式输出被取消")
        raise
    except Exception as e:
        logger.error(f"[LLM] 流式调用失败: {e}", exc_info=True)
        raise


def _extract_delta(chunk: ChatCompletionChunk) -> Optional[str]:
    """从 chunk 中提取文本增量"""
    if not chunk.choices:
        return None
    delta = chunk.choices[0].delta
    if delta.content is None:
        return None
    return delta.content
