
import asyncio
import logging
from typing import Optional

import numpy as np

from voicebot.vad.vad_manager import VADManager, VADConfig, SpeechSegment
from voicebot.asr.asr_manager import ASRManager

logger = logging.getLogger(__name__)


class VoicePipeline:
    """
    语音处理管道：VAD → ASR → LLM → TTS
    这一章只实现 VAD → ASR 部分
    """

    def __init__(self):
        self._vad = VADManager(VADConfig(
            silence_threshold_ms=600,
            min_speech_ms=200,
            max_speech_ms=30000,
            speech_threshold=0.5,
        ))
        self._asr = ASRManager()
        self._asr_task: Optional[asyncio.Task] = None
        self._on_transcript = None  # 识别结果回调
        self._on_speech_start = None

    async def init(self):
        await self._vad.init()
        await self._asr.init()
        logger.info("VoicePipeline 初始化完成")

    def on_transcript(self, callback):
        """注册识别结果回调"""
        self._on_transcript = callback
        return self

    def on_speech_start(self, callback):
        """注册语音开始回调（可以用于打断 AI 说话）"""
        self._on_speech_start = callback
        return self

    async def process_audio(self, audio_chunk: bytes):
        """
        处理一块原始音频数据（从 WebSocket 收到的）

        Args:
            audio_chunk: 原始 int16 音频字节
        """
        chunk = np.frombuffer(audio_chunk, dtype=np.int16)
        segment = await self._vad.process_chunk(chunk)

        if segment is not None:
            # VAD 检测到一段完整语音，异步发给 ASR
            asyncio.create_task(self._run_asr(segment))

    async def _run_asr(self, segment: SpeechSegment):
        """在独立任务中运行 ASR，不阻塞 VAD"""
        try:
            logger.info(f"送入 ASR，时长={segment.duration_ms:.0f}ms")

            result = await self._asr.transcribe(segment.audio)

            if result and self._on_transcript:
                await self._on_transcript(result)
                logger.info(f"ASR 结果: {result!r}")
        except Exception as e:
            logger.error(f"ASR 处理失败: {e}", exc_info=True)

    def reset(self):
        """重置状态（新会话开始时调用）"""
        self._vad.reset()
