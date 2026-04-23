
from dataclasses import dataclass, field
from enum import Enum
import time
import json


class ClientMessageType(str, Enum):
    AUDIO_CHUNK = "audio_chunk"
    CONFIG = "config"
    PING = "ping"
    INTERRUPT = "interrupt"


class ServerMessageType(str, Enum):
    ASR_RESULT = "asr_result"
    LLM_TOKEN = "llm_token"
    TTS_AUDIO = "tts_audio"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    PONG = "pong"
    ERROR = "error"
    SESSION_READY = "session_ready"


@dataclass
class ClientMessage:
    """客户端发来的 JSON 消息"""
    type: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_json(cls, raw: str) -> "ClientMessage":
        obj = json.loads(raw)
        return cls(
            type=obj.get("type", ""),
            data=obj.get("data", {}),
            timestamp=obj.get("timestamp", time.time()),
        )


@dataclass
class ServerMessage:
    """服务器发给客户端的 JSON 消息"""
    type: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }, ensure_ascii=False)
