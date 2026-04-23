# 第九章：服务端 VAD

## 开篇场景

VoiceBot 跑了一段时间，你开始收到用户反馈：

> "有时候我明明说完了，但 AI 半天没反应。"
> "有时候我还没说完，AI 就开始回答了，把我的话截断了。"

你去查日志，发现问题出在客户端 VAD。手机离嘴远的时候，音量检测的阈值不够，静音判断提前了；在嘈杂环境里，背景噪声被误识别为语音。

更麻烦的是，网络抖动会让音频包乱序或丢失，导致客户端 VAD 的时间序列完全错乱。

这就是为什么生产级 VoiceAI 系统需要**服务端 VAD**——它是客户端 VAD 的兜底，也是整个语音管道中最重要的"指挥官"之一。

---

## 9.1 服务端 VAD 的价值

先来理解一下整体架构：

```
客户端                          服务端
┌─────────────────────┐        ┌──────────────────────────────┐
│                     │        │                              │
│  麦克风 → 音频采集   │        │  ┌──────────┐               │
│       ↓             │  音频  │  │ 接收缓冲  │               │
│  客户端 VAD（可选）  │ ──────▶ │  └────┬─────┘               │
│       ↓             │        │       ↓                      │
│  WebSocket 发送     │        │  ┌──────────┐  触发  ┌─────┐ │
│                     │        │  │ 服务端VAD │ ────▶  │ ASR │ │
└─────────────────────┘        │  └──────────┘        └─────┘ │
                               │                              │
                               └──────────────────────────────┘
```

服务端 VAD 的主要价值：

1. **抵御网络抖动**：客户端发来的音频包可能乱序、重复或丢失。服务端 VAD 在有序的、经过重组的音频流上工作，更可靠
2. **统一决策权**：无论客户端是手机、PC 还是电话网关，服务端 VAD 提供一致的行为
3. **客户端 VAD 兜底**：客户端 VAD 漏检时，服务端 VAD 兜住；客户端 VAD 误触发时，服务端 VAD 可以过滤
4. **访问更强大的模型**：服务端可以运行神经网络 VAD（如 FSMN-VAD），准确率比纯能量检测高得多

---

## 9.2 FSMN-VAD：工业级神经网络 VAD

FSMN-VAD 是阿里巴巴开源的基于 FSMN（前馈序列记忆网络）的 VAD 模型，已集成在 FunASR 框架中。

它的核心优势：
- **极低延迟**：单帧推理时间 < 1ms（在普通 CPU 上）
- **高准确率**：在复杂环境（嘈杂办公室、马路边）效果远超能量阈值法
- **流式推理**：支持逐帧输入，不需要等待完整音频

### 安装

```bash
# 安装 FunASR（包含 FSMN-VAD）
pip install funasr

# 如果需要 GPU 加速
pip install funasr torch torchvision torchaudio

# 下载 FSMN-VAD 模型（首次运行时自动下载，也可以提前手动下载）
# 模型大小约 2MB，非常轻量
```

### 快速验证

```python
# scripts/test_fsmn_vad.py
# 运行前准备一个测试音频文件：test.wav（16kHz, 单声道）

from funasr import AutoModel

# 加载 FSMN-VAD 模型
vad_model = AutoModel(
    model="fsmn-vad",
    model_revision="v2.0.4",
)

# 对完整音频文件做 VAD
result = vad_model.generate(input="test.wav")
print("VAD 结果:", result)
# 输出格式示例：
# [{'key': 'test', 'value': [[0, 2300], [3500, 6000]]}]
# 表示 0-2300ms 和 3500-6000ms 是语音段
```

---

## 9.3 流式 VAD 的状态机设计

对完整录音做 VAD 很简单，但 VoiceBot 需要的是**流式 VAD**：音频实时流入，实时给出"语音开始"和"语音结束"的判断。

流式 VAD 的核心是一个状态机：

```
         检测到语音
SILENCE ──────────────▶ SPEECH
   ▲                      │
   │  静音超过阈值          │
   └──────────────────────┘

SPEECH 状态内：
- 持续收集音频
- 最大语音长度超限 → 强制结束

状态转换条件：
┌─────────────────────────────────────────────────┐
│  SILENCE → SPEECH:                              │
│    连续 N 帧检测为语音（避免爆音误触发）           │
│                                                 │
│  SPEECH → SILENCE:                              │
│    连续 M 帧检测为静音（trailing silence 策略）  │
│    或总语音长度超过 max_speech_ms               │
└─────────────────────────────────────────────────┘
```

### VAD 参数说明

| 参数 | 典型值 | 说明 |
|------|--------|------|
| `speech_threshold` | 0.5 | 模型输出概率超过此值认为是语音 |
| `silence_threshold_ms` | 600ms | 静音持续多久才算说完 |
| `min_speech_ms` | 200ms | 少于此时长的语音段忽略（过滤咳嗽、杂音） |
| `max_speech_ms` | 30000ms | 超过此时长强制截断（防止用户不停说） |
| `pre_roll_ms` | 100ms | 语音开始点前保留的音频（避免切掉开头） |

---

## 9.4 VADManager 完整实现

```python
# src/voicebot/vad/vad_manager.py

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class VADState(Enum):
    SILENCE = auto()   # 静音状态
    SPEECH = auto()    # 语音状态


@dataclass
class VADConfig:
    """VAD 配置参数"""
    # 模型推理参数
    speech_threshold: float = 0.5       # 语音概率阈值
    sample_rate: int = 16000            # 采样率（必须是 16kHz）
    chunk_ms: int = 60                  # 每帧时长（FSMN-VAD 推荐 60ms）

    # 状态转换参数
    silence_threshold_ms: int = 600     # 静音多久判定为说完
    min_speech_ms: int = 200            # 最短有效语音时长
    max_speech_ms: int = 30000          # 最长语音段

    # 预滚动：保留语音开始前的音频
    pre_roll_ms: int = 100

    # 触发语音开始需要连续多少帧检测为语音
    speech_trigger_frames: int = 2

    @property
    def chunk_samples(self) -> int:
        return int(self.sample_rate * self.chunk_ms / 1000)

    @property
    def silence_threshold_frames(self) -> int:
        return self.silence_threshold_ms // self.chunk_ms

    @property
    def min_speech_frames(self) -> int:
        return self.min_speech_ms // self.chunk_ms

    @property
    def pre_roll_frames(self) -> int:
        return self.pre_roll_ms // self.chunk_ms


@dataclass
class SpeechSegment:
    """一段完整的语音"""
    audio: np.ndarray          # 音频数据（int16）
    start_ms: float            # 相对于会话开始的时间戳（毫秒）
    end_ms: float
    duration_ms: float = field(init=False)

    def __post_init__(self):
        self.duration_ms = self.end_ms - self.start_ms


class VADManager:
    """
    服务端流式 VAD 管理器

    使用方式：
        vad = VADManager()
        await vad.init()

        async for segment in vad.process_stream(audio_generator):
            # segment 是一段完整的语音，可以送给 ASR
            asr_result = await asr.transcribe(segment.audio)
    """

    def __init__(self, config: Optional[VADConfig] = None):
        self._config = config or VADConfig()
        self._model = None
        self._state = VADState.SILENCE
        self._speech_buffer: list[np.ndarray] = []    # 当前语音段的音频帧
        self._pre_roll_buffer: deque = deque(maxlen=self._config.pre_roll_frames)
        self._silence_frames = 0          # 连续静音帧计数
        self._speech_frames = 0           # 连续语音帧计数
        self._total_speech_frames = 0     # 本次语音段总帧数
        self._session_start_time = time.monotonic()
        self._model_cache = {}            # FSMN-VAD 需要维护帧间状态

    async def init(self):
        """加载 FSMN-VAD 模型（异步，避免阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info("VADManager 初始化完成")

    def _load_model(self):
        """在线程池中加载模型"""
        from funasr import AutoModel
        self._model = AutoModel(
            model="fsmn-vad",
            model_revision="v2.0.4",
            disable_log=True,
        )
        logger.info("FSMN-VAD 模型加载完成")

    def reset(self):
        """重置 VAD 状态（新会话开始时调用）"""
        self._state = VADState.SILENCE
        self._speech_buffer.clear()
        self._pre_roll_buffer.clear()
        self._silence_frames = 0
        self._speech_frames = 0
        self._total_speech_frames = 0
        self._session_start_time = time.monotonic()
        self._model_cache = {}
        logger.debug("VAD 状态已重置")

    def _get_timestamp_ms(self) -> float:
        """获取当前相对时间戳（毫秒）"""
        return (time.monotonic() - self._session_start_time) * 1000

    def _run_inference(self, audio_chunk: np.ndarray) -> float:
        """
        对一帧音频运行 FSMN-VAD 推理

        Returns:
            语音概率 [0.0, 1.0]
        """
        # FSMN-VAD 接受 float32，范围 [-1, 1]
        if audio_chunk.dtype == np.int16:
            audio_float = audio_chunk.astype(np.float32) / 32768.0
        else:
            audio_float = audio_chunk.astype(np.float32)

        result = self._model.generate(
            input=audio_float,
            cache=self._model_cache,           # 传入缓存，维护帧间状态
            is_final=False,                    # 流式模式
            chunk_size=self._config.chunk_ms,  # 告诉模型每帧时长
        )

        # 解析模型输出
        # 流式模式下，result 是 VAD 段的列表
        # 如果当前帧是语音，列表中会有未结束的段（end=-1）
        if result and result[0].get('value'):
            segments = result[0]['value']
            for seg in segments:
                if seg[1] == -1:  # end=-1 表示语音还在继续
                    return 1.0    # 当前帧是语音
            # 有已结束的段，当前帧可能是静音
            return 0.0
        return 0.0

    async def process_chunk(self, audio_chunk: np.ndarray) -> Optional[SpeechSegment]:
        """
        处理一帧音频

        Args:
            audio_chunk: int16 音频数据，长度应为 chunk_samples

        Returns:
            如果一段语音结束，返回 SpeechSegment；否则返回 None
        """
        cfg = self._config
        loop = asyncio.get_event_loop()

        # 在线程池中运行推理（避免阻塞事件循环）
        speech_prob = await loop.run_in_executor(
            None, self._run_inference, audio_chunk
        )

        is_speech = speech_prob >= cfg.speech_threshold

        if self._state == VADState.SILENCE:
            # 静音状态：维护预滚动缓冲区
            self._pre_roll_buffer.append(audio_chunk.copy())

            if is_speech:
                self._speech_frames += 1
                if self._speech_frames >= cfg.speech_trigger_frames:
                    # 连续多帧检测为语音，进入 SPEECH 状态
                    self._state = VADState.SPEECH
                    self._silence_frames = 0
                    self._total_speech_frames = self._speech_frames

                    # 把预滚动的帧加入语音缓冲
                    self._speech_buffer = list(self._pre_roll_buffer)
                    self._speech_buffer.append(audio_chunk.copy())

                    speech_start_ms = self._get_timestamp_ms()
                    logger.debug(f"语音开始 @ {speech_start_ms:.0f}ms，概率={speech_prob:.2f}")
            else:
                self._speech_frames = 0

        elif self._state == VADState.SPEECH:
            # 语音状态：收集音频
            self._speech_buffer.append(audio_chunk.copy())
            self._total_speech_frames += 1

            if is_speech:
                self._silence_frames = 0
            else:
                self._silence_frames += 1

            # 判断是否结束
            speech_ended = False
            reason = ""

            if self._silence_frames >= cfg.silence_threshold_frames:
                speech_ended = True
                reason = f"静音超过 {cfg.silence_threshold_ms}ms"
            elif self._total_speech_frames * cfg.chunk_ms >= cfg.max_speech_ms:
                speech_ended = True
                reason = f"超过最大时长 {cfg.max_speech_ms}ms"

            if speech_ended:
                # 检查最短语音时长
                total_ms = self._total_speech_frames * cfg.chunk_ms
                if total_ms >= cfg.min_speech_ms:
                    # 拼接所有音频帧
                    audio_data = np.concatenate(self._speech_buffer)
                    end_ms = self._get_timestamp_ms()
                    start_ms = end_ms - total_ms

                    segment = SpeechSegment(
                        audio=audio_data,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    )
                    logger.info(
                        f"语音结束，时长={total_ms:.0f}ms，原因={reason}，"
                        f"样本数={len(audio_data)}"
                    )
                else:
                    logger.debug(f"语音段过短（{total_ms}ms < {cfg.min_speech_ms}ms），丢弃")
                    segment = None

                # 重置状态
                self._state = VADState.SILENCE
                self._speech_buffer.clear()
                self._pre_roll_buffer.clear()
                self._silence_frames = 0
                self._speech_frames = 0
                self._total_speech_frames = 0

                return segment

        return None

    async def process_stream(self, audio_generator):
        """
        处理音频流的异步生成器

        Args:
            audio_generator: 异步生成器，每次 yield int16 音频帧

        Yields:
            SpeechSegment: 每段完整的语音
        """
        cfg = self._config

        async for raw_chunk in audio_generator:
            # 确保音频块是 numpy array
            if not isinstance(raw_chunk, np.ndarray):
                chunk = np.frombuffer(raw_chunk, dtype=np.int16)
            else:
                chunk = raw_chunk

            # 如果音频块大小不一致，按 chunk_samples 分割处理
            for i in range(0, len(chunk), cfg.chunk_samples):
                frame = chunk[i:i + cfg.chunk_samples]

                # 最后一帧可能不足，用零填充
                if len(frame) < cfg.chunk_samples:
                    frame = np.pad(frame, (0, cfg.chunk_samples - len(frame)))

                segment = await self.process_chunk(frame)
                if segment is not None:
                    yield segment
```

---

## 9.5 VAD 与 ASR 的协作

VAD 检测到语音结束后，需要把收集到的音频送给 ASR。这个流程需要仔细设计：

```
音频流
  │
  ▼
VADManager.process_chunk()
  │
  │ 检测到语音结束
  ▼
SpeechSegment（完整音频帧）
  │
  ├──▶ 发送给 ASR（并发，不阻塞 VAD 继续工作）
  │
  ▼
ASR 结果回调
  │
  ▼
LLM 处理 → TTS 播放
```

关键设计原则：**VAD 和 ASR 必须解耦**。VAD 发现语音后，立刻把音频塞给 ASR 任务，然后继续监听下一句话，不能等 ASR 返回结果。

```python
# src/voicebot/pipeline/voice_pipeline.py

import asyncio
import logging
from typing import Optional

import numpy as np

from voicebot.vad.vad_manager import VADManager, VADConfig, SpeechSegment
from voicebot.asr.asr_manager import ASRManager

logger = logging.getLogger(__name__)


class VoicePipeline:
    """
    语音处理管道：VAD → ASR → LLM → TTS
    这一章只实现 VAD → ASR 部分
    """

    def __init__(self):
        self._vad = VADManager(VADConfig(
            silence_threshold_ms=600,
            min_speech_ms=200,
            max_speech_ms=30000,
            speech_threshold=0.5,
        ))
        self._asr = ASRManager()
        self._asr_task: Optional[asyncio.Task] = None
        self._on_transcript = None  # 识别结果回调
        self._on_speech_start = None

    async def init(self):
        await self._vad.init()
        await self._asr.init()
        logger.info("VoicePipeline 初始化完成")

    def on_transcript(self, callback):
        """注册识别结果回调"""
        self._on_transcript = callback
        return self

    def on_speech_start(self, callback):
        """注册语音开始回调（可以用于打断 AI 说话）"""
        self._on_speech_start = callback
        return self

    async def process_audio(self, audio_chunk: bytes):
        """
        处理一块原始音频数据（从 WebSocket 收到的）

        Args:
            audio_chunk: 原始 int16 音频字节
        """
        chunk = np.frombuffer(audio_chunk, dtype=np.int16)
        segment = await self._vad.process_chunk(chunk)

        if segment is not None:
            # VAD 检测到一段完整语音，异步发给 ASR
            asyncio.create_task(self._run_asr(segment))

    async def _run_asr(self, segment: SpeechSegment):
        """在独立任务中运行 ASR，不阻塞 VAD"""
        try:
            logger.info(f"送入 ASR，时长={segment.duration_ms:.0f}ms")

            result = await self._asr.transcribe(segment.audio)

            if result and self._on_transcript:
                await self._on_transcript(result)
                logger.info(f"ASR 结果: {result!r}")
        except Exception as e:
            logger.error(f"ASR 处理失败: {e}", exc_info=True)

    def reset(self):
        """重置状态（新会话开始时调用）"""
        self._vad.reset()
```

---

## 9.6 在 WebSocket 服务中集成

```python
# src/voicebot/server/ws_handler.py

import asyncio
import logging
import json

from aiohttp import web, WSMsgType

from voicebot.pipeline.voice_pipeline import VoicePipeline

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理单个 WebSocket 连接的生命周期"""

    def __init__(self, ws: web.WebSocketResponse, session_id: str):
        self._ws = ws
        self._session_id = session_id
        self._pipeline = VoicePipeline()

    async def init(self):
        """初始化 pipeline（加载模型等耗时操作）"""
        await self._pipeline.init()

        # 注册回调
        self._pipeline.on_transcript(self._on_transcript)
        self._pipeline.on_speech_start(self._on_speech_start)

        logger.info(f"[{self._session_id}] 连接初始化完成")

    async def handle_message(self, msg):
        """处理 WebSocket 消息"""
        if msg.type == WSMsgType.BINARY:
            # 收到音频数据
            await self._pipeline.process_audio(msg.data)

        elif msg.type == WSMsgType.TEXT:
            # 收到控制指令
            data = json.loads(msg.data)
            await self._handle_control(data)

    async def _handle_control(self, data: dict):
        cmd = data.get('type')
        if cmd == 'start_session':
            self._pipeline.reset()
            await self._send_json({'type': 'session_started'})
        elif cmd == 'stop_session':
            await self._send_json({'type': 'session_stopped'})

    async def _on_transcript(self, text: str):
        """ASR 识别完成，发送给客户端"""
        await self._send_json({
            'type': 'transcript',
            'text': text,
            'is_final': True,
        })

    async def _on_speech_start(self):
        """语音开始，通知客户端（可以用于打断 TTS）"""
        await self._send_json({'type': 'speech_start'})

    async def _send_json(self, data: dict):
        try:
            await self._ws.send_json(data)
        except Exception as e:
            logger.warning(f"[{self._session_id}] 发送消息失败: {e}")

    async def cleanup(self):
        logger.info(f"[{self._session_id}] 连接关闭，清理资源")


# WebSocket 路由处理函数
async def ws_voice_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=64 * 1024)  # 64KB 消息大小限制
    await ws.prepare(request)

    session_id = request.headers.get('X-Session-ID', f"sess_{id(ws)}")
    manager = ConnectionManager(ws, session_id)

    try:
        await manager.init()
        await ws.send_json({'type': 'ready'})

        async for msg in ws:
            if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
            await manager.handle_message(msg)

    except Exception as e:
        logger.error(f"[{session_id}] 连接异常: {e}", exc_info=True)
    finally:
        await manager.cleanup()

    return ws
```

---

## 9.7 VAD 参数调优指南

参数调优没有万能答案，需要根据实际场景测试。以下是一套系统性的调优方法：

### 录制测试集

```python
# scripts/collect_vad_test_data.py
"""
收集真实用户的语音数据，用于测试 VAD 参数
"""

import asyncio
import wave
import numpy as np
from datetime import datetime

async def record_and_save(duration_sec: int = 30, output_dir: str = "test_audio"):
    """录制一段音频并保存，标注每句话的开始和结束时间"""
    import sounddevice as sd

    sample_rate = 16000
    audio = sd.rec(
        int(duration_sec * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype='int16'
    )

    print(f"开始录制 {duration_sec} 秒，请正常说话...")
    sd.wait()
    print("录制完成")

    filename = f"{output_dir}/test_{datetime.now().strftime('%H%M%S')}.wav"
    with wave.open(filename, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # int16 = 2 bytes
        f.setframerate(sample_rate)
        f.writeframes(audio.tobytes())

    print(f"已保存到 {filename}")
    return filename
```

### 评估脚本

```python
# scripts/evaluate_vad.py
"""
对比不同 VAD 参数配置的效果

评估指标：
- 漏检率（有语音但 VAD 没有触发）
- 误触发率（静音被判定为语音）
- 截断率（一句话被错误分割成两段）
- 延迟（从说完到 VAD 触发的时间）
"""

import asyncio
import numpy as np
import wave

from voicebot.vad.vad_manager import VADManager, VADConfig


async def evaluate_config(audio_file: str, config: VADConfig) -> dict:
    """评估一组 VAD 参数配置"""
    vad = VADManager(config)
    await vad.init()

    # 读取音频
    with wave.open(audio_file, 'r') as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    # 模拟流式处理
    segments = []
    for i in range(0, len(audio), config.chunk_samples):
        frame = audio[i:i + config.chunk_samples]
        if len(frame) < config.chunk_samples:
            frame = np.pad(frame, (0, config.chunk_samples - len(frame)))

        segment = await vad.process_chunk(frame)
        if segment:
            segments.append(segment)

    return {
        'num_segments': len(segments),
        'avg_duration_ms': np.mean([s.duration_ms for s in segments]) if segments else 0,
        'total_audio_ms': sum(s.duration_ms for s in segments),
        'segments': [(s.start_ms, s.end_ms, s.duration_ms) for s in segments],
    }


async def grid_search():
    """网格搜索最优参数"""
    test_file = "test_audio/test_sample.wav"

    configs = [
        VADConfig(silence_threshold_ms=400, speech_threshold=0.4),
        VADConfig(silence_threshold_ms=600, speech_threshold=0.5),
        VADConfig(silence_threshold_ms=800, speech_threshold=0.5),
        VADConfig(silence_threshold_ms=600, speech_threshold=0.6),
        VADConfig(silence_threshold_ms=1000, speech_threshold=0.5),
    ]

    print(f"{'静音阈值(ms)':<15} {'语音概率阈值':<15} {'段数':<8} {'平均时长(ms)':<15}")
    print("-" * 60)

    for cfg in configs:
        result = await evaluate_config(test_file, cfg)
        print(
            f"{cfg.silence_threshold_ms:<15} "
            f"{cfg.speech_threshold:<15} "
            f"{result['num_segments']:<8} "
            f"{result['avg_duration_ms']:<15.0f}"
        )


if __name__ == "__main__":
    asyncio.run(grid_search())
```

### 常见调优场景

```
场景 1：用户说话停顿较多（如老年人、思考时）
  问题：一句话被切成多段
  调整：增大 silence_threshold_ms（600 → 1000ms）

场景 2：嘈杂环境（咖啡厅、马路边）
  问题：背景噪声频繁触发 VAD
  调整：
    - 增大 speech_threshold（0.5 → 0.65）
    - 增大 min_speech_ms（200 → 400ms）
    - 增大 speech_trigger_frames（2 → 3）

场景 3：呼叫中心场景（用户说话较快，需要低延迟）
  问题：等待时间过长
  调整：减小 silence_threshold_ms（600 → 350ms）

场景 4：语音指令场景（"打开空调"、"播放音乐"，短句为主）
  问题：ASR 收到太多短噪声片段
  调整：增大 min_speech_ms（200 → 500ms）
```

---

## 9.8 服务端 VAD 作为兜底

如果客户端已经有了 VAD，服务端是否还需要？答案是：**是的，但职责不同**。

```
理想情况（客户端 VAD 正常工作）：
┌────────────┐  已裁剪的语音段  ┌────────────┐
│ 客户端 VAD  │ ──────────────▶ │ 服务端接收  │
└────────────┘                  └─────┬──────┘
                                      │ 直接送 ASR
                                      ▼
                                   ASR 处理

网络故障情况（客户端发来连续音频流）：
┌────────────┐  连续音频流     ┌────────────┐
│ 客户端 VAD  │ ──────────────▶ │ 服务端 VAD  │ → ASR
│ （已失效）  │                  │ （兜底）    │
└────────────┘                  └────────────┘
```

服务端可以根据客户端发来的消息类型来决定是否激活 VAD：

```python
# 客户端可以发送不同类型的音频帧
# type=raw：连续音频流，需要服务端 VAD
# type=segment：客户端已经 VAD 过的语音段，直接送 ASR

async def handle_message(self, msg):
    if msg.type == WSMsgType.TEXT:
        data = json.loads(msg.data)
        if data.get('type') == 'audio_mode':
            # 客户端告诉服务端它发的是什么类型
            mode = data.get('mode', 'raw')
            self._audio_mode = mode  # 'raw' 或 'segment'
            logger.info(f"音频模式切换为: {mode}")

    elif msg.type == WSMsgType.BINARY:
        if self._audio_mode == 'raw':
            # 连续流，需要服务端 VAD
            await self._pipeline.process_audio(msg.data)
        else:
            # 客户端已经做了 VAD，直接送 ASR
            await self._pipeline.send_to_asr(msg.data)
```

---

## 本章小结

本章实现了 VoiceBot 的服务端 VAD 模块：

- **为什么需要服务端 VAD**：网络抖动、客户端兼容性差异、统一决策权
- **FSMN-VAD**：FunASR 提供的工业级神经网络 VAD，准确率远超能量阈值法
- **流式状态机**：SILENCE → SPEECH → SILENCE 的状态转换，带预滚动缓冲
- **VADConfig 参数**：`silence_threshold_ms`（600ms）、`min_speech_ms`（200ms）、`speech_threshold`（0.5）是最关键的三个参数
- **VAD + ASR 解耦**：VAD 检测到语音后立即创建 asyncio.Task 送给 ASR，不阻塞继续监听
- **兜底策略**：客户端发来的是连续流还是已裁剪的片段，服务端根据模式决定是否激活 VAD

下一章，我们深入 ASR 本身——理解流式识别的原理，接入阿里云实时语音识别 API，并部署本地 SenseVoice 模型，让 VoiceBot 在网络不稳定或成本敏感的场景中也能稳定运行。
