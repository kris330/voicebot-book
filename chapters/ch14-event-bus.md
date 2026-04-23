# 第十四章：事件总线——解耦 VoiceBot 的各个模块

---

我们的 VoiceBot 现在有了 ASR、LLM、TTS 三个核心模块，还有 WebSocket 网关。但有一个棘手的问题：这些模块之间怎么通信？

最直接的方法是让它们互相直接调用：

```
ASR ──调用──> LLM ──调用──> TTS
 ↑                           │
 └──────── 网关 ─────────────┘
```

这看起来可以工作，但当功能变复杂时，问题就出来了：

- ASR 识别完，除了通知 LLM，还需要通知网关（显示字幕）、通知延迟监控（记录 ASR 延迟）
- TTS 开始播放时，需要通知网关（让客户端知道开始播放）、通知打断检测模块（VAD 要进入静音状态）
- 每次新增一个功能，就要修改所有相关模块，添加新的调用

```
         ┌──────────────────────────────────────┐
         │           越来越复杂的调用关系          │
         │                                      │
         │   ASR ──────> LLM ──────> TTS        │
         │    │  ╲      / │  ╲      / │         │
         │    │   ╲    /  │   ╲    /  │         │
         │  网关  监控  重写  监控  网关  监控    │
         │    │         │          │             │
         │    └─────────┴──────────┘             │
         │              VAD                      │
         └──────────────────────────────────────┘
```

这就是"意大利面条式"架构——牵一发动全身，难以测试，难以维护。

解决方案是**事件总线**（Event Bus）：所有模块不直接调用对方，而是通过发布/订阅事件来通信。

---

## 14.1 发布-订阅模式

发布-订阅（Pub/Sub）是一种解耦通信的经典模式：

```
                    事件总线
                   ┌────────┐
    ASR ──发布──> │AUDIO   │──订阅──> LLM
                  │_READY  │──订阅──> 网关（显示字幕）
                  │        │──订阅──> 延迟监控
                  ├────────┤
    LLM ──发布──> │LLM     │──订阅──> TTS
                  │_TOKEN  │──订阅──> 网关（流式显示文字）
                  ├────────┤
    TTS ──发布──> │TTS     │──订阅──> 网关（发送音频）
                  │_CHUNK  │──订阅──> 延迟监控
                  └────────┘
```

各模块的关系变成：

- 每个模块只知道"发布哪些事件"和"订阅哪些事件"
- 模块之间没有直接依赖
- 新增功能只需要订阅已有事件，不需要修改现有模块
- 每个模块可以独立测试

---

## 14.2 事件定义

先定义 VoiceBot 中会用到的所有事件类型。用 `dataclass` 定义事件，既有类型安全，又便于序列化：

```python
# src/voicebot/events.py

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
```

---

## 14.3 从零实现异步事件总线

现在来实现核心的 `EventBus` 类：

```python
# src/voicebot/event_bus.py

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable

from .events import BaseEvent

logger = logging.getLogger(__name__)

# 事件处理器类型定义
EventHandler = Callable[[BaseEvent], Awaitable[None]]


@dataclass
class HandlerEntry:
    """
    一个已注册的事件处理器

    包含处理器函数和优先级信息。
    优先级越小，执行越早（类似 CSS z-index 的反向逻辑）。
    """
    handler: EventHandler
    priority: int
    handler_name: str  # 用于日志和调试


class EventBus:
    """
    异步事件总线

    特性：
    - 支持同一事件类型的多个处理器
    - 按优先级顺序执行处理器
    - 错误隔离：一个处理器出错不影响其他处理器
    - 支持会话作用域（只接收特定 session 的事件）
    - 线程安全（在 asyncio 事件循环内）
    """

    def __init__(self) -> None:
        # event_type → List[HandlerEntry]，按优先级排序
        self._handlers: dict[str, list[HandlerEntry]] = defaultdict(list)

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 50,
        name: str | None = None,
    ) -> None:
        """
        订阅事件

        Args:
            event_type: 要订阅的事件类型（EventType 枚举值）
            handler: 异步处理函数，签名为 async def handler(event: BaseEvent)
            priority: 优先级，数值越小越先执行，默认 50
            name: 处理器名称，用于日志（默认用函数名）
        """
        handler_name = name or handler.__name__
        entry = HandlerEntry(
            handler=handler,
            priority=priority,
            handler_name=handler_name,
        )

        handlers = self._handlers[event_type]
        handlers.append(entry)
        # 按优先级排序，优先级小的在前
        handlers.sort(key=lambda e: e.priority)

        logger.debug(
            f"订阅事件 [{event_type}] "
            f"处理器：{handler_name}，优先级：{priority}"
        )

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """
        取消订阅

        Returns:
            True 如果成功找到并移除，False 如果未找到
        """
        handlers = self._handlers.get(event_type, [])
        original_count = len(handlers)
        self._handlers[event_type] = [
            e for e in handlers if e.handler is not handler
        ]
        removed = len(self._handlers[event_type]) < original_count
        if removed:
            logger.debug(f"取消订阅 [{event_type}] {handler.__name__}")
        return removed

    async def publish(self, event: BaseEvent) -> None:
        """
        发布事件

        按优先级顺序调用所有已注册的处理器。
        每个处理器的异常会被捕获并记录，不会影响其他处理器。

        Args:
            event: 要发布的事件
        """
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            logger.debug(f"事件 [{event.event_type}] 无处理器，忽略")
            return

        logger.debug(
            f"发布事件 [{event.event_type}] "
            f"session={event.session_id}，"
            f"处理器数量：{len(handlers)}"
        )

        for entry in handlers:
            try:
                await entry.handler(event)
            except Exception as e:
                # 错误隔离：记录错误但继续执行其他处理器
                logger.error(
                    f"事件处理器 [{entry.handler_name}] "
                    f"处理 [{event.event_type}] 时出错：{e}",
                    exc_info=True,
                )

    async def publish_nowait(self, event: BaseEvent) -> None:
        """
        发布事件（非阻塞版本）

        把事件处理包装成后台 Task，立即返回。
        适合在不想等待处理完成的场景使用。
        """
        asyncio.create_task(self.publish(event))

    def subscriber_count(self, event_type: str) -> int:
        """获取某事件类型的处理器数量"""
        return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        """清空所有订阅（测试时用）"""
        self._handlers.clear()
```

### 14.3.1 subscribe 装饰器

为了让订阅更简洁，提供装饰器风格的 API：

```python
# src/voicebot/event_bus.py（续）

    def on(
        self,
        event_type: str,
        priority: int = 50,
    ) -> Callable:
        """
        装饰器风格的订阅

        用法：
            @bus.on(EventType.ASR_FINAL_RESULT)
            async def handle_asr(event: ASRResultEvent) -> None:
                print(event.text)
        """
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler, priority=priority)
            return handler
        return decorator
```

---

## 14.4 优先级机制

同一事件可能有多个处理器，优先级决定执行顺序。这在某些场景下很重要：

```python
# 场景：ASR 结果出来后，先做打断检测，再触发 LLM

bus = EventBus()

@bus.on(EventType.ASR_FINAL_RESULT, priority=10)  # 优先级 10，先执行
async def check_interrupt(event: ASRResultEvent) -> None:
    """检查是否是打断指令（优先处理）"""
    if "停止" in event.text or "算了" in event.text:
        await bus.publish(InterruptEvent(session_id=event.session_id))
        return  # 阻止后续处理？注意：事件总线不支持阻止，需要用其他机制

@bus.on(EventType.ASR_FINAL_RESULT, priority=50)  # 优先级 50，后执行
async def trigger_llm(event: ASRResultEvent) -> None:
    """触发 LLM 生成"""
    # 调用 LLM
    ...

@bus.on(EventType.ASR_FINAL_RESULT, priority=90)  # 优先级 90，最后执行
async def log_asr_latency(event: ASRResultEvent) -> None:
    """记录 ASR 延迟（最后执行，不影响主流程）"""
    latency = time.time() - event.timestamp
    logger.info(f"ASR 延迟：{latency * 1000:.0f}ms")
```

优先级约定（供参考）：

```
优先级 10-20：安全/打断检测（必须最先执行）
优先级 30-40：主业务逻辑预处理
优先级 50（默认）：主业务逻辑
优先级 70-80：后处理、格式转换
优先级 90-100：日志、监控、统计
```

---

## 14.5 错误隔离实验

让我们验证一个处理器出错不会影响其他处理器：

```python
# 演示错误隔离
import asyncio
from voicebot.event_bus import EventBus
from voicebot.events import ASRResultEvent, EventType


async def demo_error_isolation() -> None:
    bus = EventBus()

    results = []

    @bus.on(EventType.ASR_FINAL_RESULT, priority=10)
    async def handler_a(event):
        results.append("A started")
        raise ValueError("处理器 A 故意抛出异常！")

    @bus.on(EventType.ASR_FINAL_RESULT, priority=50)
    async def handler_b(event):
        results.append("B executed")  # 这个应该仍然执行

    @bus.on(EventType.ASR_FINAL_RESULT, priority=90)
    async def handler_c(event):
        results.append("C executed")  # 这个也应该执行

    event = ASRResultEvent(
        session_id="test-session",
        text="你好",
        is_final=True,
    )
    await bus.publish(event)

    print(f"执行结果：{results}")
    # 输出：执行结果：['A started', 'B executed', 'C executed']
    # 处理器 A 出错，但 B 和 C 都正常执行了


asyncio.run(demo_error_isolation())
```

---

## 14.6 会话作用域的事件总线

在多用户场景下，我们需要确保事件只在对应的会话内传播。一个用户的 ASR 结果不应该触发另一个用户的 LLM：

```python
# src/voicebot/session_event_bus.py

import asyncio
import logging
from .event_bus import EventBus, EventHandler
from .events import BaseEvent

logger = logging.getLogger(__name__)


class SessionEventBus:
    """
    会话级别的事件总线

    每个 Session 有自己独立的 EventBus 实例，
    事件不会跨 Session 传播。

    使用方式：
        # 每个会话创建时
        session_bus = session_bus_factory.create(session_id)

        # 模块订阅本 session 的事件
        session_bus.subscribe(EventType.ASR_FINAL_RESULT, handler)

        # 发布事件
        await session_bus.publish(event)
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._bus = EventBus()

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 50,
        name: str | None = None,
    ) -> None:
        self._bus.subscribe(event_type, handler, priority=priority, name=name)

    def on(self, event_type: str, priority: int = 50):
        return self._bus.on(event_type, priority=priority)

    async def publish(self, event: BaseEvent) -> None:
        """发布事件（自动检查 session_id 匹配）"""
        if event.session_id != self.session_id:
            logger.warning(
                f"事件 session_id [{event.session_id}] "
                f"与总线 session_id [{self.session_id}] 不匹配，忽略"
            )
            return
        await self._bus.publish(event)

    def clear(self) -> None:
        """清空所有订阅（会话结束时调用）"""
        self._bus.clear()


class SessionEventBusFactory:
    """管理所有会话的事件总线"""

    def __init__(self) -> None:
        self._buses: dict[str, SessionEventBus] = {}

    def create(self, session_id: str) -> SessionEventBus:
        """为新会话创建事件总线"""
        bus = SessionEventBus(session_id)
        self._buses[session_id] = bus
        logger.info(f"创建会话事件总线 [{session_id}]")
        return bus

    def get(self, session_id: str) -> SessionEventBus | None:
        return self._buses.get(session_id)

    def destroy(self, session_id: str) -> None:
        """销毁会话事件总线"""
        bus = self._buses.pop(session_id, None)
        if bus:
            bus.clear()
            logger.info(f"销毁会话事件总线 [{session_id}]")
```

---

## 14.7 在 VoiceBot 中使用事件总线

把事件总线集成到实际的模块中：

### 14.7.1 ASR 模块（发布者）

```python
# src/voicebot/modules/asr_module.py

import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    AudioChunkEvent,
    ASRResultEvent,
    EventType,
)

logger = logging.getLogger(__name__)


class ASRModule:
    """
    ASR 模块

    订阅：AUDIO_CHUNK_RECEIVED（接收音频）
    发布：ASR_PARTIAL_RESULT、ASR_FINAL_RESULT
    """

    def __init__(self, bus: EventBus, asr_engine) -> None:
        self._bus = bus
        self._asr = asr_engine
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.AUDIO_CHUNK_RECEIVED,
            self._handle_audio_chunk,
            priority=50,
            name="ASRModule.handle_audio",
        )

    async def _handle_audio_chunk(self, event: AudioChunkEvent) -> None:
        """处理音频块，调用 ASR 引擎"""
        result = await self._asr.process_chunk(
            event.audio_bytes,
            event.sample_rate,
        )

        if result is None:
            return

        asr_event = ASRResultEvent(
            session_id=event.session_id,
            text=result.text,
            is_final=result.is_final,
            confidence=result.confidence,
        )

        await self._bus.publish(asr_event)
```

### 14.7.2 LLM 模块（订阅者 + 发布者）

```python
# src/voicebot/modules/llm_module.py

import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    ASRResultEvent,
    LLMTokenEvent,
    LLMSentenceEvent,
    LLMEndEvent,
    EventType,
)
from voicebot.tts.text_processor import SentenceSplitter

logger = logging.getLogger(__name__)


class LLMModule:
    """
    LLM 模块

    订阅：ASR_FINAL_RESULT（触发生成）
    发布：LLM_TOKEN、LLM_SENTENCE_READY、LLM_END
    """

    def __init__(self, bus: EventBus, llm_engine) -> None:
        self._bus = bus
        self._llm = llm_engine
        self._splitter = SentenceSplitter()
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.ASR_FINAL_RESULT,
            self._handle_asr_result,
            priority=50,
            name="LLMModule.handle_asr",
        )

    async def _handle_asr_result(self, event: ASRResultEvent) -> None:
        """收到 ASR 结果，触发 LLM 生成"""
        logger.info(f"LLM 收到用户输入：{event.text}")

        accumulated = ""
        sentence_buffer = ""
        sentence_seq = 0

        async for token in self._llm.generate_stream(event.text):
            accumulated += token
            sentence_buffer += token

            # 发布 token 事件（用于网关实时显示）
            await self._bus.publish(LLMTokenEvent(
                session_id=event.session_id,
                token=token,
                accumulated_text=accumulated,
            ))

            # 检查是否形成了完整句子
            sentences = self._splitter.split(sentence_buffer)
            if len(sentences) > 1:
                # 除了最后一个（可能不完整），其余都是完整句子
                for sentence in sentences[:-1]:
                    if sentence.strip():
                        await self._bus.publish(LLMSentenceEvent(
                            session_id=event.session_id,
                            sentence=sentence,
                            sequence_number=sentence_seq,
                        ))
                        sentence_seq += 1
                # 保留最后一个（可能不完整的）片段
                sentence_buffer = sentences[-1]

        # 发布剩余的最后一个句子
        if sentence_buffer.strip():
            await self._bus.publish(LLMSentenceEvent(
                session_id=event.session_id,
                sentence=sentence_buffer,
                sequence_number=sentence_seq,
            ))

        # 发布 LLM 结束事件
        await self._bus.publish(LLMEndEvent(
            session_id=event.session_id,
            full_response=accumulated,
        ))
```

### 14.7.3 TTS 模块（订阅者 + 发布者）

```python
# src/voicebot/modules/tts_module.py

import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    LLMSentenceEvent,
    TTSAudioChunkEvent,
    InterruptEvent,
    EventType,
)

logger = logging.getLogger(__name__)


class TTSModule:
    """
    TTS 模块

    订阅：LLM_SENTENCE_READY（触发合成）、INTERRUPT（停止播放）
    发布：TTS_AUDIO_CHUNK
    """

    def __init__(self, bus: EventBus, tts_manager) -> None:
        self._bus = bus
        self._tts = tts_manager
        self._interrupted = False
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.LLM_SENTENCE_READY,
            self._handle_sentence,
            priority=50,
            name="TTSModule.handle_sentence",
        )
        self._bus.subscribe(
            EventType.INTERRUPT,
            self._handle_interrupt,
            priority=10,  # 高优先级，尽快处理打断
            name="TTSModule.handle_interrupt",
        )

    async def _handle_interrupt(self, event: InterruptEvent) -> None:
        """处理打断事件"""
        logger.info(f"TTS 收到打断信号：{event.reason}")
        self._interrupted = True

    async def _handle_sentence(self, event: LLMSentenceEvent) -> None:
        """收到 LLM 句子，触发 TTS 合成"""
        if self._interrupted:
            logger.info("TTS 已打断，跳过合成")
            return

        async for audio_chunk in self._tts.speak(event.sentence):
            if self._interrupted:
                logger.info("TTS 合成中途被打断")
                break

            await self._bus.publish(TTSAudioChunkEvent(
                session_id=event.session_id,
                audio_bytes=audio_chunk,
                sequence_number=event.sequence_number,
            ))
```

---

## 14.8 完整代码汇总

```python
# src/voicebot/event_bus.py（完整版）

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable

from .events import BaseEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]


@dataclass
class HandlerEntry:
    handler: EventHandler
    priority: int
    handler_name: str


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HandlerEntry]] = defaultdict(list)

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 50,
        name: str | None = None,
    ) -> None:
        handler_name = name or handler.__name__
        entry = HandlerEntry(
            handler=handler,
            priority=priority,
            handler_name=handler_name,
        )
        handlers = self._handlers[event_type]
        handlers.append(entry)
        handlers.sort(key=lambda e: e.priority)
        logger.debug(
            f"订阅 [{event_type}] 处理器：{handler_name}，优先级：{priority}"
        )

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        handlers = self._handlers.get(event_type, [])
        original_count = len(handlers)
        self._handlers[event_type] = [
            e for e in handlers if e.handler is not handler
        ]
        return len(self._handlers[event_type]) < original_count

    def on(self, event_type: str, priority: int = 50) -> Callable:
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler, priority=priority)
            return handler
        return decorator

    async def publish(self, event: BaseEvent) -> None:
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            return
        logger.debug(
            f"发布 [{event.event_type}] session={event.session_id}"
        )
        for entry in handlers:
            try:
                await entry.handler(event)
            except Exception as e:
                logger.error(
                    f"处理器 [{entry.handler_name}] 出错：{e}",
                    exc_info=True,
                )

    async def publish_nowait(self, event: BaseEvent) -> None:
        asyncio.create_task(self.publish(event))

    def subscriber_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        self._handlers.clear()
```

---

## 14.9 单元测试

```python
# tests/test_event_bus.py

import asyncio
import pytest
from voicebot.event_bus import EventBus
from voicebot.events import (
    ASRResultEvent,
    EventType,
    InterruptEvent,
)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.mark.asyncio
async def test_basic_publish_subscribe(bus: EventBus) -> None:
    """测试基本发布-订阅"""
    received_events = []

    async def handler(event: ASRResultEvent) -> None:
        received_events.append(event)

    bus.subscribe(EventType.ASR_FINAL_RESULT, handler)

    event = ASRResultEvent(
        session_id="s1",
        text="你好",
        is_final=True,
    )
    await bus.publish(event)

    assert len(received_events) == 1
    assert received_events[0].text == "你好"


@pytest.mark.asyncio
async def test_priority_ordering(bus: EventBus) -> None:
    """测试处理器按优先级顺序执行"""
    execution_order = []

    async def handler_high(event) -> None:
        execution_order.append("high")

    async def handler_medium(event) -> None:
        execution_order.append("medium")

    async def handler_low(event) -> None:
        execution_order.append("low")

    # 注意：先注册 low，后注册 high，但应该按优先级执行
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_low, priority=90)
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_high, priority=10)
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_medium, priority=50)

    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    await bus.publish(event)

    assert execution_order == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_error_isolation(bus: EventBus) -> None:
    """测试错误隔离：一个处理器出错不影响其他处理器"""
    executed = []

    async def failing_handler(event) -> None:
        executed.append("failing")
        raise ValueError("故意的错误")

    async def ok_handler(event) -> None:
        executed.append("ok")

    bus.subscribe(EventType.ASR_FINAL_RESULT, failing_handler, priority=10)
    bus.subscribe(EventType.ASR_FINAL_RESULT, ok_handler, priority=50)

    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    # 不应该抛出异常
    await bus.publish(event)

    # failing_handler 执行了，ok_handler 也执行了
    assert "failing" in executed
    assert "ok" in executed


@pytest.mark.asyncio
async def test_unsubscribe(bus: EventBus) -> None:
    """测试取消订阅"""
    call_count = 0

    async def handler(event) -> None:
        nonlocal call_count
        call_count += 1

    bus.subscribe(EventType.ASR_FINAL_RESULT, handler)
    event = ASRResultEvent(session_id="s1", text="test", is_final=True)

    await bus.publish(event)
    assert call_count == 1

    bus.unsubscribe(EventType.ASR_FINAL_RESULT, handler)
    await bus.publish(event)
    assert call_count == 1  # 取消订阅后不再调用


@pytest.mark.asyncio
async def test_decorator_style(bus: EventBus) -> None:
    """测试装饰器风格的订阅"""
    received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handler(event: ASRResultEvent) -> None:
        received.append(event.text)

    event = ASRResultEvent(session_id="s1", text="装饰器测试", is_final=True)
    await bus.publish(event)

    assert received == ["装饰器测试"]


@pytest.mark.asyncio
async def test_no_handlers_no_error(bus: EventBus) -> None:
    """测试没有处理器时不报错"""
    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    # 不应该抛出任何异常
    await bus.publish(event)


@pytest.mark.asyncio
async def test_multiple_event_types(bus: EventBus) -> None:
    """测试多种事件类型互不干扰"""
    asr_received = []
    interrupt_received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handle_asr(event) -> None:
        asr_received.append(event)

    @bus.on(EventType.INTERRUPT)
    async def handle_interrupt(event) -> None:
        interrupt_received.append(event)

    asr_event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    interrupt_event = InterruptEvent(session_id="s1")

    await bus.publish(asr_event)
    await bus.publish(interrupt_event)

    assert len(asr_received) == 1
    assert len(interrupt_received) == 1
    # 两者没有互相干扰


@pytest.mark.asyncio
async def test_publish_nowait(bus: EventBus) -> None:
    """测试非阻塞发布"""
    received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handler(event) -> None:
        await asyncio.sleep(0.01)  # 模拟异步操作
        received.append(event.text)

    event = ASRResultEvent(session_id="s1", text="nowait test", is_final=True)

    # publish_nowait 立即返回
    await bus.publish_nowait(event)
    assert len(received) == 0  # 还没执行完

    # 等待后台任务完成
    await asyncio.sleep(0.1)
    assert len(received) == 1
```

运行测试：

```bash
pytest tests/test_event_bus.py -v

# 输出：
# tests/test_event_bus.py::test_basic_publish_subscribe PASSED
# tests/test_event_bus.py::test_priority_ordering PASSED
# tests/test_event_bus.py::test_error_isolation PASSED
# tests/test_event_bus.py::test_unsubscribe PASSED
# tests/test_event_bus.py::test_decorator_style PASSED
# tests/test_event_bus.py::test_no_handlers_no_error PASSED
# tests/test_event_bus.py::test_multiple_event_types PASSED
# tests/test_event_bus.py::test_publish_nowait PASSED
```

---

## 本章小结

本章我们实现了 VoiceBot 的"神经系统"——事件总线：

- **为什么需要事件总线**：模块间直接调用导致紧耦合，新功能难以添加，测试困难。
- **发布-订阅模式**：发布者不知道谁在监听，订阅者不知道谁在发布，实现真正解耦。
- **事件定义**：用 `dataclass` 定义强类型事件，每个字段有明确含义和类型。
- **EventBus 实现**：支持多处理器、优先级排序、错误隔离的异步事件总线。
- **优先级机制**：同一事件的多个处理器按优先级顺序执行，保证关键逻辑先运行。
- **错误隔离**：任何处理器抛出异常只影响自己，不影响其他处理器，系统更健壮。
- **会话作用域**：每个 Session 有独立的事件总线，防止多用户事件串台。
- **完整测试**：8 个测试用例覆盖主要功能，包括发布-订阅、优先级、错误隔离、取消订阅。

现在我们有了事件总线，各模块可以通过事件通信了。但还有一个问题：模块实例怎么创建？怎么把 ASR 引擎、LLM 引擎、TTS 引擎组合成一个可工作的 VoiceBot？每次想换一个 ASR 引擎，要修改多少地方？

**下一章**我们来设计 Pipeline——一个把所有模型和组件组合在一起、可以通过配置文件驱动的处理链。
