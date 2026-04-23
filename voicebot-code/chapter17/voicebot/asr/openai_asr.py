
import logging
import tempfile
import os

from openai import AsyncOpenAI

from ..config import ASRConfig

logger = logging.getLogger(__name__)


class OpenAIASR:
    """使用 OpenAI Whisper API 进行语音识别。"""

    def __init__(self, config: ASRConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key)

    async def transcribe(self, audio_data: bytes) -> str:
        """
        将 PCM 音频数据转换为文字。

        Args:
            audio_data: 原始 PCM 音频数据（16kHz, 16bit, mono）

        Returns:
            识别出的文字，如果识别失败返回空字符串
        """
        if not audio_data:
            return ""

        # Whisper API 需要文件格式（wav/mp3 等），不接受原始 PCM
        # 我们把 PCM 包装成 WAV 格式
        wav_data = _pcm_to_wav(audio_data, sample_rate=16000)

        try:
            # 使用临时文件（Whisper API 需要文件对象）
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_data)
                tmp_path = tmp.name

            with open(tmp_path, "rb") as audio_file:
                response = await self._client.audio.transcriptions.create(
                    model=self._config.model,
                    file=audio_file,
                    language=self._config.language,
                )

            text = response.text.strip()
            logger.info(f"ASR 识别结果: '{text}'")
            return text

        except Exception as e:
            logger.error(f"ASR 识别失败: {e}", exc_info=True)
            return ""
        finally:
            if "tmp_path" in locals():
                os.unlink(tmp_path)


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000) -> bytes:
    """
    将原始 PCM 数据包装成 WAV 格式。
    WAV = 44字节头 + PCM 数据。
    """
    import struct

    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_data)
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,              # PCM format chunk size
        1,               # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data
