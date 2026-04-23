
import asyncio
import logging
from typing import AsyncIterator, Callable

from .emotion import Emotion, EmotionConfig, get_emotion_config
from .emotion_parser import EmotionStreamParser
from .tts.base import BaseTTSEngine

logger = logging.getLogger(__name__)


def split_sentences(text: str) -> list[str]:
    """
    按中文标点分割句子。
    返回的最后一个元素可能是不完整的句子。
    """
    import re
    # 在句末标点后分割，保留标点
    parts = re.split(r'(?<=[。！？，；])', text)
    return [p for p in parts if p]


class EmotionPipeline:
    """
    带情感控制的 LLM→TTS 流水线。

    数据流：
    LLM 流 → 情感解析 → 句子分割 → TTS 合成 → 音频流
    """

    def __init__(
        self,
        tts_engine: BaseTTSEngine,
        default_emotion: Emotion = Emotion.NEUTRAL,
        min_sentence_length: int = 5,  # 最短句子长度，太短不送 TTS
    ) -> None:
        self._tts = tts_engine
        self._default_emotion = default_emotion
        self._min_sentence_length = min_sentence_length

    async def process(
        self,
        llm_stream: AsyncIterator[str],
        on_emotion_detected: Callable[[Emotion], None] | None = None,
    ) -> AsyncIterator[bytes]:
        """
        处理 LLM 流，返回音频字节流。

        Args:
            llm_stream: LLM 的流式文本输出
            on_emotion_detected: 情感被检测到时的回调（可用于更新 UI）
        """
        parser = EmotionStreamParser(self._default_emotion)
        text_buffer = ""
        emotion_notified = False

        async def generate_audio() -> AsyncIterator[bytes]:
            nonlocal text_buffer, emotion_notified

            async for chunk in parser.process_stream(llm_stream):
                # 情感解析完后立即通知
                if not emotion_notified and parser._tag_parsed:
                    emotion_notified = True
                    if on_emotion_detected:
                        on_emotion_detected(parser.emotion)
                    logger.info(f"Emotion detected: {parser.emotion.name}")

                text_buffer += chunk
                sentences = split_sentences(text_buffer)

                # 保留最后一个（可能未完整）
                complete_sentences = sentences[:-1]
                text_buffer = sentences[-1] if sentences else ""

                for sentence in complete_sentences:
                    if len(sentence.strip()) >= self._min_sentence_length:
                        config = parser.emotion_config
                        logger.debug(
                            f"Synthesizing: '{sentence[:20]}...' "
                            f"emotion={config.emotion.name} "
                            f"speed={config.speed}"
                        )
                        async for audio_chunk in self._tts.synthesize_stream(
                            sentence, config
                        ):
                            yield audio_chunk

            # 处理最后剩余的文本
            if text_buffer.strip():
                if not emotion_notified and on_emotion_detected:
                    on_emotion_detected(parser.emotion)
                config = parser.emotion_config
                async for audio_chunk in self._tts.synthesize_stream(
                    text_buffer, config
                ):
                    yield audio_chunk

        async for audio in generate_audio():
            yield audio
