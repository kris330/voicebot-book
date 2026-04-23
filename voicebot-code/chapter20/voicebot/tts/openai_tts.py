
import asyncio
from openai import AsyncOpenAI
from ..emotion import Emotion, EmotionConfig

# OpenAI voice 到情感的映射
# alloy: 中性  echo: 低沉  fable: 故事感  onyx: 权威  nova: 温暖  shimmer: 活泼
OPENAI_VOICE_MAP: dict[str, str] = {
    "calm":       "echo",
    "gentle":     "nova",
    "cheerful":   "shimmer",
    "default":    "alloy",
    "serious":    "onyx",
    "excited":    "shimmer",
}

class OpenAITTSWithEmotion:
    def __init__(self, api_key: str, model: str = "tts-1") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    def _get_voice(self, config: EmotionConfig) -> str:
        return OPENAI_VOICE_MAP.get(config.voice_style, "alloy")

    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """流式合成，边合成边返回音频块"""
        voice = self._get_voice(emotion_config)
        # OpenAI TTS 不直接支持语速，通过 speed 参数（1.0 = 正常）
        speed = emotion_config.speed

        async with self._client.audio.speech.with_streaming_response.create(
            model=self._model,
            voice=voice,
            input=text,
            speed=speed,
            response_format="pcm",
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                yield chunk
