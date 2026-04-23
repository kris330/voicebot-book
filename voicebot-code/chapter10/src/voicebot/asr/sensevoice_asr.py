
import asyncio
import io
import logging
import time
import wave
from typing import AsyncGenerator, Optional

import numpy as np

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)

# SenseVoice 情感标签映射
EMOTION_MAP = {
    "NEUTRAL": "中性",
    "HAPPY": "开心",
    "SAD": "悲伤",
    "ANGRY": "愤怒",
    "FEARFUL": "恐惧",
    "DISGUSTED": "厌恶",
    "SURPRISED": "惊讶",
}


class SenseVoiceASR(BaseASR):
    """
    SenseVoice 本地语音识别

    基于 FunASR 框架，支持中英日韩等多语言
    模型下载地址：https://www.modelscope.cn/models/iic/SenseVoiceSmall
    """

    def __init__(
        self,
        model_name: str = "iic/SenseVoiceSmall",
        device: str = "cpu",
        batch_size: int = 1,
    ):
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model = None

    @property
    def name(self) -> str:
        return f"SenseVoice({self._model_name})"

    async def init(self) -> None:
        """在线程池中加载模型（避免阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info(f"[{self.name}] 模型加载完成，设备={self._device}")

    def _load_model(self):
        from funasr import AutoModel

        self._model = AutoModel(
            model=self._model_name,
            device=self._device,
            disable_update=True,   # 不自动更新模型（生产环境推荐）
            disable_log=True,
        )
        logger.info(f"[{self.name}] FunASR 模型加载完成")

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """把 numpy 数组转换为 WAV 格式字节流（FunASR 接受 WAV 输入）"""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)       # int16 = 2 bytes
            f.setframerate(16000)
            f.writeframes(audio.astype(np.int16).tobytes())
        return buffer.getvalue()

    def _run_inference(self, audio: np.ndarray) -> dict:
        """运行模型推理（同步，在线程池中调用）"""
        start_time = time.monotonic()

        # SenseVoice 接受 numpy float32 数组
        if audio.dtype == np.int16:
            audio_float = audio.astype(np.float32) / 32768.0
        else:
            audio_float = audio.astype(np.float32)

        result = self._model.generate(
            input=audio_float,
            cache={},
            language="auto",                      # 自动检测语言
            use_itn=True,                          # 逆文本归一化（数字写法）
            batch_size_s=60,                       # 批处理时长（秒）
        )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.debug(f"[{self.name}] 推理耗时: {elapsed_ms:.1f}ms")

        return result[0] if result else {}

    def _parse_result(self, raw: dict) -> ASRResult:
        """解析 FunASR 的原始输出"""
        text = raw.get("text", "").strip()

        # SenseVoice 输出格式示例：
        # "<|zh|><|NEUTRAL|><|Speech|><|woitn|>今天天气怎么样"
        # 需要去掉特殊标签

        import re
        # 提取情感标签
        emotion_match = re.search(r"<\|([A-Z]+)\|>", text)
        emotion = emotion_match.group(1) if emotion_match else "NEUTRAL"

        # 去掉所有 <|...|> 标签
        clean_text = re.sub(r"<\|[^|]+\|>", "", text).strip()

        return ASRResult(
            text=clean_text,
            is_final=True,
            confidence=1.0,
        )

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """识别一段完整音频"""
        if isinstance(audio, bytes):
            audio_array = np.frombuffer(audio, dtype=np.int16)
        else:
            audio_array = audio

        if len(audio_array) == 0:
            return ASRResult(text="", is_final=True)

        loop = asyncio.get_event_loop()
        raw_result = await loop.run_in_executor(None, self._run_inference, audio_array)

        return self._parse_result(raw_result)

    async def transcribe_stream(self, audio_generator: AsyncGenerator):
        """
        SenseVoice 不原生支持流式，这里用非流式模拟：
        等待 VAD 切出完整语音段后，统一送入识别
        """
        raise NotImplementedError(
            "SenseVoice 不支持流式识别，请使用 transcribe() 处理 VAD 切出的语音段"
        )

    async def close(self) -> None:
        self._model = None
        logger.info(f"[{self.name}] 已释放资源")
