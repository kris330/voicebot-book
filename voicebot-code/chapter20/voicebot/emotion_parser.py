
import re
import asyncio
import logging
from typing import AsyncIterator
from .emotion import Emotion, get_emotion_config, EmotionConfig

logger = logging.getLogger(__name__)

# 情感标记的正则表达式
EMOTION_TAG_PATTERN = re.compile(r'^\[EMOTION:(\d+)\]\s*')

# 缓冲多少个字符再尝试解析（标记最长约 12 个字符）
BUFFER_SIZE = 20


class EmotionStreamParser:
    """
    从 LLM 流式输出中提取情感标记。

    工作流程：
    1. 先缓冲前 BUFFER_SIZE 个字符
    2. 尝试匹配情感标记
    3. 如果匹配到，提取情感值，剩余文本继续输出
    4. 如果没有匹配到，按默认情感处理，缓冲区内容直接输出
    5. 之后的所有 token 直接透传（不再缓冲）
    """

    def __init__(self, default_emotion: Emotion = Emotion.NEUTRAL) -> None:
        self._default_emotion = default_emotion
        self._detected_emotion: Emotion | None = None
        self._buffer: str = ""
        self._tag_parsed: bool = False

    @property
    def emotion(self) -> Emotion:
        """返回检测到的情感，未检测到则返回默认值"""
        return self._detected_emotion if self._detected_emotion is not None else self._default_emotion

    @property
    def emotion_config(self) -> EmotionConfig:
        return get_emotion_config(self.emotion)

    async def process_stream(
        self, llm_stream: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        """
        处理 LLM 流，返回去掉情感标记后的文本流。
        情感值通过 self.emotion 属性访问。
        """
        async for chunk in llm_stream:
            if self._tag_parsed:
                # 标记已解析完毕，直接透传
                if chunk:
                    yield chunk
                continue

            # 还在缓冲阶段
            self._buffer += chunk

            if len(self._buffer) >= BUFFER_SIZE:
                # 缓冲区足够大，尝试解析
                yield from self._flush_buffer()
                self._tag_parsed = True

        # 流结束，如果缓冲区还有内容
        if not self._tag_parsed:
            yield from self._flush_buffer()
            self._tag_parsed = True

    def _flush_buffer(self) -> list[str]:
        """
        尝试从缓冲区解析情感标记，返回应该输出的文本列表。
        """
        match = EMOTION_TAG_PATTERN.match(self._buffer)

        if match:
            emotion_value = int(match.group(1))
            try:
                self._detected_emotion = Emotion(emotion_value)
                logger.debug(f"Detected emotion: {self._detected_emotion.name}")
            except ValueError:
                logger.warning(
                    f"Unknown emotion value: {emotion_value}, using default"
                )
                self._detected_emotion = self._default_emotion

            # 返回标记之后的内容
            remaining = self._buffer[match.end():]
            return [remaining] if remaining else []
        else:
            # 没有情感标记，用默认情感
            logger.debug("No emotion tag found, using default emotion")
            self._detected_emotion = self._default_emotion
            result = self._buffer
            self._buffer = ""
            return [result] if result else []
