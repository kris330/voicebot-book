# 第十五章：Pipeline 设计——把所有组件组合起来

---

经过前面几章，VoiceBot 的各个组件都已经就位：

- ASR：把音频转成文字
- LLM：根据文字生成回复
- TTS：把文字转成音频
- WebSocket 网关：处理客户端通信
- 事件总线：解耦各模块

但现在你面临一个新问题：**怎么把这些组件组合成一个可以正常工作的系统？**

一种方式是在启动代码里写死：

```python
# 这样写能跑，但很脆弱
asr = SenseVoiceASR(model_path="/models/asr")
llm = OpenAILLM(api_key="sk-xxx", model="gpt-4")
tts = KokoroLocalTTS(voice="zf_xiaobei")
bus = EventBus()
# ...手动把它们串起来...
```

这有几个问题：
1. 换一个 ASR 引擎要改代码
2. 不同的配置（开发环境/生产环境）需要不同的实例化代码
3. 每个用户的会话可能需要不同的配置（比如不同的 TTS 声音）
4. 无法从配置文件驱动

本章我们来设计 **Pipeline**——一个把所有组件组合成可配置处理链的框架。

---

## 15.1 Pipeline 的核心思想

Pipeline 的设计目标：

```
配置文件 (JSON/YAML)
        │
        ▼
┌───────────────────┐
│  Pipeline Factory  │  ← 根据配置创建所有组件实例
└────────┬──────────┘
         │
         ▼
┌───────────────────────────────────────┐
│              Pipeline                  │
│                                       │
│  ┌──────┐  ┌──────┐  ┌──────┐        │
│  │ ASR  │  │ LLM  │  │ TTS  │        │
│  │Engine│  │Engine│  │Engine│        │
│  └──────┘  └──────┘  └──────┘        │
│                                       │
│  ┌──────────────┐  ┌───────────────┐  │
│  │  EventBus    │  │ SessionManager│  │
│  └──────────────┘  └───────────────┘  │
└───────────────────────────────────────┘
         │
         │ clone() 为每个用户会话创建独立实例
         ▼
┌─────────────────┐  ┌─────────────────┐
│  SessionPipeline│  │  SessionPipeline│
│  (用户 A)       │  │  (用户 B)       │
└─────────────────┘  └─────────────────┘
```

关键设计决策：
- **共享组件**（ASR 引擎、LLM 引擎、TTS 引擎）：模型加载一次，所有会话共享
- **独立状态**（对话历史、当前状态）：每个会话独立
- **配置驱动**：从 JSON 创建 Pipeline，不用改代码换引擎

---

## 15.2 抽象接口设计

Protocol（协议类）定义了每个组件必须实现的接口。上层代码只依赖 Protocol，不依赖具体实现。

```python
# src/voicebot/protocols.py

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class ASREngine(Protocol):
    """ASR 引擎接口"""

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        sample_rate: int = 16000,
    ) -> AsyncIterator[tuple[str, bool]]:
        """
        流式识别

        Yields:
            (text, is_final) 元组
            - text: 识别出的文字（中间结果或最终结果）
            - is_final: 是否是最终结果
        """
        ...

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        批量识别（一次性处理完整音频）

        Returns:
            识别出的文字
        """
        ...


@runtime_checkable
class LLMEngine(Protocol):
    """LLM 引擎接口"""

    async def generate_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> AsyncIterator[str]:
        """
        流式生成

        Args:
            messages: 对话历史，格式 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词

        Yields:
            生成的 token（字符串片段）
        """
        ...

    async def generate(
        self,
        messages: list[dict],
        system_prompt: str = "",
    ) -> str:
        """
        批量生成（等待完整回复）

        Returns:
            完整的生成文字
        """
        ...


@runtime_checkable
class TTSEngine(Protocol):
    """TTS 引擎接口"""

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        流式合成

        Yields:
            PCM 音频块（16-bit 有符号整数）
        """
        ...

    async def synthesize(self, text: str) -> bytes:
        """
        批量合成

        Returns:
            完整 PCM 音频数据
        """
        ...

    def get_sample_rate(self) -> int:
        """返回输出采样率（Hz）"""
        ...


@runtime_checkable
class TextRewriter(Protocol):
    """文本改写器接口（可选组件）"""

    async def rewrite(self, text: str) -> str:
        """
        对 LLM 输出进行后处理

        常见用途：去除特定内容、格式规范化
        """
        ...
```

---

## 15.3 模型注册机制

模型注册机制让我们可以用字符串名称来创建模型实例，配置文件只需要写引擎名称和参数：

```python
# src/voicebot/registry.py

import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 工厂函数类型：接收 dict 配置，返回组件实例
FactoryFn = Callable[[dict], Any]


class ComponentRegistry:
    """
    组件注册表

    把字符串名称映射到工厂函数，
    工厂函数负责根据配置创建组件实例。
    """

    def __init__(self) -> None:
        self._factories: dict[str, FactoryFn] = {}

    def register(self, name: str, factory: FactoryFn) -> None:
        """注册组件工厂函数"""
        self._factories[name] = factory
        logger.debug(f"注册组件：{name}")

    def create(self, name: str, config: dict) -> Any:
        """
        根据名称和配置创建组件实例

        Args:
            name: 组件名称（必须已注册）
            config: 传递给工厂函数的配置字典

        Raises:
            KeyError: 未找到组件名称
        """
        factory = self._factories.get(name)
        if factory is None:
            available = ", ".join(sorted(self._factories.keys()))
            raise KeyError(
                f"未知组件：{name!r}。"
                f"可用组件：{available}"
            )
        logger.debug(f"创建组件：{name}，配置：{config}")
        return factory(config)

    def available_names(self) -> list[str]:
        return sorted(self._factories.keys())


# 全局注册表
_asr_registry = ComponentRegistry()
_llm_registry = ComponentRegistry()
_tts_registry = ComponentRegistry()


def register_asr(name: str) -> Callable:
    """装饰器：注册 ASR 引擎"""
    def decorator(cls):
        _asr_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def register_llm(name: str) -> Callable:
    """装饰器：注册 LLM 引擎"""
    def decorator(cls):
        _llm_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def register_tts(name: str) -> Callable:
    """装饰器：注册 TTS 引擎"""
    def decorator(cls):
        _tts_registry.register(name, lambda cfg: cls(**cfg))
        return cls
    return decorator


def create_asr(name: str, config: dict) -> Any:
    return _asr_registry.create(name, config)


def create_llm(name: str, config: dict) -> Any:
    return _llm_registry.create(name, config)


def create_tts(name: str, config: dict) -> Any:
    return _tts_registry.create(name, config)
```

### 15.3.1 用装饰器注册组件

```python
# src/voicebot/speech/asr/sensevoice.py

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
```

---

## 15.4 Pipeline 类

Pipeline 持有所有共享组件实例：

```python
# src/voicebot/pipeline.py

import logging
from dataclasses import dataclass, field
from typing import Any

from .protocols import ASREngine, LLMEngine, TTSEngine, TextRewriter

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Pipeline 的完整配置"""

    # ASR 配置
    asr_engine: str = "sensevoice"
    asr_config: dict = field(default_factory=dict)

    # LLM 配置
    llm_engine: str = "openai"
    llm_config: dict = field(default_factory=dict)
    system_prompt: str = "你是一个友好的语音助手，回答要简洁。"

    # TTS 配置
    tts_engine: str = "kokoro"
    tts_config: dict = field(default_factory=dict)
    target_sample_rate: int = 16000

    # 可选组件
    rewriter_engine: str | None = None
    rewriter_config: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineConfig":
        """从字典创建配置"""
        return cls(
            asr_engine=data.get("asr", {}).get("engine", "sensevoice"),
            asr_config=data.get("asr", {}).get("config", {}),
            llm_engine=data.get("llm", {}).get("engine", "openai"),
            llm_config=data.get("llm", {}).get("config", {}),
            system_prompt=data.get("llm", {}).get(
                "system_prompt",
                "你是一个友好的语音助手，回答要简洁。",
            ),
            tts_engine=data.get("tts", {}).get("engine", "kokoro"),
            tts_config=data.get("tts", {}).get("config", {}),
            target_sample_rate=data.get("tts", {}).get("target_sample_rate", 16000),
            rewriter_engine=data.get("rewriter", {}).get("engine"),
            rewriter_config=data.get("rewriter", {}).get("config", {}),
        )


class Pipeline:
    """
    VoiceBot 处理管道

    持有所有共享的模型和组件实例。
    每个用户会话通过 clone() 获得独立的 SessionPipeline。
    """

    def __init__(
        self,
        config: PipelineConfig,
        asr: ASREngine,
        llm: LLMEngine,
        tts: TTSEngine,
        rewriter: TextRewriter | None = None,
    ) -> None:
        self.config = config
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.rewriter = rewriter
        logger.info(
            f"Pipeline 创建完成："
            f"ASR={config.asr_engine}，"
            f"LLM={config.llm_engine}，"
            f"TTS={config.tts_engine}"
        )

    def clone(self, session_id: str) -> "SessionPipeline":
        """
        为新会话创建 SessionPipeline

        注意：引擎实例是共享的，只有会话状态是独立的。
        """
        return SessionPipeline(
            pipeline=self,
            session_id=session_id,
        )
```

---

## 15.5 会话隔离：SessionPipeline

`SessionPipeline` 是每个用户会话独有的处理单元，持有该会话的状态，但共享底层引擎：

```python
# src/voicebot/pipeline.py（续）

import asyncio
from dataclasses import dataclass, field


@dataclass
class ConversationHistory:
    """对话历史"""
    messages: list[dict] = field(default_factory=list)
    max_turns: int = 20  # 最多保留的对话轮数

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self._trim()

    def get(self) -> list[dict]:
        return list(self.messages)

    def clear(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        """保持对话历史在限制范围内"""
        max_messages = self.max_turns * 2  # 每轮 2 条消息
        if len(self.messages) > max_messages:
            # 保留最新的消息
            self.messages = self.messages[-max_messages:]


class SessionPipeline:
    """
    会话级别的处理管道

    每个用户会话独有一个 SessionPipeline 实例。
    共享底层引擎（ASR/LLM/TTS），但有独立的：
    - 对话历史
    - 当前状态
    - 配置覆盖（比如用户选择的 TTS 声音）
    """

    def __init__(
        self,
        pipeline: "Pipeline",
        session_id: str,
    ) -> None:
        self._pipeline = pipeline
        self.session_id = session_id
        self.history = ConversationHistory()
        self._is_speaking = False  # TTS 是否正在播放
        self._interrupt_flag = asyncio.Event()

    @property
    def asr(self) -> ASREngine:
        return self._pipeline.asr

    @property
    def llm(self) -> LLMEngine:
        return self._pipeline.llm

    @property
    def tts(self) -> TTSEngine:
        return self._pipeline.tts

    @property
    def system_prompt(self) -> str:
        return self._pipeline.config.system_prompt

    def interrupt(self) -> None:
        """打断当前 TTS 播放"""
        self._interrupt_flag.set()

    def reset_interrupt(self) -> None:
        """重置打断标志"""
        self._interrupt_flag.clear()

    @property
    def is_interrupted(self) -> bool:
        return self._interrupt_flag.is_set()

    async def process_user_input(
        self,
        user_text: str,
    ) -> "AsyncIterator[bytes]":
        """
        处理用户输入，返回 TTS 音频流

        完整流程：
        1. 记录用户输入到历史
        2. 调用 LLM 生成回复
        3. （可选）文本改写
        4. 流式 TTS 合成
        5. 记录回复到历史
        """
        self.reset_interrupt()
        self.history.add_user(user_text)

        # 收集完整回复（用于保存到历史）
        full_response = ""

        async def audio_generator():
            nonlocal full_response

            sentence_buffer = ""
            from voicebot.tts.text_processor import SentenceSplitter, TextPreprocessor
            splitter = SentenceSplitter()
            preprocessor = TextPreprocessor()

            async for token in self._pipeline.llm.generate_stream(
                messages=self.history.get(),
                system_prompt=self.system_prompt,
            ):
                if self.is_interrupted:
                    break

                full_response += token
                sentence_buffer += token

                # 检查是否有完整句子可以合成
                sentences = splitter.split(sentence_buffer)
                if len(sentences) > 1:
                    for sentence in sentences[:-1]:
                        if sentence.strip() and not self.is_interrupted:
                            clean = preprocessor.process(sentence)
                            async for chunk in self._pipeline.tts.synthesize_stream(clean):
                                if self.is_interrupted:
                                    return
                                yield chunk
                    sentence_buffer = sentences[-1]

            # 处理最后剩余的文字
            if sentence_buffer.strip() and not self.is_interrupted:
                clean = preprocessor.process(sentence_buffer)
                async for chunk in self._pipeline.tts.synthesize_stream(clean):
                    if self.is_interrupted:
                        return
                    yield chunk

            # 保存完整回复到历史
            if full_response:
                self.history.add_assistant(full_response)

        return audio_generator()
```

---

## 15.6 Pipeline 工厂

工厂函数负责从配置创建完整的 Pipeline 实例：

```python
# src/voicebot/pipeline_factory.py

import json
import logging
from pathlib import Path

from .pipeline import Pipeline, PipelineConfig
from .registry import create_asr, create_llm, create_tts

logger = logging.getLogger(__name__)


class PipelineFactory:
    """
    Pipeline 工厂

    负责从配置（dict 或 JSON 文件）创建 Pipeline 实例。
    """

    @classmethod
    def from_config(cls, config: PipelineConfig) -> Pipeline:
        """
        从 PipelineConfig 对象创建 Pipeline

        这是创建 Pipeline 的核心方法。
        """
        logger.info("开始创建 Pipeline...")

        # 创建 ASR 引擎
        logger.info(f"加载 ASR 引擎：{config.asr_engine}")
        asr = create_asr(config.asr_engine, config.asr_config)

        # 创建 LLM 引擎
        logger.info(f"加载 LLM 引擎：{config.llm_engine}")
        llm = create_llm(config.llm_engine, config.llm_config)

        # 创建 TTS 引擎
        logger.info(f"加载 TTS 引擎：{config.tts_engine}")
        tts = create_tts(config.tts_engine, config.tts_config)

        # 可选：创建文本改写器
        rewriter = None
        if config.rewriter_engine:
            from .registry import _tts_registry  # 复用注册表概念
            logger.info(f"加载改写器：{config.rewriter_engine}")

        logger.info("Pipeline 创建完成")
        return Pipeline(
            config=config,
            asr=asr,
            llm=llm,
            tts=tts,
            rewriter=rewriter,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Pipeline:
        """从字典创建 Pipeline"""
        config = PipelineConfig.from_dict(data)
        return cls.from_config(config)

    @classmethod
    def from_json_file(cls, path: str | Path) -> Pipeline:
        """从 JSON 文件创建 Pipeline"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在：{path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        logger.info(f"从配置文件创建 Pipeline：{path}")
        return cls.from_dict(data)
```

---

## 15.7 配置文件格式

有了工厂函数，Pipeline 的创建完全由 JSON 配置驱动：

```json
{
  "asr": {
    "engine": "sensevoice",
    "config": {
      "model_path": "models/sensevoice",
      "device": "cpu"
    }
  },
  "llm": {
    "engine": "openai",
    "config": {
      "api_key": "${OPENAI_API_KEY}",
      "model": "gpt-4o-mini",
      "temperature": 0.7
    },
    "system_prompt": "你是一个专业的语音助手，回答要简洁、自然，像正常说话一样，不要用 Markdown 格式。"
  },
  "tts": {
    "engine": "kokoro",
    "config": {
      "voice": "zf_xiaobei",
      "speed": 1.0
    },
    "target_sample_rate": 16000
  }
}
```

**切换到本地全离线方案**，只需要换一份配置：

```json
{
  "asr": {
    "engine": "sensevoice",
    "config": {
      "model_path": "models/sensevoice",
      "device": "cpu"
    }
  },
  "llm": {
    "engine": "ollama",
    "config": {
      "model": "qwen2.5:7b",
      "base_url": "http://localhost:11434"
    },
    "system_prompt": "你是一个本地部署的语音助手。"
  },
  "tts": {
    "engine": "kokoro",
    "config": {
      "voice": "zf_xiaobei",
      "speed": 1.0
    },
    "target_sample_rate": 16000
  }
}
```

代码一行不改，只换配置文件！

### 15.7.1 环境变量插值

配置文件中的 `"${OPENAI_API_KEY}"` 需要运行时替换：

```python
# src/voicebot/config_loader.py

import json
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _interpolate_env_vars(obj: object) -> object:
    """
    递归替换配置中的环境变量占位符

    "${VAR_NAME}" → os.environ["VAR_NAME"]
    """
    if isinstance(obj, str):
        pattern = r"\$\{([^}]+)\}"
        matches = re.findall(pattern, obj)
        for var_name in matches:
            value = os.environ.get(var_name)
            if value is None:
                logger.warning(f"环境变量未设置：{var_name}")
                value = ""
            obj = obj.replace(f"${{{var_name}}}", value)
        return obj
    elif isinstance(obj, dict):
        return {k: _interpolate_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_interpolate_env_vars(item) for item in obj]
    return obj


def load_config(path: str | Path) -> dict:
    """
    加载配置文件，自动替换环境变量

    Args:
        path: JSON 配置文件路径

    Returns:
        处理后的配置字典
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    return _interpolate_env_vars(raw)
```

---

## 15.8 完整集成：从配置到运行

把所有组件连接起来，创建一个完整可运行的 VoiceBot：

```python
# src/voicebot/app.py

import asyncio
import logging
from fastapi import FastAPI

from .config_loader import load_config
from .pipeline_factory import PipelineFactory
from .gateway.gateway import VoiceBotGateway
from .gateway.session_binding import SessionManager
from .event_bus import EventBus
from .gateway.messages import ServerMessage, ServerMessageType
from .events import (
    AudioChunkEvent,
    ASRResultEvent,
    TTSAudioChunkEvent,
    EventType,
)

logger = logging.getLogger(__name__)


def create_voicebot_app(config_path: str) -> FastAPI:
    """
    从配置文件创建完整的 VoiceBot FastAPI 应用

    Args:
        config_path: JSON 配置文件路径

    Returns:
        可直接运行的 FastAPI 应用
    """
    # 1. 加载配置
    config_data = load_config(config_path)

    # 2. 创建 Pipeline（加载模型）
    logger.info("加载模型...")
    pipeline = PipelineFactory.from_dict(config_data)

    # 3. 创建核心组件
    gateway = VoiceBotGateway()
    session_manager = SessionManager()

    # 4. 创建 FastAPI 应用
    app = FastAPI(title="VoiceBot")

    # 5. 挂载网关
    gateway.attach_to_app(app, path="/ws")

    # 6. 注册连接事件处理：新连接时创建 Session
    original_handle = gateway._handle_connection

    async def handle_connection_with_session(websocket):
        """扩展连接处理，绑定 Session"""
        # 实际项目中这里会在连接建立后立即绑定 session
        # 为简洁起见这里省略详细实现
        await original_handle(websocket)

    # 7. 设置音频数据处理流程
    _setup_audio_pipeline(gateway, session_manager, pipeline)

    return app


def _setup_audio_pipeline(gateway, session_manager, pipeline):
    """
    设置音频处理流水线

    连接网关 → ASR → LLM → TTS → 网关 的完整数据流
    """
    # 覆盖网关的音频处理方法
    original_handle_audio = gateway._handle_audio_data

    async def handle_audio_with_pipeline(conn, audio_bytes):
        """处理音频：ASR → LLM → TTS"""
        session = session_manager.get_session_by_connection(conn.connection_id)
        if session is None:
            return

        session_pipeline = pipeline.clone(session.session_id)

        # ASR 识别
        text = await pipeline.asr.transcribe(audio_bytes)
        if not text.strip():
            return

        # 发送 ASR 结果给客户端
        await gateway.send_message(
            conn.connection_id,
            ServerMessage(
                type=ServerMessageType.ASR_RESULT,
                data={"text": text, "is_final": True},
            ),
        )

        # LLM + TTS 流式处理
        await gateway.send_message(
            conn.connection_id,
            ServerMessage(type=ServerMessageType.TTS_START, data={}),
        )

        async for audio_chunk in await session_pipeline.process_user_input(text):
            await gateway.send_audio(conn.connection_id, audio_chunk)

        await gateway.send_message(
            conn.connection_id,
            ServerMessage(type=ServerMessageType.TTS_END, data={}),
        )

    gateway._handle_audio_data = handle_audio_with_pipeline
```

### 15.8.1 启动脚本

```python
# main.py

import logging
import uvicorn
from voicebot.app import create_voicebot_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = create_voicebot_app("config.json")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
```

启动：

```bash
python main.py
# 或者
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 15.9 Pipeline 的测试

Pipeline 设计的好处之一是容易测试——用 Mock 替换真实引擎：

```python
# tests/test_pipeline.py

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from voicebot.pipeline import Pipeline, PipelineConfig, SessionPipeline


@pytest.fixture
def mock_asr():
    """模拟 ASR 引擎"""
    asr = MagicMock()
    asr.transcribe = AsyncMock(return_value="你好，VoiceBot")
    return asr


@pytest.fixture
def mock_llm():
    """模拟 LLM 引擎（流式）"""
    llm = MagicMock()

    async def mock_stream(messages, system_prompt=""):
        for token in ["你好", "！", "很高兴", "认识你", "。"]:
            yield token

    llm.generate_stream = mock_stream
    return llm


@pytest.fixture
def mock_tts():
    """模拟 TTS 引擎（流式）"""
    tts = MagicMock()

    async def mock_synth_stream(text):
        # 返回一些假的音频数据
        yield bytes(1024)
        yield bytes(512)

    tts.synthesize_stream = mock_synth_stream
    tts.get_sample_rate = MagicMock(return_value=16000)
    return tts


@pytest.fixture
def pipeline(mock_asr, mock_llm, mock_tts):
    config = PipelineConfig(
        asr_engine="mock",
        llm_engine="mock",
        tts_engine="mock",
        system_prompt="你是测试助手",
    )
    return Pipeline(
        config=config,
        asr=mock_asr,
        llm=mock_llm,
        tts=mock_tts,
    )


@pytest.mark.asyncio
async def test_session_pipeline_clone(pipeline):
    """测试 clone 创建独立会话"""
    session_a = pipeline.clone("session-a")
    session_b = pipeline.clone("session-b")

    # 不同会话
    assert session_a.session_id != session_b.session_id

    # 但共享同一个引擎
    assert session_a.asr is session_b.asr
    assert session_a.llm is session_b.llm
    assert session_a.tts is session_b.tts


@pytest.mark.asyncio
async def test_conversation_history_isolation(pipeline):
    """测试会话历史隔离"""
    session_a = pipeline.clone("session-a")
    session_b = pipeline.clone("session-b")

    session_a.history.add_user("A 的消息")

    # B 的历史不受影响
    assert len(session_b.history.get()) == 0
    assert len(session_a.history.get()) == 1


@pytest.mark.asyncio
async def test_process_user_input(pipeline):
    """测试完整的用户输入处理流程"""
    session = pipeline.clone("test-session")

    audio_chunks = []
    audio_gen = await session.process_user_input("你好")
    async for chunk in audio_gen:
        audio_chunks.append(chunk)

    # 应该收到了音频数据
    assert len(audio_chunks) > 0

    # 对话历史应该被记录
    history = session.history.get()
    assert len(history) >= 2  # 用户消息 + 助手回复
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "你好"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_interrupt(pipeline):
    """测试打断机制"""
    session = pipeline.clone("test-session")

    chunks_before_interrupt = []
    audio_gen = await session.process_user_input("你好")

    count = 0
    async for chunk in audio_gen:
        chunks_before_interrupt.append(chunk)
        count += 1
        if count == 1:
            session.interrupt()  # 收到第一个音频块后打断
            break

    # 打断后停止
    assert session.is_interrupted


@pytest.mark.asyncio
async def test_history_max_turns(pipeline):
    """测试对话历史长度限制"""
    session = pipeline.clone("test-session")
    session.history.max_turns = 3  # 只保留 3 轮

    for i in range(5):
        session.history.add_user(f"用户消息 {i}")
        session.history.add_assistant(f"助手回复 {i}")

    history = session.history.get()
    # 应该被截断到最近 3 轮（6 条消息）
    assert len(history) <= 6


def test_pipeline_config_from_dict():
    """测试从字典创建配置"""
    data = {
        "asr": {"engine": "sensevoice", "config": {"device": "cpu"}},
        "llm": {
            "engine": "openai",
            "config": {"api_key": "test-key", "model": "gpt-4o-mini"},
            "system_prompt": "测试系统提示",
        },
        "tts": {
            "engine": "kokoro",
            "config": {"voice": "zf_xiaobei"},
            "target_sample_rate": 16000,
        },
    }

    config = PipelineConfig.from_dict(data)
    assert config.asr_engine == "sensevoice"
    assert config.llm_engine == "openai"
    assert config.tts_engine == "kokoro"
    assert config.system_prompt == "测试系统提示"
    assert config.target_sample_rate == 16000
```

运行测试：

```bash
pytest tests/test_pipeline.py -v

# 输出：
# tests/test_pipeline.py::test_session_pipeline_clone PASSED
# tests/test_pipeline.py::test_conversation_history_isolation PASSED
# tests/test_pipeline.py::test_process_user_input PASSED
# tests/test_pipeline.py::test_interrupt PASSED
# tests/test_pipeline.py::test_history_max_turns PASSED
# tests/test_pipeline.py::test_pipeline_config_from_dict PASSED
```

---

## 15.10 整体架构回顾

现在 VoiceBot 的完整架构是这样的：

```
                     ┌─────────────────────────────────────┐
                     │            config.json               │
                     └──────────────────┬──────────────────┘
                                        │ PipelineFactory
                                        ▼
┌───────────────────────────────────────────────────────────┐
│                        Pipeline                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  ASREngine   │  │  LLMEngine   │  │  TTSEngine   │    │
│  │（共享）      │  │（共享）      │  │（共享）      │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
└──────────────────────────┬────────────────────────────────┘
                           │ clone(session_id)
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ Session A  │  │ Session B  │  │ Session C  │
    │ Pipeline   │  │ Pipeline   │  │ Pipeline   │
    │            │  │            │  │            │
    │ history_a  │  │ history_b  │  │ history_c  │
    └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
          │               │               │
          ▼               ▼               ▼
    ┌────────────────────────────────────────────┐
    │              WebSocket 网关                 │
    │  ┌──────────┐   ┌──────────┐   ┌──────────┐│
    │  │连接管理器│   │消息路由器│   │心跳检测  ││
    │  └──────────┘   └──────────┘   └──────────┘│
    └────────────────────────────────────────────┘
          │               │               │
          ▼               ▼               ▼
       浏览器 A        浏览器 B        浏览器 C
```

核心设计原则：
1. **模型共享**：ASR/LLM/TTS 引擎加载一次，所有会话共享，节省内存
2. **状态隔离**：每个会话有独立的对话历史和状态，互不干扰
3. **配置驱动**：换引擎只需要改配置文件，不改代码
4. **接口统一**：通过 Protocol 定义统一接口，引擎可以任意替换
5. **事件解耦**：模块间通过事件总线通信，不直接依赖

---

## 本章小结

本章我们完成了 VoiceBot 的"骨架"——Pipeline 系统：

- **设计目标**：把所有组件组合成可配置的处理链，让系统更灵活、更易维护。
- **Protocol 接口**：用 Python Protocol 定义 ASR、LLM、TTS 的统一接口，上层代码不依赖具体实现。
- **组件注册机制**：通过 `@register_asr`、`@register_llm`、`@register_tts` 装饰器，用字符串名称映射到工厂函数。
- **Pipeline 类**：持有共享的引擎实例，通过 `clone()` 为每个会话创建独立的 `SessionPipeline`。
- **SessionPipeline**：会话级处理单元，持有对话历史，支持打断，处理 LLM 生成和 TTS 合成的完整流程。
- **PipelineFactory**：从 JSON 配置文件创建完整的 Pipeline，一行代码换引擎。
- **环境变量插值**：配置文件中的 `${ENV_VAR}` 自动替换为环境变量值，保护 API 密钥。
- **完整测试**：通过 Mock 引擎独立测试 Pipeline 逻辑，不依赖真实模型。

至此，VoiceBot 的核心架构已经完整：

| 章节 | 组件 | 作用 |
|------|------|------|
| 第12章 | TTS | 文字转语音 |
| 第13章 | WebSocket 网关 | 客户端通信 |
| 第14章 | 事件总线 | 模块解耦 |
| 第15章 | Pipeline | 组件组合与配置驱动 |

**下一章**我们将把所有组件真正跑起来，进行端到端测试，并优化延迟——从用户说完话到 VoiceBot 开始回答，我们的目标是 **800ms 以内**。
