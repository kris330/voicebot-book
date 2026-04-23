
import asyncio
import logging
from typing import AsyncIterator

from .base import TTSEngine
from .text_processor import TextPreprocessor, SentenceSplitter

logger = logging.getLogger(__name__)


class TTSManager:
    """
    TTS 管理器

    负责：
    1. 文本预处理
    2. 句子切分
    3. 调用底层 TTS 引擎
    4. 流式输出音频块
    """

    def __init__(
        self,
        engine: TTSEngine,
        target_sample_rate: int = 16000,
    ) -> None:
        self.engine = engine
        self.target_sample_rate = target_sample_rate
        self._preprocessor = TextPreprocessor()
        self._splitter = SentenceSplitter()

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """
        把文字转成音频流

        完整流程：预处理 → 切分 → 合成 → 采样率转换
        """
        # 1. 预处理
        clean_text = self._preprocessor.process(text)
        if not clean_text.strip():
            logger.debug("预处理后文字为空，跳过合成")
            return

        # 2. 切分成句子
        sentences = self._splitter.split(clean_text)
        logger.debug(f"切分为 {len(sentences)} 个句子")

        # 3. 逐句合成
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            logger.debug(f"合成句子：{sentence[:20]}...")

            async for chunk in self.engine.synthesize_stream(sentence):
                # 4. 采样率转换（如果需要）
                converted = self._convert_sample_rate(
                    chunk,
                    self.engine.get_sample_rate(),
                    self.target_sample_rate,
                )
                yield converted

    def _convert_sample_rate(
        self,
        pcm_bytes: bytes,
        src_rate: int,
        dst_rate: int,
    ) -> bytes:
        """简单的采样率转换"""
        if src_rate == dst_rate:
            return pcm_bytes

        try:
            import numpy as np
            from scipy import signal

            audio = np.frombuffer(pcm_bytes, dtype=np.int16)
            # 计算重采样后的长度
            new_length = int(len(audio) * dst_rate / src_rate)
            resampled = signal.resample(audio, new_length)
            return resampled.astype(np.int16).tobytes()
        except ImportError:
            # 没有 scipy 就直接返回原始数据，让上层处理
            logger.warning("scipy 未安装，跳过采样率转换")
            return pcm_bytes
