
from dataclasses import dataclass, field
from enum import Enum
import time


class EventType(str, Enum):
    """VoiceBot 所有事件类型"""

    # ASR 相关
    AUDIO_CHUNK_RECEIVED = "audio_chunk_received"    # 收到原始音频块
    ASR_PARTIAL_RESULT = "asr_partial_result"         # ASR 中间结果（实时识别）
    ASR_FINAL_RESULT = "asr_final_result"             # ASR 最终结果（一句话识别完毕）

    # LLM 相关
    LLM_START = "llm_start"                          # LLM 开始生成
    LLM_TOKEN = "llm_token"                          # LLM 生成一个 token
    LLM_SENTENCE_READY = "llm_sentence_ready"         # LLM 生成了一个完整句子（用于 TTS）
    LLM_END = "llm_end"                              # LLM 生成结束

    # TTS 相关
    TTS_SYNTHESIS_START = "tts_synthesis_start"       # TTS 开始合成
    TTS_AUDIO_CHUNK = "tts_audio_chunk"               # TTS 生成一个音频块
    TTS_SYNTHESIS_END = "tts_synthesis_end"           # TTS 合成结束

    # 控制相关
    INTERRUPT = "interrupt"                           # 用户打断
    SESSION_END = "session_end"                       # 会话结束


@dataclass
class BaseEvent:
    """所有事件的基类"""
    event_type: str
    session_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AudioChunkEvent(BaseEvent):
    """收到原始音频块"""
    audio_bytes: bytes = b""
    sample_rate: int = 16000

    def __post_init__(self) -> None:
        self.event_type = EventType.AUDIO_CHUNK_RECEIVED


@dataclass
class ASRResultEvent(BaseEvent):
    """ASR 识别结果"""
    text: str = ""
    is_final: bool = False
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.event_type = (
            EventType.ASR_FINAL_RESULT
            if self.is_final
            else EventType.ASR_PARTIAL_RESULT
        )


@dataclass
class LLMTokenEvent(BaseEvent):
    """LLM 生成的单个 token"""
    token: str = ""
    accumulated_text: str = ""  # 到目前为止累积的文本

    def __post_init__(self) -> None:
        self.event_type = EventType.LLM_TOKEN


@dataclass
class LLMSentenceEvent(BaseEvent):
    """LLM 生成的完整句子（用于触发 TTS）"""
    sentence: str = ""
    sequence_number: int = 0  # 句子序号，用于保证 TTS 顺序

    def __post_init__(self) -> None:
        self.event_type = EventType.LLM_SENTENCE_READY


@dataclass
class LLMEndEvent(BaseEvent):
    """LLM 生成结束"""
    full_response: str = ""

    def __post_init__(self) -> None:
        self.event_type = EventType.LLM_END


@dataclass
class TTSAudioChunkEvent(BaseEvent):
    """TTS 生成的音频块"""
    audio_bytes: bytes = b""
    sample_rate: int = 16000
    sequence_number: int = 0  # 对应哪个句子的音频

    def __post_init__(self) -> None:
        self.event_type = EventType.TTS_AUDIO_CHUNK


@dataclass
class InterruptEvent(BaseEvent):
    """用户打断事件"""
    reason: str = "user_interrupt"

    def __post_init__(self) -> None:
        self.event_type = EventType.INTERRUPT


@dataclass
class SessionEndEvent(BaseEvent):
    """会话结束事件"""
    reason: str = "normal"

    def __post_init__(self) -> None:
        self.event_type = EventType.SESSION_END
