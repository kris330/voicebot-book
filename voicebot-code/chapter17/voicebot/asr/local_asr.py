
from faster_whisper import WhisperModel


class LocalASR:
    def __init__(self, model_size: str = "small") -> None:
        # 第一次运行会自动下载模型
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    async def transcribe(self, audio_data: bytes) -> str:
        import asyncio
        import io
        import numpy as np

        # 转换为 numpy float32
        pcm = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

        # 在线程池里运行（避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        segments, _ = await loop.run_in_executor(
            None,
            lambda: self._model.transcribe(pcm, language="zh")
        )

        text = "".join(seg.text for seg in segments).strip()
        return text
