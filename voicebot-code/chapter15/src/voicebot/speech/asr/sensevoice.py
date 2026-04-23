
from voicebot.registry import register_asr


@register_asr("sensevoice")
class SenseVoiceASR:
    """SenseVoice 本地 ASR 引擎"""

    def __init__(
        self,
        model_path: str = "models/asr",
        device: str = "cpu",
    ) -> None:
        self.model_path = model_path
        self.device = device
        # 实际加载模型...

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        # 实际识别逻辑...
        return ""

    async def transcribe_stream(self, audio_stream, sample_rate=16000):
        async for chunk in audio_stream:
            # 实际流式识别...
            yield ("", False)


# src/voicebot/llm/openai_llm.py

from voicebot.registry import register_llm


@register_llm("openai")
class OpenAILLM:
    """OpenAI LLM 引擎"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        temperature: float = 0.7,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    async def generate_stream(self, messages, system_prompt=""):
        # 实际调用 OpenAI API...
        yield ""

    async def generate(self, messages, system_prompt="") -> str:
        return ""


# src/voicebot/tts/kokoro_local.py（注册版本）

from voicebot.registry import register_tts
from voicebot.tts.kokoro_local import KokoroLocalTTS as _KokoroLocalTTS


@register_tts("kokoro")
class KokoroLocalTTS(_KokoroLocalTTS):
    """Kokoro 本地 TTS（已注册到组件注册表）"""
    pass


@register_tts("cosyvoice_api")
class CosyVoiceAPITTS:
    """CosyVoice API TTS（已注册到组件注册表）"""

    def __init__(
        self,
        api_url: str,
        voice: str = "longxiaochun",
        sample_rate: int = 22050,
    ) -> None:
        self.api_url = api_url
        self.voice = voice
        self._sample_rate = sample_rate

    def get_sample_rate(self) -> int:
        return self._sample_rate

    async def synthesize_stream(self, text: str):
        # 实际调用 CosyVoice API...
        yield b""

    async def synthesize(self, text: str) -> bytes:
        return b""
