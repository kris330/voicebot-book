
import asyncio
import io
import logging
import os
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class KokoroLocalTTS:
    """
    Kokoro 本地 TTS 引擎

    模型参数：82M，支持 CPU 推理
    支持语言：中文、英文、日文等
    输出采样率：24000 Hz
    """

    OUTPUT_SAMPLE_RATE = 24000  # Kokoro 固定输出 24kHz

    def __init__(
        self,
        model_path: str | None = None,
        voice: str = "zf_xiaobei",
        speed: float = 1.0,
    ) -> None:
        """
        Args:
            model_path: ONNX 模型文件路径，None 则自动下载
            voice: 声音名称，中文推荐 zf_xiaobei / zm_yunyang
            speed: 语速，1.0 为正常速度
        """
        self.model_path = model_path
        self.voice = voice
        self.speed = speed
        self._kokoro = None

    def _load_model(self) -> None:
        """加载模型（懒加载）"""
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise ImportError(
                "请安装 kokoro-onnx: pip install kokoro-onnx"
            )

        if self.model_path and os.path.exists(self.model_path):
            model_file = self.model_path
        else:
            # 自动下载模型
            logger.info("未找到本地模型，尝试自动下载 Kokoro 模型...")
            model_file = None  # Kokoro 库会自动处理下载

        self._kokoro = Kokoro(model_file, lang="zh")
        logger.info(f"Kokoro TTS 模型已加载，声音：{self.voice}")

    def _ensure_loaded(self) -> None:
        """确保模型已加载"""
        if self._kokoro is None:
            self._load_model()

    def synthesize_sync(self, text: str) -> bytes:
        """
        同步合成（在线程池中调用）

        Returns:
            PCM 音频数据（16-bit，24kHz，单声道）
        """
        self._ensure_loaded()

        samples, sample_rate = self._kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang="zh",
        )

        # 转换为 16-bit PCM bytes
        audio_int16 = (samples * 32767).astype(np.int16)
        return audio_int16.tobytes()

    async def synthesize(self, text: str) -> bytes:
        """
        异步合成：在线程池中运行同步推理
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.synthesize_sync, text)

    async def synthesize_stream(
        self,
        text: str,
        chunk_size_ms: int = 200,
    ) -> AsyncIterator[bytes]:
        """
        流式合成：先完整合成，再按块输出

        Kokoro 本身不支持真正的流式输出，但我们可以把完整音频
        切成小块逐步 yield，配合播放缓冲实现"伪流式"效果。

        Args:
            text: 要合成的文字
            chunk_size_ms: 每块音频的时长（毫秒）
        """
        audio_bytes = await self.synthesize(text)

        # 计算每块的字节数
        # 16-bit PCM = 2 bytes/sample, 24000 samples/sec
        bytes_per_ms = self.OUTPUT_SAMPLE_RATE * 2 // 1000
        chunk_bytes = chunk_size_ms * bytes_per_ms

        for i in range(0, len(audio_bytes), chunk_bytes):
            chunk = audio_bytes[i : i + chunk_bytes]
            if chunk:
                yield chunk
                # 让出控制权，避免阻塞事件循环
                await asyncio.sleep(0)

    def get_sample_rate(self) -> int:
        """返回输出采样率"""
        return self.OUTPUT_SAMPLE_RATE
