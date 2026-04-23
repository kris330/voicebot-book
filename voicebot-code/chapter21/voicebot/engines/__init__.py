# 在这里统一注册所有引擎，保证注册表在启动时已填满

from ..registry import asr_registry, llm_registry, tts_registry

# ── ASR 引擎 ──────────────────────────────────────────
from .asr.openai_whisper import OpenAIWhisperASR
from .asr.sensevoice_local import SenseVoiceLocalASR

asr_registry.register("openai_whisper",   OpenAIWhisperASR)
asr_registry.register("sensevoice_local", SenseVoiceLocalASR)

# ── LLM 引擎 ──────────────────────────────────────────
from .llm.openai_chat import OpenAIChatLLM
from .llm.ollama import OllamaLLM

llm_registry.register("openai_chat", OpenAIChatLLM)
llm_registry.register("ollama",      OllamaLLM)

# ── TTS 引擎 ──────────────────────────────────────────
from .tts.openai_tts import OpenAITTSEngine
from .tts.cosyvoice_local import CosyVoiceLocalTTS
from .tts.cosyvoice_grpc import CosyVoiceGRPCTTS

tts_registry.register("openai_tts",      OpenAITTSEngine)
tts_registry.register("cosyvoice_local", CosyVoiceLocalTTS)
tts_registry.register("cosyvoice_grpc",  CosyVoiceGRPCTTS)
