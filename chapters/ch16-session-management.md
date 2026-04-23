# 第 16 章：Session 管理

## 多个用户同时打来电话，怎么办？

你把 VoiceBot 部署上线了。第一天，你的朋友 A 打开了网页，和 AI 聊起了天气。

然后朋友 B 也打开了。

朋友 A 正在问"北京今天冷不冷"，朋友 B 在问"推荐一部电影"。服务端需要同时处理两路 WebSocket 连接，各自维护各自的对话历史，各自的 TTS 队列，各自的 VAD 状态。

如果没有妥善的隔离机制，朋友 A 问天气，朋友 B 收到了天气回答——场面会很混乱。

这就是 **Session 管理**要解决的问题。

---

## 16.1 什么是 Session？

最简单的定义：

> **一次 WebSocket 连接 = 一个 Session**

用户打开浏览器、建立 WebSocket 连接时，Session 开始。用户关闭页面、断开连接时，Session 结束。

一个 Session 包含了这次对话的所有上下文：

```
Session
├── session_id         # 全局唯一标识符
├── websocket          # WebSocket 连接对象
├── created_at         # 创建时间
├── last_active_at     # 最后活跃时间
│
├── 对话状态
│   ├── conversation_history   # 和 LLM 的对话历史
│   ├── asr_buffer             # 当前 ASR 缓冲区
│   └── vad_state              # VAD 状态（用户是否在说话）
│
├── 任务队列
│   ├── tts_queue              # 待播放的 TTS 音频队列
│   ├── current_llm_task       # 当前 LLM 生成任务
│   └── current_tts_task       # 当前 TTS 合成任务
│
└── 配置
    └── user_config            # 用户个性化配置（音色、语速等）
```

---

## 16.2 Session 的生命周期

```
                    用户打开浏览器
                         │
                         ▼
              ┌─────────────────────┐
              │      创建 Session    │
              │  生成 session_id     │
              │  初始化所有状态      │
              └─────────┬───────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │      活跃 Session    │◄──────────┐
              │  处理音频流          │           │
              │  更新 last_active   │           │
              │  维护对话历史        │     用户继续说话
              └─────────┬───────────┘           │
                        │                       │
              ┌─────────┴─────────┐             │
              │                   │             │
         用户断开连接         超时未活跃          │
              │                   │             │
              ▼                   ▼             │
   ┌──────────────────┐  ┌──────────────────┐  │
   │    结束 Session   │  │    结束 Session   │  │
   │  (WebSocket关闭)  │  │  (超时清理)       │  │
   └────────┬─────────┘  └────────┬─────────┘  │
            │                     │
            └──────────┬──────────┘
                       │
                       ▼
           ┌───────────────────────┐
           │      清理资源          │
           │  取消所有异步任务       │
           │  释放内存              │
           │  记录日志              │
           └───────────────────────┘
```

这个生命周期设计有几个关键点：

1. **创建时立即初始化**：Session 一创建，所有状态都要就位，避免后续的空指针问题
2. **活跃时持续更新 last_active**：这是超时清理的依据
3. **结束时必须清理**：取消异步任务、释放队列，避免内存泄漏
4. **两种结束方式**：主动断开 vs 超时清理，都要走同一套清理逻辑

---

## 16.3 状态隔离的重要性

让我们看一个没有状态隔离时会发生什么的例子：

```python
# 危险！全局状态
conversation_history = []  # 所有用户共享同一个历史
tts_queue = asyncio.Queue()  # 所有用户共享同一个队列

async def handle_websocket(ws):
    # 用户 A 的问题
    conversation_history.append({"role": "user", "content": "今天天气怎样？"})
    # 用户 B 的问题也进来了
    conversation_history.append({"role": "user", "content": "推荐一部电影"})
    # LLM 收到的是两个问题混在一起的历史！
```

正确的做法是每个 Session 有自己独立的状态：

```
Session A                    Session B
┌─────────────────────┐      ┌─────────────────────┐
│ history:            │      │ history:             │
│  - user: 天气怎样   │      │  - user: 推荐电影    │
│  - ai: 今天晴天...  │      │  - ai: 我推荐...     │
│                     │      │                      │
│ tts_queue:          │      │ tts_queue:           │
│  [音频块1, 音频块2] │      │  [音频块A, 音频块B]  │
│                     │      │                      │
│ vad_state: SILENT   │      │ vad_state: SPEAKING  │
└─────────────────────┘      └─────────────────────┘
```

完全隔离，互不干扰。

---

## 16.4 Session ID 设计

Session ID 需要满足几个条件：

- **全局唯一**：不能两个 Session 用同一个 ID
- **不可猜测**：防止恶意用户用别人的 Session ID 发请求
- **可读性**：日志里看起来要清晰

推荐使用 UUID4：

```python
import uuid

session_id = str(uuid.uuid4())
# 输出: "f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

如果你想要更短、更易读的 ID，可以用前 8 位：

```python
session_id = str(uuid.uuid4())[:8]
# 输出: "f47ac10b"
```

但要注意，截短后碰撞概率会上升。在用户量不大的情况下（<10 万并发）问题不大，生产环境建议用完整 UUID。

还有一种方案是带时间戳前缀，方便按时间排序日志：

```python
import uuid
from datetime import datetime

def generate_session_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{timestamp}-{short_uuid}"
# 输出: "20241215143052-f47ac10b"
```

---

## 16.5 Session 类的完整实现

```python
# voicebot/session.py

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def generate_session_id() -> str:
    """生成全局唯一的 Session ID。"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{timestamp}-{short_uuid}"


@dataclass
class ConversationMessage:
    """对话历史中的单条消息。"""
    role: str          # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class Session:
    """
    表示一次完整的对话会话。

    一个 Session 对应一个 WebSocket 连接，封装了该连接的所有状态：
    对话历史、TTS 队列、VAD 状态、当前进行中的异步任务。
    """

    def __init__(self, websocket, system_prompt: str = "") -> None:
        self.session_id = generate_session_id()
        self.websocket = websocket
        self.created_at = datetime.now()
        self.last_active_at = datetime.now()

        # 对话状态
        self.conversation_history: list[ConversationMessage] = []
        self.system_prompt = system_prompt
        self.asr_buffer: list[bytes] = []       # 当前轮次的 ASR 音频缓冲
        self.vad_state: str = "silent"          # "silent" | "speaking"

        # TTS 音频队列（服务端往里放，发送协程从里取）
        self.tts_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        # 当前进行中的异步任务（用于打断时取消）
        self.current_llm_task: Optional[asyncio.Task] = None
        self.current_tts_task: Optional[asyncio.Task] = None

        # 是否已关闭
        self._closed = False

        logger.info(f"[{self.session_id}] Session 已创建")

    def touch(self) -> None:
        """更新最后活跃时间。每次收到用户消息时调用。"""
        self.last_active_at = datetime.now()

    def add_user_message(self, content: str) -> None:
        """添加用户消息到对话历史。"""
        self.touch()
        self.conversation_history.append(
            ConversationMessage(role="user", content=content)
        )
        logger.debug(f"[{self.session_id}] 用户消息: {content[:50]}...")

    def add_assistant_message(self, content: str) -> None:
        """添加 AI 回复到对话历史。"""
        self.conversation_history.append(
            ConversationMessage(role="assistant", content=content)
        )
        logger.debug(f"[{self.session_id}] AI 回复: {content[:50]}...")

    def get_llm_messages(self) -> list[dict]:
        """
        获取格式化的对话历史，供 LLM API 使用。
        返回 OpenAI 格式的消息列表。
        """
        messages = []

        # 加入 system prompt
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 加入对话历史
        for msg in self.conversation_history:
            messages.append({"role": msg.role, "content": msg.content})

        return messages

    def clear_asr_buffer(self) -> bytes:
        """清空 ASR 缓冲区并返回积累的音频数据。"""
        if not self.asr_buffer:
            return b""
        audio_data = b"".join(self.asr_buffer)
        self.asr_buffer = []
        return audio_data

    def append_asr_audio(self, audio_chunk: bytes) -> None:
        """向 ASR 缓冲区追加音频数据。"""
        self.asr_buffer.append(audio_chunk)

    async def cancel_current_tasks(self) -> None:
        """
        取消当前正在执行的 LLM 和 TTS 任务。
        用于打断处理和 Session 关闭时的清理。
        """
        tasks_to_cancel = []

        if self.current_llm_task and not self.current_llm_task.done():
            tasks_to_cancel.append(("LLM", self.current_llm_task))

        if self.current_tts_task and not self.current_tts_task.done():
            tasks_to_cancel.append(("TTS", self.current_tts_task))

        for task_name, task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"[{self.session_id}] {task_name} 任务已取消")

        self.current_llm_task = None
        self.current_tts_task = None

    async def drain_tts_queue(self) -> None:
        """清空 TTS 队列，丢弃所有待播放的音频。"""
        count = 0
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        if count > 0:
            logger.debug(f"[{self.session_id}] 已清空 TTS 队列，丢弃 {count} 个音频块")

    async def close(self) -> None:
        """
        关闭 Session，释放所有资源。
        可以安全地多次调用（幂等）。
        """
        if self._closed:
            return

        self._closed = True
        logger.info(f"[{self.session_id}] 开始关闭 Session...")

        # 取消所有进行中的任务
        await self.cancel_current_tasks()

        # 清空 TTS 队列
        await self.drain_tts_queue()

        # 发送终止信号给 TTS 发送协程（如果还在等待）
        try:
            self.tts_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # 清空对话历史（释放内存）
        self.conversation_history.clear()
        self.asr_buffer.clear()

        duration = (datetime.now() - self.created_at).total_seconds()
        logger.info(
            f"[{self.session_id}] Session 已关闭，"
            f"持续时间: {duration:.1f}s，"
            f"对话轮次: {len(self.conversation_history)}"
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def idle_seconds(self) -> float:
        """距离上次活跃已过去多少秒。"""
        return (datetime.now() - self.last_active_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"Session(id={self.session_id}, "
            f"vad={self.vad_state}, "
            f"turns={len(self.conversation_history)}, "
            f"idle={self.idle_seconds:.0f}s)"
        )
```

---

## 16.6 SessionManager：管理所有活跃 Session

单个 Session 解决了一个用户的问题。`SessionManager` 解决多用户并发的问题——它是所有 Session 的"登记处"。

```
SessionManager
├── sessions: Dict[str, Session]
│   ├── "20241215143052-f47ac10b" → Session A
│   ├── "20241215143118-a3b2c1d0" → Session B
│   └── "20241215143201-e9f8a7b6" → Session C
│
└── cleanup_task: asyncio.Task  # 后台定时清理超时 Session
```

```python
# voicebot/session_manager.py

import asyncio
import logging
from typing import Optional

from .session import Session

logger = logging.getLogger(__name__)


class SessionManager:
    """
    管理所有活跃的 Session。

    职责：
    1. Session 的注册和注销
    2. 按 session_id 查找 Session
    3. 定期清理超时的 Session（默认超时 30 分钟）
    """

    def __init__(
        self,
        session_timeout_seconds: int = 1800,  # 30 分钟
        cleanup_interval_seconds: int = 60,   # 每分钟检查一次
        system_prompt: str = "",
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._session_timeout = session_timeout_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._system_prompt = system_prompt
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动 SessionManager，开始后台清理任务。"""
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="session-cleanup"
        )
        logger.info("SessionManager 已启动")

    async def stop(self) -> None:
        """停止 SessionManager，关闭所有 Session。"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 关闭所有活跃 Session
        async with self._lock:
            session_ids = list(self._sessions.keys())

        for session_id in session_ids:
            await self.remove_session(session_id)

        logger.info("SessionManager 已停止")

    async def create_session(self, websocket) -> Session:
        """
        为新的 WebSocket 连接创建 Session。

        Args:
            websocket: WebSocket 连接对象

        Returns:
            新创建的 Session
        """
        session = Session(websocket, system_prompt=self._system_prompt)

        async with self._lock:
            self._sessions[session.session_id] = session

        logger.info(
            f"[{session.session_id}] 新 Session 已注册，"
            f"当前活跃 Session 数: {len(self._sessions)}"
        )
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """按 session_id 查找 Session。"""
        async with self._lock:
            return self._sessions.get(session_id)

    async def remove_session(self, session_id: str) -> None:
        """
        注销并关闭指定 Session。

        Args:
            session_id: 要关闭的 Session ID
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session:
            await session.close()
            logger.info(
                f"[{session_id}] Session 已注销，"
                f"剩余活跃 Session 数: {len(self._sessions)}"
            )

    async def _cleanup_loop(self) -> None:
        """
        后台清理循环：定期扫描并关闭超时的 Session。
        """
        logger.info(
            f"Session 清理任务已启动，"
            f"超时阈值: {self._session_timeout}s，"
            f"检查间隔: {self._cleanup_interval}s"
        )

        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                logger.info("Session 清理任务已停止")
                break
            except Exception as e:
                logger.error(f"Session 清理时发生错误: {e}", exc_info=True)
                # 出错后继续运行，不要让清理任务崩溃

    async def _cleanup_expired_sessions(self) -> None:
        """找出并关闭所有超时的 Session。"""
        expired_ids = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                if session.idle_seconds > self._session_timeout:
                    expired_ids.append(session_id)

        if expired_ids:
            logger.info(f"发现 {len(expired_ids)} 个超时 Session，开始清理...")
            for session_id in expired_ids:
                logger.info(f"[{session_id}] Session 超时，正在清理")
                await self.remove_session(session_id)

    @property
    def active_session_count(self) -> int:
        """当前活跃 Session 数量。"""
        return len(self._sessions)

    def get_stats(self) -> dict:
        """获取 SessionManager 的统计信息。"""
        sessions_info = []
        for session in self._sessions.values():
            sessions_info.append({
                "session_id": session.session_id,
                "idle_seconds": round(session.idle_seconds, 1),
                "conversation_turns": len(session.conversation_history),
                "vad_state": session.vad_state,
            })

        return {
            "active_sessions": len(self._sessions),
            "sessions": sessions_info,
        }
```

---

## 16.7 把 SessionManager 集成到 WebSocket 服务

现在把 `SessionManager` 接入 WebSocket 处理流程：

```python
# voicebot/server.py

import asyncio
import json
import logging

import websockets

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

# 全局 SessionManager 实例
session_manager = SessionManager(
    session_timeout_seconds=1800,
    system_prompt="你是一个友好的语音助手，回答要简洁，适合语音播放。",
)


async def handle_connection(websocket) -> None:
    """
    处理单个 WebSocket 连接的完整生命周期。
    """
    # 1. 创建 Session
    session = await session_manager.create_session(websocket)

    try:
        # 2. 发送欢迎消息
        await websocket.send(json.dumps({
            "type": "session_created",
            "session_id": session.session_id,
        }))

        # 3. 启动 TTS 发送协程
        tts_sender_task = asyncio.create_task(
            tts_sender(session),
            name=f"tts-sender-{session.session_id}"
        )

        # 4. 主消息处理循环
        async for message in websocket:
            await handle_message(session, message)

    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"[{session.session_id}] WebSocket 连接关闭: {e.code} {e.reason}")
    except Exception as e:
        logger.error(f"[{session.session_id}] 处理连接时发生错误: {e}", exc_info=True)
    finally:
        # 5. 无论如何都要清理 Session
        await session_manager.remove_session(session.session_id)
        if "tts_sender_task" in locals():
            tts_sender_task.cancel()
            try:
                await tts_sender_task
            except asyncio.CancelledError:
                pass


async def handle_message(session, raw_message) -> None:
    """处理来自客户端的单条消息。"""
    session.touch()  # 更新活跃时间

    try:
        if isinstance(raw_message, bytes):
            # 二进制消息 = 音频数据
            await handle_audio_chunk(session, raw_message)
        else:
            # 文本消息 = JSON 控制消息
            data = json.loads(raw_message)
            await handle_control_message(session, data)
    except json.JSONDecodeError:
        logger.warning(f"[{session.session_id}] 收到无效 JSON: {raw_message[:100]}")
    except Exception as e:
        logger.error(f"[{session.session_id}] 处理消息时发生错误: {e}", exc_info=True)


async def handle_audio_chunk(session, audio_chunk: bytes) -> None:
    """处理音频数据块（详见 ASR、VAD 相关章节）。"""
    session.append_asr_audio(audio_chunk)


async def handle_control_message(session, data: dict) -> None:
    """处理控制消息。"""
    msg_type = data.get("type")

    if msg_type == "vad_start":
        # 用户开始说话
        session.vad_state = "speaking"
        logger.debug(f"[{session.session_id}] VAD: 开始说话")

    elif msg_type == "vad_end":
        # 用户说完了，送去 ASR
        session.vad_state = "silent"
        audio_data = session.clear_asr_buffer()
        if audio_data:
            logger.info(
                f"[{session.session_id}] 收到完整语音，"
                f"大小: {len(audio_data)} bytes"
            )
            # 触发 ASR → LLM → TTS 流水线（详见后续章节）

    elif msg_type == "interrupt":
        # 用户打断 AI 的说话
        logger.info(f"[{session.session_id}] 收到打断信号")
        await session.cancel_current_tasks()
        await session.drain_tts_queue()

    else:
        logger.warning(f"[{session.session_id}] 未知消息类型: {msg_type}")


async def tts_sender(session) -> None:
    """
    从 TTS 队列中取出音频块，发送给客户端。
    这是一个独立的协程，和主处理循环并行运行。
    """
    logger.debug(f"[{session.session_id}] TTS 发送协程已启动")

    while True:
        try:
            # 等待 TTS 队列中有数据
            audio_chunk = await session.tts_queue.get()

            # None 是终止信号
            if audio_chunk is None:
                logger.debug(f"[{session.session_id}] TTS 发送协程收到终止信号")
                break

            # 发送音频数据给客户端
            await session.websocket.send(audio_chunk)

        except asyncio.CancelledError:
            logger.debug(f"[{session.session_id}] TTS 发送协程已取消")
            break
        except Exception as e:
            logger.error(
                f"[{session.session_id}] TTS 发送时发生错误: {e}",
                exc_info=True
            )
            break


async def main() -> None:
    """启动 WebSocket 服务器。"""
    await session_manager.start()

    async with websockets.serve(handle_connection, "0.0.0.0", 8765) as server:
        logger.info("VoiceBot 服务器已启动，监听端口 8765")
        await asyncio.Future()  # 永远运行

    await session_manager.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
```

---

## 16.8 内存管理：防止对话历史无限增长

一个用户如果和 VoiceBot 聊了 100 轮，对话历史会很长。发给 LLM 的 token 越来越多，既慢又贵。

解决方案：**滑动窗口**——只保留最近 N 轮对话。

```python
# 在 Session 类中添加

MAX_HISTORY_TURNS = 20  # 最多保留 20 轮对话

def add_user_message(self, content: str) -> None:
    """添加用户消息，并在必要时截断历史。"""
    self.touch()
    self.conversation_history.append(
        ConversationMessage(role="user", content=content)
    )
    self._trim_history()

def add_assistant_message(self, content: str) -> None:
    """添加 AI 回复，并在必要时截断历史。"""
    self.conversation_history.append(
        ConversationMessage(role="assistant", content=content)
    )
    self._trim_history()

def _trim_history(self) -> None:
    """
    保留最近 MAX_HISTORY_TURNS 轮对话。
    注意：一轮 = 一条 user 消息 + 一条 assistant 消息。
    所以最多保留 MAX_HISTORY_TURNS * 2 条消息。
    """
    max_messages = MAX_HISTORY_TURNS * 2
    if len(self.conversation_history) > max_messages:
        removed = len(self.conversation_history) - max_messages
        self.conversation_history = self.conversation_history[-max_messages:]
        logger.debug(
            f"[{self.session_id}] 历史截断，移除了最早的 {removed} 条消息"
        )
```

另一个方案是 **token 计数限制**，在 token 数超过阈值时截断：

```python
def _estimate_tokens(self) -> int:
    """粗略估算对话历史的 token 数（中文约 1.5 字/token）。"""
    total_chars = sum(len(msg.content) for msg in self.conversation_history)
    return int(total_chars / 1.5)

def _trim_history_by_tokens(self, max_tokens: int = 3000) -> None:
    """按 token 数限制截断历史。"""
    while self._estimate_tokens() > max_tokens and len(self.conversation_history) > 2:
        # 移除最早的一轮（user + assistant）
        self.conversation_history.pop(0)
        if self.conversation_history and self.conversation_history[0].role == "assistant":
            self.conversation_history.pop(0)
```

---

## 16.9 单元测试

```python
# tests/test_session.py

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from voicebot.session import Session
from voicebot.session_manager import SessionManager


class TestSession:

    def setup_method(self):
        """每个测试用例前创建一个 mock websocket 和 Session。"""
        self.ws = AsyncMock()
        self.session = Session(self.ws, system_prompt="你是一个助手")

    def test_session_id_is_unique(self):
        """每个 Session 的 ID 应该是唯一的。"""
        ws2 = AsyncMock()
        session2 = Session(ws2)
        assert self.session.session_id != session2.session_id

    def test_add_messages_builds_history(self):
        """添加消息后对话历史应该正确记录。"""
        self.session.add_user_message("你好")
        self.session.add_assistant_message("你好，有什么可以帮助你的？")

        assert len(self.session.conversation_history) == 2
        assert self.session.conversation_history[0].role == "user"
        assert self.session.conversation_history[1].role == "assistant"

    def test_get_llm_messages_includes_system_prompt(self):
        """获取 LLM 消息时应该包含 system prompt。"""
        self.session.add_user_message("你好")
        messages = self.session.get_llm_messages()

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "你是一个助手"
        assert messages[1]["role"] == "user"

    def test_clear_asr_buffer(self):
        """清空 ASR 缓冲区应该返回累积的音频数据。"""
        self.session.append_asr_audio(b"chunk1")
        self.session.append_asr_audio(b"chunk2")

        audio = self.session.clear_asr_buffer()

        assert audio == b"chunk1chunk2"
        assert self.session.asr_buffer == []

    def test_idle_seconds_increases_over_time(self):
        """idle_seconds 应该随时间增加。"""
        import time
        time.sleep(0.1)
        assert self.session.idle_seconds >= 0.1

    def test_touch_resets_idle_time(self):
        """touch() 应该重置 idle 时间。"""
        import time
        time.sleep(0.1)
        self.session.touch()
        assert self.session.idle_seconds < 0.05

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """多次调用 close() 应该是安全的。"""
        await self.session.close()
        await self.session.close()  # 不应该抛出异常
        assert self.session.is_closed

    @pytest.mark.asyncio
    async def test_cancel_tasks(self):
        """cancel_current_tasks 应该取消所有进行中的任务。"""
        async def long_running():
            await asyncio.sleep(100)

        self.session.current_llm_task = asyncio.create_task(long_running())
        self.session.current_tts_task = asyncio.create_task(long_running())

        await self.session.cancel_current_tasks()

        assert self.session.current_llm_task is None
        assert self.session.current_tts_task is None

    def test_history_trimming(self):
        """超过最大轮次时应该截断历史。"""
        from voicebot.session import MAX_HISTORY_TURNS

        # 添加超过限制的对话
        for i in range(MAX_HISTORY_TURNS + 5):
            self.session.add_user_message(f"问题 {i}")
            self.session.add_assistant_message(f"回答 {i}")

        max_messages = MAX_HISTORY_TURNS * 2
        assert len(self.session.conversation_history) <= max_messages


class TestSessionManager:

    @pytest.mark.asyncio
    async def test_create_and_remove_session(self):
        """创建和删除 Session 的基本流程。"""
        manager = SessionManager(session_timeout_seconds=60)
        await manager.start()

        ws = AsyncMock()
        session = await manager.create_session(ws)

        assert manager.active_session_count == 1
        assert await manager.get_session(session.session_id) is session

        await manager.remove_session(session.session_id)

        assert manager.active_session_count == 0
        assert await manager.get_session(session.session_id) is None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self):
        """超时 Session 应该被自动清理。"""
        manager = SessionManager(
            session_timeout_seconds=0,   # 立即超时
            cleanup_interval_seconds=1,
        )
        await manager.start()

        ws = AsyncMock()
        session = await manager.create_session(ws)
        session_id = session.session_id

        # 等待清理任务运行
        await asyncio.sleep(1.5)

        assert await manager.get_session(session_id) is None
        assert manager.active_session_count == 0

        await manager.stop()

    @pytest.mark.asyncio
    async def test_multiple_sessions_are_isolated(self):
        """多个 Session 的状态应该完全隔离。"""
        manager = SessionManager()
        await manager.start()

        ws_a = AsyncMock()
        ws_b = AsyncMock()
        session_a = await manager.create_session(ws_a)
        session_b = await manager.create_session(ws_b)

        session_a.add_user_message("用户A的问题")
        session_b.add_user_message("用户B的问题")

        # 两个 Session 的历史完全独立
        assert len(session_a.conversation_history) == 1
        assert len(session_b.conversation_history) == 1
        assert session_a.conversation_history[0].content == "用户A的问题"
        assert session_b.conversation_history[0].content == "用户B的问题"

        await manager.stop()
```

运行测试：

```bash
pytest tests/test_session.py -v
```

---

## 本章小结

本章我们构建了 VoiceBot 的"身份管理系统"：

- **Session 的核心概念**：一次 WebSocket 连接 = 一个 Session，封装了该连接的所有状态
- **生命周期管理**：创建 → 活跃（touch 更新时间） → 结束 → 清理，任何结束方式都走统一的清理逻辑
- **状态隔离**：每个 Session 有独立的对话历史、ASR 缓冲、TTS 队列、VAD 状态，多用户并发互不干扰
- **Session ID 设计**：时间戳 + UUID 前缀，兼顾唯一性和可读性
- **SessionManager**：统一管理所有 Session，后台定时清理超时 Session
- **内存管理**：滑动窗口截断对话历史，防止无限增长

**下一章预告**：我们已经有了所有的模块——VAD、ASR、LLM、TTS、Session 管理。是时候把它们全部串起来，跑通第一个完整的 VoiceBot 系统了。第 17 章将从零开始，一步步完成项目搭建、配置、启动，直到你能对着麦克风说话、听到 AI 回答。
