
from __future__ import annotations
import json
import os
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class EngineConfig:
    """某个引擎（ASR/LLM/TTS）的配置"""
    engine: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMConfig:
    engine: str
    config: dict[str, Any] = field(default_factory=dict)
    system_prompt_file: str = "prompts/default_system.txt"


@dataclass(frozen=True)
class EmotionConfig:
    enabled: bool = True
    default_emotion: int = 3
    buffer_size: int = 20


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size_ms: int = 100


@dataclass(frozen=True)
class VoiceBotConfig:
    """VoiceBot 完整配置"""
    server: ServerConfig
    asr: EngineConfig
    llm: LLMConfig
    tts: EngineConfig
    emotion: EmotionConfig
    audio: AudioConfig
    version: str = "1.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceBotConfig:
        """从字典创建配置对象"""
        return cls(
            version=data.get("version", "1.0"),
            server=ServerConfig(**data.get("server", {})),
            asr=EngineConfig(**data.get("asr", {"engine": "openai_whisper"})),
            llm=LLMConfig(**data.get("llm", {"engine": "openai_chat"})),
            tts=EngineConfig(**data.get("tts", {"engine": "openai_tts"})),
            emotion=EmotionConfig(**data.get("emotion", {})),
            audio=AudioConfig(**data.get("audio", {})),
        )

    @classmethod
    def from_file(cls, path: str) -> VoiceBotConfig:
        """从 JSON 文件加载配置"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded config from: {path}")
        return cls.from_dict(data)
