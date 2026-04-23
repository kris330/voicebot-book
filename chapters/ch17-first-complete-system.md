# 第 17 章：第一个完整系统

## 终于到了这一天

你已经学了 16 章。你知道 VAD 怎么判断用户在说话，ASR 怎么把语音转成文字，LLM 怎么生成回复，TTS 怎么把文字变成声音，Session 怎么隔离每个用户的状态。

但这些模块一直是分散的。今天，我们把它们全部连起来。

读完这章，你会有一个可以真正运行的 VoiceBot：打开浏览器，对着麦克风说话，AI 用语音回答你。

---

## 17.1 项目目录结构

```
voicebot/
├── config.json                 # 配置文件（API Key、模型选择等）
├── main.py                     # 服务器入口
├── requirements.txt            # Python 依赖
│
├── voicebot/                   # 核心代码包
│   ├── __init__.py
│   ├── session.py              # Session 类（第 16 章）
│   ├── session_manager.py      # SessionManager（第 16 章）
│   ├── config.py               # 配置加载
│   ├── pipeline.py             # ASR → LLM → TTS 流水线
│   │
│   ├── asr/
│   │   ├── __init__.py
│   │   └── openai_asr.py       # 使用 OpenAI Whisper API
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   └── openai_llm.py       # 使用 OpenAI Chat API
│   │
│   └── tts/
│       ├── __init__.py
│       └── openai_tts.py       # 使用 OpenAI TTS API
│
└── frontend/
    └── index.html              # 前端页面（录音 + 播放）
```

这个结构很扁平，适合初学者。后面系统变复杂了，再按功能分层。

---

## 17.2 完整的 config.json

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8765,
    "log_level": "INFO"
  },

  "session": {
    "timeout_seconds": 1800,
    "max_history_turns": 20,
    "system_prompt": "你是一个友好的语音助手。回答要简洁清晰，适合语音播放，避免使用 Markdown 格式。每次回答控制在 100 字以内。"
  },

  "asr": {
    "provider": "openai",
    "model": "whisper-1",
    "language": "zh",
    "api_key": "${OPENAI_API_KEY}"
  },

  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "max_tokens": 500,
    "api_key": "${OPENAI_API_KEY}"
  },

  "tts": {
    "provider": "openai",
    "model": "tts-1",
    "voice": "alloy",
    "speed": 1.0,
    "api_key": "${OPENAI_API_KEY}"
  }
}
```

`${OPENAI_API_KEY}` 是占位符，程序启动时从环境变量读取实际值。不要把真实的 API Key 写进配置文件。

---

## 17.3 配置加载模块

```python
# voicebot/config.py

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "INFO"


@dataclass(frozen=True)
class SessionConfig:
    timeout_seconds: int = 1800
    max_history_turns: int = 20
    system_prompt: str = ""


@dataclass(frozen=True)
class ASRConfig:
    provider: str = "openai"
    model: str = "whisper-1"
    language: str = "zh"
    api_key: str = ""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 500
    api_key: str = ""


@dataclass(frozen=True)
class TTSConfig:
    provider: str = "openai"
    model: str = "tts-1"
    voice: str = "alloy"
    speed: float = 1.0
    api_key: str = ""


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    session: SessionConfig
    asr: ASRConfig
    llm: LLMConfig
    tts: TTSConfig


def _resolve_env_vars(value: Any) -> Any:
    """
    递归解析配置中的环境变量占位符。
    ${VAR_NAME} 会被替换为对应的环境变量值。
    """
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'
        def replace(match):
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                logger.warning(f"环境变量 {var_name} 未设置")
                return ""
            return env_value
        return re.sub(pattern, replace, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: str = "config.json") -> Config:
    """
    从 JSON 文件加载配置，并解析环境变量占位符。

    Args:
        config_path: 配置文件路径

    Returns:
        Config 对象

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    # 解析环境变量
    resolved = _resolve_env_vars(raw)

    # 构建配置对象
    server_data = resolved.get("server", {})
    session_data = resolved.get("session", {})
    asr_data = resolved.get("asr", {})
    llm_data = resolved.get("llm", {})
    tts_data = resolved.get("tts", {})

    config = Config(
        server=ServerConfig(**server_data),
        session=SessionConfig(**session_data),
        asr=ASRConfig(**asr_data),
        llm=LLMConfig(**llm_data),
        tts=TTSConfig(**tts_data),
    )

    logger.info(
        f"配置已加载: ASR={config.asr.provider}/{config.asr.model}, "
        f"LLM={config.llm.provider}/{config.llm.model}, "
        f"TTS={config.tts.provider}/{config.tts.model}"
    )
    return config
```

---

## 17.4 ASR 模块：OpenAI Whisper

```python
# voicebot/asr/openai_asr.py

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
```

---

## 17.5 LLM 模块：OpenAI Chat

```python
# voicebot/llm/openai_llm.py

import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from ..config import LLMConfig

logger = logging.getLogger(__name__)


class OpenAILLM:
    """使用 OpenAI Chat API 生成回复（流式输出）。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key)

    async def generate_stream(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        """
        流式生成回复。

        Args:
            messages: OpenAI 格式的对话历史

        Yields:
            文字片段（token by token）
        """
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error(f"LLM 生成失败: {e}", exc_info=True)
            yield "抱歉，我现在有点问题，请稍后再试。"
```

---

## 17.6 TTS 模块：OpenAI TTS

```python
# voicebot/tts/openai_tts.py

import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from ..config import TTSConfig

logger = logging.getLogger(__name__)


class OpenAITTS:
    """使用 OpenAI TTS API 将文字转换为语音。"""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=config.api_key)

    async def synthesize_stream(
        self,
        text: str,
    ) -> AsyncGenerator[bytes, None]:
        """
        流式合成语音。

        Args:
            text: 要合成的文字

        Yields:
            音频数据块（PCM 或 MP3，取决于 response_format）
        """
        if not text.strip():
            return

        try:
            # OpenAI TTS API 支持流式响应
            async with self._client.audio.speech.with_streaming_response.create(
                model=self._config.model,
                voice=self._config.voice,
                input=text,
                response_format="pcm",  # 原始 PCM，方便客户端直接播放
                speed=self._config.speed,
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk

        except Exception as e:
            logger.error(f"TTS 合成失败: {e}", exc_info=True)
```

---

## 17.7 核心流水线：Pipeline

这是整个系统最核心的部分——把 ASR、LLM、TTS 串联成一个流水线：

```python
# voicebot/pipeline.py

import asyncio
import logging
from collections.abc import AsyncGenerator

from .asr.openai_asr import OpenAIASR
from .llm.openai_llm import OpenAILLM
from .tts.openai_tts import OpenAITTS
from .session import Session

logger = logging.getLogger(__name__)

# 触发 TTS 合成的最小文本单位
# 遇到这些标点符号时，立即把前面积累的文字送去 TTS
TTS_TRIGGER_PUNCTUATION = {"。", "！", "？", "，", "；", "…", ".", "!", "?", ","}

# 积累多少字符后强制触发 TTS（即使没遇到标点）
TTS_FORCE_TRIGGER_CHARS = 50


class VoicePipeline:
    """
    ASR → LLM → TTS 完整流水线。

    核心思路：
    1. 用 ASR 把音频转成文字
    2. 把文字送给 LLM，流式获取回复
    3. 不等 LLM 全部输出完——第一个句子出来就立刻送给 TTS
    4. TTS 流式合成，第一帧音频出来就放入 Session 的 TTS 队列
    5. 独立的发送协程从队列里取音频，发给客户端
    """

    def __init__(self, asr: OpenAIASR, llm: OpenAILLM, tts: OpenAITTS) -> None:
        self._asr = asr
        self._llm = llm
        self._tts = tts

    async def process(self, session: Session, audio_data: bytes) -> None:
        """
        处理一次完整的语音输入。

        Args:
            session: 当前会话
            audio_data: 用户的语音 PCM 数据
        """
        # 步骤 1：ASR 识别
        logger.info(f"[{session.session_id}] 开始 ASR 识别...")
        user_text = await self._asr.transcribe(audio_data)

        if not user_text:
            logger.info(f"[{session.session_id}] ASR 结果为空，跳过")
            return

        # 把用户消息加入历史
        session.add_user_message(user_text)

        # 通知客户端识别结果（可选，用于显示字幕）
        import json
        await session.websocket.send(json.dumps({
            "type": "asr_result",
            "text": user_text,
        }))

        # 步骤 2 & 3：LLM 生成 + TTS 合成（同步流水线）
        logger.info(f"[{session.session_id}] 开始 LLM 生成...")

        # 创建 LLM + TTS 流水线任务
        pipeline_task = asyncio.create_task(
            self._llm_tts_pipeline(session),
            name=f"pipeline-{session.session_id}"
        )
        session.current_llm_task = pipeline_task

        try:
            await pipeline_task
        except asyncio.CancelledError:
            logger.info(f"[{session.session_id}] 流水线已被取消（打断）")
        except Exception as e:
            logger.error(f"[{session.session_id}] 流水线错误: {e}", exc_info=True)

    async def _llm_tts_pipeline(self, session: Session) -> None:
        """
        LLM 生成 → 按句子切分 → TTS 合成 → 放入播放队列。

        关键优化：不等 LLM 全部输出完，第一句就送 TTS。
        """
        messages = session.get_llm_messages()
        full_response = []
        pending_text = ""  # 积累中的文字，等待凑够一句话

        async for token in self._llm.generate_stream(messages):
            full_response.append(token)
            pending_text += token

            # 判断是否应该触发 TTS
            should_trigger = (
                any(p in pending_text for p in TTS_TRIGGER_PUNCTUATION)
                or len(pending_text) >= TTS_FORCE_TRIGGER_CHARS
            )

            if should_trigger:
                text_to_synthesize = pending_text.strip()
                pending_text = ""

                if text_to_synthesize:
                    logger.debug(
                        f"[{session.session_id}] TTS 触发: '{text_to_synthesize[:30]}...'"
                    )
                    await self._synthesize_and_enqueue(session, text_to_synthesize)

        # 处理最后剩余的文字
        if pending_text.strip():
            await self._synthesize_and_enqueue(session, pending_text.strip())

        # 记录完整回复到对话历史
        full_response_text = "".join(full_response)
        session.add_assistant_message(full_response_text)

        # 发送结束信号
        import json
        await session.websocket.send(json.dumps({
            "type": "tts_end",
        }))

        logger.info(f"[{session.session_id}] 流水线完成，回复长度: {len(full_response_text)} 字")

    async def _synthesize_and_enqueue(self, session: Session, text: str) -> None:
        """
        合成一段文字，把音频块逐一放入 TTS 队列。
        """
        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                # 检查是否被取消
                if session.is_closed:
                    return
                await session.tts_queue.put(audio_chunk)
        except asyncio.CancelledError:
            raise  # 向上传播，让调用方知道被取消了
        except Exception as e:
            logger.error(
                f"[{session.session_id}] TTS 合成失败 ('{text[:20]}...'): {e}"
            )
```

---

## 17.8 服务端入口：main.py

```python
# main.py

import asyncio
import json
import logging
import os
import signal
import sys

import websockets

from voicebot.config import load_config
from voicebot.session_manager import SessionManager
from voicebot.asr.openai_asr import OpenAIASR
from voicebot.llm.openai_llm import OpenAILLM
from voicebot.tts.openai_tts import OpenAITTS
from voicebot.pipeline import VoicePipeline


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class VoiceBotServer:
    """VoiceBot WebSocket 服务器。"""

    def __init__(self, config_path: str = "config.json") -> None:
        self._config = load_config(config_path)

        # 初始化各模块
        self._asr = OpenAIASR(self._config.asr)
        self._llm = OpenAILLM(self._config.llm)
        self._tts = OpenAITTS(self._config.tts)
        self._pipeline = VoicePipeline(self._asr, self._llm, self._tts)

        self._session_manager = SessionManager(
            session_timeout_seconds=self._config.session.timeout_seconds,
            system_prompt=self._config.session.system_prompt,
        )

    async def start(self) -> None:
        """启动服务器。"""
        setup_logging(self._config.server.log_level)
        logger = logging.getLogger(__name__)

        await self._session_manager.start()

        host = self._config.server.host
        port = self._config.server.port

        async with websockets.serve(
            self._handle_connection,
            host,
            port,
            # 增大消息大小限制，音频数据可能很大
            max_size=10 * 1024 * 1024,  # 10MB
            # 保持连接活跃
            ping_interval=20,
            ping_timeout=60,
        ) as server:
            logger.info(f"VoiceBot 已启动！WebSocket 地址: ws://{host}:{port}")
            logger.info(f"打开浏览器访问: http://localhost:{port}")

            # 等待关闭信号
            stop_event = asyncio.Event()

            def _handle_signal():
                logger.info("收到关闭信号，正在停止服务器...")
                stop_event.set()

            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _handle_signal)

            await stop_event.wait()

        await self._session_manager.stop()
        logger.info("服务器已停止")

    async def _handle_connection(self, websocket) -> None:
        """处理单个 WebSocket 连接。"""
        logger = logging.getLogger(__name__)

        # 创建 Session
        session = await self._session_manager.create_session(websocket)

        # 启动 TTS 发送协程
        tts_sender_task = asyncio.create_task(
            self._tts_sender(session),
            name=f"tts-sender-{session.session_id}"
        )

        try:
            # 发送欢迎消息
            await websocket.send(json.dumps({
                "type": "connected",
                "session_id": session.session_id,
                "message": "VoiceBot 已连接，请开始说话",
            }))

            # 主消息处理循环
            async for raw_message in websocket:
                await self._handle_message(session, raw_message)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(
                f"[{session.session_id}] 连接关闭: code={e.code}"
            )
        except Exception as e:
            logger.error(
                f"[{session.session_id}] 连接处理错误: {e}",
                exc_info=True
            )
        finally:
            await self._session_manager.remove_session(session.session_id)
            tts_sender_task.cancel()
            try:
                await tts_sender_task
            except asyncio.CancelledError:
                pass

    async def _handle_message(self, session, raw_message) -> None:
        """处理来自客户端的消息。"""
        session.touch()

        if isinstance(raw_message, bytes):
            # 音频数据
            session.append_asr_audio(raw_message)
        else:
            try:
                data = json.loads(raw_message)
                await self._handle_control(session, data)
            except json.JSONDecodeError:
                pass

    async def _handle_control(self, session, data: dict) -> None:
        """处理控制消息。"""
        logger = logging.getLogger(__name__)
        msg_type = data.get("type")

        if msg_type == "vad_end":
            # 用户说完话了，触发流水线
            audio_data = session.clear_asr_buffer()
            if audio_data:
                # 异步处理，不阻塞主消息循环
                asyncio.create_task(
                    self._pipeline.process(session, audio_data),
                    name=f"pipeline-{session.session_id}"
                )

        elif msg_type == "interrupt":
            # 打断：取消当前任务，清空队列
            logger.info(f"[{session.session_id}] 处理打断信号")
            await session.cancel_current_tasks()
            await session.drain_tts_queue()

        elif msg_type == "ping":
            await session.websocket.send(json.dumps({"type": "pong"}))

    async def _tts_sender(self, session) -> None:
        """从 TTS 队列发送音频到客户端。"""
        logger = logging.getLogger(__name__)

        while True:
            try:
                audio_chunk = await session.tts_queue.get()
                if audio_chunk is None:
                    break
                await session.websocket.send(audio_chunk)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{session.session_id}] TTS 发送错误: {e}")
                break


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    server = VoiceBotServer(config_path)
    asyncio.run(server.start())
```

---

## 17.9 前端页面：index.html

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VoiceBot</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            gap: 24px;
        }

        h1 { font-size: 2rem; color: #a29bfe; }

        #status {
            font-size: 0.9rem;
            color: #74b9ff;
            height: 20px;
        }

        #mic-btn {
            width: 100px;
            height: 100px;
            border-radius: 50%;
            border: 3px solid #6c5ce7;
            background: #2d3561;
            cursor: pointer;
            font-size: 2.5rem;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        #mic-btn:hover { background: #3d4571; transform: scale(1.05); }
        #mic-btn.recording { background: #d63031; border-color: #ff7675; animation: pulse 1s infinite; }
        #mic-btn.processing { background: #6c5ce7; border-color: #a29bfe; }

        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.08); }
        }

        #transcript {
            max-width: 600px;
            width: 90vw;
            background: #16213e;
            border-radius: 12px;
            padding: 16px;
            min-height: 120px;
            font-size: 0.95rem;
            line-height: 1.6;
        }

        .msg-user { color: #74b9ff; margin-bottom: 8px; }
        .msg-ai { color: #55efc4; margin-bottom: 12px; }
        .msg-label { font-size: 0.75rem; opacity: 0.6; margin-bottom: 2px; }

        #hint { font-size: 0.8rem; color: #636e72; }
    </style>
</head>
<body>
    <h1>🎙 VoiceBot</h1>
    <div id="status">正在连接...</div>
    <button id="mic-btn" disabled>🎤</button>
    <div id="transcript"></div>
    <div id="hint">按住按钮说话，松开后 AI 回复</div>

    <script>
    // ============================================================
    // 配置
    // ============================================================
    const WS_URL = "ws://localhost:8765";
    const SAMPLE_RATE = 16000;
    const VAD_SILENCE_THRESHOLD = 0.01;  // 静音检测阈值
    const VAD_SILENCE_MS = 800;          // 静音超过多少毫秒判定说话结束

    // ============================================================
    // 状态
    // ============================================================
    let ws = null;
    let mediaStream = null;
    let audioContext = null;
    let processor = null;
    let isRecording = false;
    let audioPlayQueue = [];
    let isPlaying = false;
    let silenceTimer = null;
    let hasVoice = false;

    // ============================================================
    // WebSocket 连接
    // ============================================================
    function connect() {
        setStatus("正在连接...");
        ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            setStatus("已连接，点击按钮说话");
            document.getElementById("mic-btn").disabled = false;
        };

        ws.onclose = () => {
            setStatus("连接断开，3秒后重连...");
            document.getElementById("mic-btn").disabled = true;
            setTimeout(connect, 3000);
        };

        ws.onerror = (e) => {
            console.error("WebSocket 错误:", e);
        };

        ws.onmessage = (event) => {
            if (event.data instanceof Blob) {
                // 收到音频数据（TTS 输出）
                handleAudioChunk(event.data);
            } else {
                // 收到控制消息
                const data = JSON.parse(event.data);
                handleControlMessage(data);
            }
        };
    }

    function handleControlMessage(data) {
        if (data.type === "connected") {
            console.log("Session ID:", data.session_id);
        } else if (data.type === "asr_result") {
            appendMessage("user", data.text);
        } else if (data.type === "tts_end") {
            // AI 说完了，可以再次录音
            setStatus("AI 说完了，你可以继续说话");
        }
    }

    // ============================================================
    // 音频录制（按住说话）
    // ============================================================
    const micBtn = document.getElementById("mic-btn");

    micBtn.addEventListener("mousedown", startRecording);
    micBtn.addEventListener("mouseup", stopRecording);
    micBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
    micBtn.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });

    async function startRecording() {
        if (isRecording) return;

        // 打断 AI 正在播放的音频
        if (isPlaying) {
            interruptPlayback();
        }

        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: SAMPLE_RATE,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });

            audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
            const source = audioContext.createMediaStreamSource(mediaStream);

            // ScriptProcessor 用于获取 PCM 数据（简单可靠）
            processor = audioContext.createScriptProcessor(4096, 1, 1);
            source.connect(processor);
            processor.connect(audioContext.destination);

            processor.onaudioprocess = (e) => {
                if (!isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;

                const float32 = e.inputBuffer.getChannelData(0);
                const int16 = float32ToInt16(float32);
                ws.send(int16.buffer);
            };

            isRecording = true;
            micBtn.classList.add("recording");
            setStatus("正在录音...");
            ws.send(JSON.stringify({ type: "vad_start" }));

        } catch (err) {
            console.error("无法访问麦克风:", err);
            setStatus("无法访问麦克风，请检查权限");
        }
    }

    function stopRecording() {
        if (!isRecording) return;

        isRecording = false;
        micBtn.classList.remove("recording");
        micBtn.classList.add("processing");
        setStatus("正在识别...");

        // 停止录音
        if (processor) { processor.disconnect(); processor = null; }
        if (audioContext) { audioContext.close(); audioContext = null; }
        if (mediaStream) {
            mediaStream.getTracks().forEach(t => t.stop());
            mediaStream = null;
        }

        // 告诉服务端说话结束
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "vad_end" }));
        }

        setTimeout(() => micBtn.classList.remove("processing"), 500);
    }

    // ============================================================
    // 音频播放（TTS 输出）
    // ============================================================
    let playContext = null;
    let currentSource = null;

    async function handleAudioChunk(blob) {
        const arrayBuffer = await blob.arrayBuffer();
        audioPlayQueue.push(arrayBuffer);

        if (!isPlaying) {
            playNext();
        }
    }

    async function playNext() {
        if (audioPlayQueue.length === 0) {
            isPlaying = false;
            return;
        }

        isPlaying = true;
        const buffer = audioPlayQueue.shift();

        if (!playContext || playContext.state === "closed") {
            playContext = new AudioContext({ sampleRate: 24000 });
        }

        try {
            // PCM 数据解码为 AudioBuffer
            // OpenAI TTS 输出 24kHz 16bit PCM
            const pcm = new Int16Array(buffer);
            const float32 = new Float32Array(pcm.length);
            for (let i = 0; i < pcm.length; i++) {
                float32[i] = pcm[i] / 32768.0;
            }

            const audioBuffer = playContext.createBuffer(1, float32.length, 24000);
            audioBuffer.copyToChannel(float32, 0);

            currentSource = playContext.createBufferSource();
            currentSource.buffer = audioBuffer;
            currentSource.connect(playContext.destination);
            currentSource.onended = playNext;
            currentSource.start();

            setStatus("AI 正在说话...");
        } catch (err) {
            console.error("播放音频失败:", err);
            playNext();
        }
    }

    function interruptPlayback() {
        // 停止当前播放
        if (currentSource) {
            try { currentSource.stop(); } catch(e) {}
            currentSource = null;
        }

        // 清空播放队列
        audioPlayQueue = [];
        isPlaying = false;

        // 通知服务端打断
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "interrupt" }));
        }
    }

    // ============================================================
    // 工具函数
    // ============================================================
    function float32ToInt16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array;
    }

    function setStatus(text) {
        document.getElementById("status").textContent = text;
    }

    function appendMessage(role, text) {
        const div = document.createElement("div");
        const label = role === "user" ? "你" : "AI";
        const cls = role === "user" ? "msg-user" : "msg-ai";
        div.innerHTML = `<div class="msg-label">${label}</div><div>${text}</div>`;
        div.className = cls;
        const transcript = document.getElementById("transcript");
        transcript.appendChild(div);
        transcript.scrollTop = transcript.scrollHeight;
    }

    // ============================================================
    // 启动
    // ============================================================
    connect();
    </script>
</body>
</html>
```

---

## 17.10 requirements.txt

```
websockets>=12.0
openai>=1.30.0
python-dotenv>=1.0.0
```

---

## 17.11 端到端跑通步骤

### 第一步：安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt
```

### 第二步：配置 API Key

```bash
# 方法一：设置环境变量（推荐）
export OPENAI_API_KEY="sk-your-api-key-here"

# 方法二：创建 .env 文件
echo "OPENAI_API_KEY=sk-your-api-key-here" > .env
```

如果用 `.env` 文件，在 `main.py` 开头加一行：

```python
from dotenv import load_dotenv
load_dotenv()
```

### 第三步：检查项目结构

```bash
ls -la
# 应该看到：
# config.json
# main.py
# requirements.txt
# voicebot/
# frontend/
```

### 第四步：启动服务

```bash
python main.py
# 或者指定配置文件
python main.py config.json
```

看到这个输出说明启动成功：

```
08:30:15 [INFO] __main__: VoiceBot 已启动！WebSocket 地址: ws://0.0.0.0:8765
08:30:15 [INFO] __main__: 打开浏览器访问: http://localhost:8765
```

### 第五步：打开前端页面

用浏览器直接打开 `frontend/index.html`（Chrome/Firefox/Edge 均可）。

或者用 Python 起一个简单的 HTTP 服务：

```bash
# 另开一个终端
cd frontend
python -m http.server 8080
# 然后访问 http://localhost:8080
```

### 第六步：说话测试

1. 点击麦克风按钮（或按住）
2. 说一句话，例如"你好，介绍一下你自己"
3. 松开按钮
4. 等待 1-3 秒，听到 AI 的语音回复

---

## 17.12 常见问题排查清单

**问题 1：浏览器提示"无法连接到服务器"**

```
检查项：
□ 服务端是否已启动？（看终端有没有"VoiceBot 已启动"）
□ 端口是否正确？（config.json 里的 port 和前端 WS_URL 要一致）
□ 防火墙是否拦截了 8765 端口？
□ 试试 ws://127.0.0.1:8765 替代 ws://localhost:8765
```

**问题 2：点击麦克风按钮没反应**

```
检查项：
□ 浏览器是否请求了麦克风权限？（地址栏左侧有摄像头/麦克风图标）
□ 系统设置里是否允许浏览器访问麦克风？
□ 打开浏览器开发者工具（F12），看 Console 有没有报错
□ 必须使用 HTTPS 或 localhost（navigator.mediaDevices 在非 HTTPS 下不可用）
```

**问题 3：ASR 返回空结果**

```
检查项：
□ 录音时间是否太短？（说话少于 0.5 秒可能识别不到）
□ 麦克风音量是否太低？（系统声音设置里检查）
□ API Key 是否正确？（看服务端日志有没有认证错误）
□ 服务端日志里打印了多少字节的音频？（少于 1000 bytes 可能录音有问题）
```

**问题 4：有文字识别结果但没有声音**

```
检查项：
□ 浏览器音量是否静音？
□ 服务端日志里 TTS 合成有没有报错？
□ 浏览器开发者工具 Network 标签，看有没有 binary 类型的 WebSocket 消息
□ 试试用耳机（某些系统的扬声器和麦克风会互相干扰）
```

**问题 5：延迟很高（超过 5 秒）**

```
检查项：
□ 网络连接是否稳定？OpenAI API 在国内访问可能需要代理
□ 试试换 gpt-4o-mini（比 gpt-4 快很多）
□ 把 max_tokens 调小到 200
□ 看第 18 章延迟分析，找到具体瓶颈
```

---

## 17.13 用本地模型替换云端 API

如果你不想用 OpenAI API，或者需要完全离线运行，可以用本地模型替换：

**ASR：用 faster-whisper 替换**

```python
# voicebot/asr/local_asr.py

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
```

**LLM：用 Ollama 替换**

```python
# voicebot/llm/ollama_llm.py

import httpx
import json
from collections.abc import AsyncGenerator


class OllamaLLM:
    def __init__(self, model: str = "qwen2:7b", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url

    async def generate_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": True},
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        data = json.loads(line)
                        if content := data.get("message", {}).get("content"):
                            yield content
                        if data.get("done"):
                            break
```

**TTS：用 kokoro 或 edge-tts 替换**

```python
# voicebot/tts/edge_tts_impl.py

import edge_tts
from collections.abc import AsyncGenerator


class EdgeTTS:
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        communicate = edge_tts.Communicate(text, self._voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
```

在 `config.json` 里把 `provider` 改成 `"local"` 或 `"ollama"`，然后在 `main.py` 里根据配置选择不同的实现类。

---

## 本章小结

本章我们完成了 VoiceBot 第一个可运行的完整系统：

- **项目结构**：清晰的分层目录，ASR/LLM/TTS 各自独立，易于替换
- **配置系统**：JSON 配置 + 环境变量，API Key 不硬编码
- **完整流水线**：音频输入 → ASR → LLM 流式输出 → TTS 按句触发 → 音频发送
- **前端页面**：按住说话、松开识别、自动播放 AI 回复，支持打断
- **跑通步骤**：从安装依赖到听到第一句 AI 回复
- **本地化方案**：如何把云端 API 替换成本地模型

你现在手里有了一个可以跑起来的语音助手。但它有多快？哪里是瓶颈？

**下一章预告**：我们来系统性地测量和分析延迟。从用户说完话到听到 AI 第一句回复，这段时间叫 TTFS（Time to First Speech）。我们会把它拆解成每个节点，找到各自的典型数值，然后一一优化。
