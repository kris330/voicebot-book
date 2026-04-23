
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class VADState(Enum):
    SILENCE = auto()   # 静音状态
    SPEECH = auto()    # 语音状态


@dataclass
class VADConfig:
    """VAD 配置参数"""
    # 模型推理参数
    speech_threshold: float = 0.5       # 语音概率阈值
    sample_rate: int = 16000            # 采样率（必须是 16kHz）
    chunk_ms: int = 60                  # 每帧时长（FSMN-VAD 推荐 60ms）

    # 状态转换参数
    silence_threshold_ms: int = 600     # 静音多久判定为说完
    min_speech_ms: int = 200            # 最短有效语音时长
    max_speech_ms: int = 30000          # 最长语音段

    # 预滚动：保留语音开始前的音频
    pre_roll_ms: int = 100

    # 触发语音开始需要连续多少帧检测为语音
    speech_trigger_frames: int = 2

    @property
    def chunk_samples(self) -> int:
        return int(self.sample_rate * self.chunk_ms / 1000)

    @property
    def silence_threshold_frames(self) -> int:
        return self.silence_threshold_ms // self.chunk_ms

    @property
    def min_speech_frames(self) -> int:
        return self.min_speech_ms // self.chunk_ms

    @property
    def pre_roll_frames(self) -> int:
        return self.pre_roll_ms // self.chunk_ms


@dataclass
class SpeechSegment:
    """一段完整的语音"""
    audio: np.ndarray          # 音频数据（int16）
    start_ms: float            # 相对于会话开始的时间戳（毫秒）
    end_ms: float
    duration_ms: float = field(init=False)

    def __post_init__(self):
        self.duration_ms = self.end_ms - self.start_ms


class VADManager:
    """
    服务端流式 VAD 管理器

    使用方式：
        vad = VADManager()
        await vad.init()

        async for segment in vad.process_stream(audio_generator):
            # segment 是一段完整的语音，可以送给 ASR
            asr_result = await asr.transcribe(segment.audio)
    """

    def __init__(self, config: Optional[VADConfig] = None):
        self._config = config or VADConfig()
        self._model = None
        self._state = VADState.SILENCE
        self._speech_buffer: list[np.ndarray] = []    # 当前语音段的音频帧
        self._pre_roll_buffer: deque = deque(maxlen=self._config.pre_roll_frames)
        self._silence_frames = 0          # 连续静音帧计数
        self._speech_frames = 0           # 连续语音帧计数
        self._total_speech_frames = 0     # 本次语音段总帧数
        self._session_start_time = time.monotonic()
        self._model_cache = {}            # FSMN-VAD 需要维护帧间状态

    async def init(self):
        """加载 FSMN-VAD 模型（异步，避免阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info("VADManager 初始化完成")

    def _load_model(self):
        """在线程池中加载模型"""
        from funasr import AutoModel
        self._model = AutoModel(
            model="fsmn-vad",
            model_revision="v2.0.4",
            disable_log=True,
        )
        logger.info("FSMN-VAD 模型加载完成")

    def reset(self):
        """重置 VAD 状态（新会话开始时调用）"""
        self._state = VADState.SILENCE
        self._speech_buffer.clear()
        self._pre_roll_buffer.clear()
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_speech_frames = 0
        self._session_start_time = time.monotonic()
        self._model_cache = {}
        logger.debug("VAD 状态已重置")

    def _get_timestamp_ms(self) -> float:
        """获取当前相对时间戳（毫秒）"""
        return (time.monotonic() - self._session_start_time) * 1000

    def _run_inference(self, audio_chunk: np.ndarray) -> float:
        """
        对一帧音频运行 FSMN-VAD 推理

        Returns:
            语音概率 [0.0, 1.0]
        """
        # FSMN-VAD 接受 float32，范围 [-1, 1]
        if audio_chunk.dtype == np.int16:
            audio_float = audio_chunk.astype(np.float32) / 32768.0
        else:
            audio_float = audio_chunk.astype(np.float32)

        result = self._model.generate(
            input=audio_float,
            cache=self._model_cache,           # 传入缓存，维护帧间状态
            is_final=False,                    # 流式模式
            chunk_size=self._config.chunk_ms,  # 告诉模型每帧时长
        )

        # 解析模型输出
        # 流式模式下，result 是 VAD 段的列表
        # 如果当前帧是语音，列表中会有未结束的段（end=-1）
        if result and result[0].get('value'):
            segments = result[0]['value']
            for seg in segments:
                if seg[1] == -1:  # end=-1 表示语音还在继续
                    return 1.0    # 当前帧是语音
            # 有已结束的段，当前帧可能是静音
            return 0.0
        return 0.0

    async def process_chunk(self, audio_chunk: np.ndarray) -> Optional[SpeechSegment]:
        """
        处理一帧音频

        Args:
            audio_chunk: int16 音频数据，长度应为 chunk_samples

        Returns:
            如果一段语音结束，返回 SpeechSegment；否则返回 None
        """
        cfg = self._config
        loop = asyncio.get_event_loop()

        # 在线程池中运行推理（避免阻塞事件循环）
        speech_prob = await loop.run_in_executor(
            None, self._run_inference, audio_chunk
        )

        is_speech = speech_prob >= cfg.speech_threshold

        if self._state == VADState.SILENCE:
            # 静音状态：维护预滚动缓冲区
            self._pre_roll_buffer.append(audio_chunk.copy())

            if is_speech:
                self._speech_frames += 1
                if self._speech_frames >= cfg.speech_trigger_frames:
                    # 连续多帧检测为语音，进入 SPEECH 状态
                    self._state = VADState.SPEECH
                    self._silence_frames = 0
                    self._total_speech_frames = self._speech_frames

                    # 把预滚动的帧加入语音缓冲
                    self._speech_buffer = list(self._pre_roll_buffer)
                    self._speech_buffer.append(audio_chunk.copy())

                    speech_start_ms = self._get_timestamp_ms()
                    logger.debug(f"语音开始 @ {speech_start_ms:.0f}ms，概率={speech_prob:.2f}")
            else:
                self._speech_frames = 0

        elif self._state == VADState.SPEECH:
            # 语音状态：收集音频
            self._speech_buffer.append(audio_chunk.copy())
            self._total_speech_frames += 1

            if is_speech:
                self._silence_frames = 0
            else:
                self._silence_frames += 1

            # 判断是否结束
            speech_ended = False
            reason = ""

            if self._silence_frames >= cfg.silence_threshold_frames:
                speech_ended = True
                reason = f"静音超过 {cfg.silence_threshold_ms}ms"
            elif self._total_speech_frames * cfg.chunk_ms >= cfg.max_speech_ms:
                speech_ended = True
                reason = f"超过最大时长 {cfg.max_speech_ms}ms"

            if speech_ended:
                # 检查最短语音时长
                total_ms = self._total_speech_frames * cfg.chunk_ms
                if total_ms >= cfg.min_speech_ms:
                    # 拼接所有音频帧
                    audio_data = np.concatenate(self._speech_buffer)
                    end_ms = self._get_timestamp_ms()
                    start_ms = end_ms - total_ms

                    segment = SpeechSegment(
                        audio=audio_data,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                    logger.info(
                        f"语音结束，时长={total_ms:.0f}ms，原因={reason}，"
                        f"样本数={len(audio_data)}"
                    )
                else:
                    logger.debug(f"语音段过短（{total_ms}ms < {cfg.min_speech_ms}ms），丢弃")
                    segment = None

                # 重置状态
                self._state = VADState.SILENCE
                self._speech_buffer.clear()
                self._pre_roll_buffer.clear()
                self._silence_frames = 0
                self._speech_frames = 0
                self._total_speech_frames = 0

                return segment

        return None

    async def process_stream(self, audio_generator):
        """
        处理音频流的异步生成器

        Args:
            audio_generator: 异步生成器，每次 yield int16 音频帧

        Yields:
            SpeechSegment: 每段完整的语音
        """
        cfg = self._config

        async for raw_chunk in audio_generator:
            # 确保音频块是 numpy array
            if not isinstance(raw_chunk, np.ndarray):
                chunk = np.frombuffer(raw_chunk, dtype=np.int16)
            else:
                chunk = raw_chunk

            # 如果音频块大小不一致，按 chunk_samples 分割处理
            for i in range(0, len(chunk), cfg.chunk_samples):
                frame = chunk[i:i + cfg.chunk_samples]

                # 最后一帧可能不足，用零填充
                if len(frame) < cfg.chunk_samples:
                    frame = np.pad(frame, (0, cfg.chunk_samples - len(frame)))

                segment = await self.process_chunk(frame)
                if segment is not None:
                    yield segment
