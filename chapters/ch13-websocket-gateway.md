# 第十三章：WebSocket 网关——VoiceBot 的通信核心

---

上一章我们让 VoiceBot 能说话了。但说给谁听呢？

浏览器里的用户说了一句话，音频数据要传到服务器，服务器处理完之后，合成的音频又要传回去给浏览器播放。这个双向实时通道，就是 WebSocket 要解决的问题。

HTTP 请求是单向的：客户端问，服务器答，然后连接关闭。而语音对话需要持续的双向通信——用户在说话的同时，服务器可能在推送上一句话的 TTS 音频。HTTP 做不到这个，WebSocket 可以。

本章我们用 FastAPI 从零搭建一个完整的 WebSocket 网关，处理连接管理、消息路由、心跳检测和优雅关闭。

---

## 13.1 WebSocket 基础：为什么不用 HTTP

先看看 HTTP 和 WebSocket 的本质区别：

```
HTTP 请求-响应模式：

客户端                    服务器
  │                         │
  │──── POST /audio ────────>│
  │                         │ (处理中...)
  │<─── 200 OK + 音频 ───────│
  │                         │
  连接关闭                   │

问题：服务器不能主动推送；每次都要建立新连接


WebSocket 全双工模式：

客户端                    服务器
  │                         │
  │──── WebSocket 握手 ─────>│
  │<─── 握手确认 ────────────│
  │                         │
  │──── 音频数据 ────────────>│ ← 用户说话
  │<─── ASR 文字 ────────────│ ← 实时识别结果
  │<─── TTS 音频块 1 ─────────│ ← 开始播放
  │<─── TTS 音频块 2 ─────────│
  │──── 音频数据 ────────────>│ ← 用户继续说
  │<─── TTS 音频块 3 ─────────│
  │                         │
  连接保持，随时收发          │
```

WebSocket 的优势：
1. **双向通信**：服务器可以主动推送数据
2. **低延迟**：无需每次重新建立连接
3. **轻量帧头**：比 HTTP 头部小很多，适合频繁小消息
4. **原生支持二进制**：音频数据不需要 Base64 编码

---

## 13.2 FastAPI WebSocket 基础

FastAPI 内置了 WebSocket 支持，用起来很简洁。先看最简单的例子：

```python
# 最简单的 WebSocket 服务器
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()  # 接受连接

    try:
        while True:
            # 接收消息（文本或二进制）
            data = await websocket.receive_text()
            # 发送回复
            await websocket.send_text(f"Echo: {data}")
    except Exception:
        pass  # 连接断开
```

这个例子能工作，但有很多问题：
- 没有连接管理（无法追踪所有连接）
- 没有消息路由（不同类型的消息怎么分发？）
- 没有心跳（连接可能悄悄断开）
- 没有会话绑定（怎么知道这个连接对应哪个用户？）

我们需要一个更完整的设计。

---

## 13.3 消息协议设计

在动手写代码之前，先定义好消息格式。清晰的协议是后续一切的基础。

### 13.3.1 消息类型

```
客户端 → 服务器：
┌─────────────────┬──────────────────────────────────┐
│ 消息类型         │ 说明                              │
├─────────────────┼──────────────────────────────────┤
│ audio_chunk     │ 原始音频数据（二进制帧）            │
│ config          │ 会话配置（JSON 文本帧）             │
│ ping            │ 心跳 ping（JSON 文本帧）           │
│ interrupt       │ 打断当前 TTS 播放（JSON 文本帧）    │
└─────────────────┴──────────────────────────────────┘

服务器 → 客户端：
┌─────────────────┬──────────────────────────────────┐
│ 消息类型         │ 说明                              │
├─────────────────┼──────────────────────────────────┤
│ asr_result      │ ASR 识别结果（JSON 文本帧）         │
│ llm_token       │ LLM 生成的 token（JSON 文本帧）     │
│ tts_audio       │ TTS 合成的音频块（二进制帧）         │
│ tts_start       │ TTS 开始播放通知（JSON 文本帧）     │
│ tts_end         │ TTS 播放结束通知（JSON 文本帧）     │
│ pong            │ 心跳 pong（JSON 文本帧）           │
│ error           │ 错误通知（JSON 文本帧）             │
└─────────────────┴──────────────────────────────────┘
```

### 13.3.2 消息格式定义

```python
# src/voicebot/gateway/messages.py

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
```

---

## 13.4 连接生命周期管理

每个 WebSocket 连接都有自己的生命周期：

```
连接建立                处理阶段              连接关闭
    │                      │                     │
    ▼                      ▼                     ▼
┌────────┐    ┌────────────────────────┐    ┌─────────┐
│ ACCEPT │───>│  ACTIVE (收发消息)      │───>│  CLOSE  │
└────────┘    │  ┌──────────────────┐  │    └─────────┘
              │  │ 接收音频数据      │  │
              │  │ ASR 识别         │  │
              │  │ LLM 生成         │  │
              │  │ TTS 合成         │  │
              │  │ 发送音频         │  │
              │  └──────────────────┘  │
              └────────────────────────┘
                            │
                     ┌──────┴──────┐
                     │             │
                  正常关闭      异常断开
                  (客户端主动)  (网络故障/超时)
```

```python
# src/voicebot/gateway/connection.py

import asyncio
import time
import uuid
import logging
from enum import Enum
from dataclasses import dataclass, field
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    CONNECTING = "connecting"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class Connection:
    """
    代表一个 WebSocket 连接

    每个连接有唯一 ID，绑定一个会话
    """
    websocket: WebSocket
    connection_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    session_id: str | None = None
    state: ConnectionState = ConnectionState.CONNECTING
    created_at: float = field(default_factory=time.time)
    last_ping_at: float = field(default_factory=time.time)
    last_pong_at: float = field(default_factory=time.time)

    # 用于取消各种后台任务
    _tasks: list[asyncio.Task] = field(default_factory=list)

    def is_alive(self) -> bool:
        """检查连接是否还活着"""
        return self.state == ConnectionState.ACTIVE

    def mark_ping(self) -> None:
        self.last_ping_at = time.time()

    def mark_pong(self) -> None:
        self.last_pong_at = time.time()

    def pong_timeout(self, timeout_seconds: float = 30.0) -> bool:
        """判断心跳是否超时"""
        return (time.time() - self.last_pong_at) > timeout_seconds

    def register_task(self, task: asyncio.Task) -> None:
        """注册后台任务（关闭时统一取消）"""
        self._tasks.append(task)

    def cancel_tasks(self) -> None:
        """取消所有后台任务"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
```

---

## 13.5 连接管理器

管理所有活跃连接的容器：

```python
# src/voicebot/gateway/connection_manager.py

import asyncio
import logging
from typing import Iterator

from .connection import Connection, ConnectionState
from .messages import ServerMessage

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    管理所有 WebSocket 连接

    线程安全注意：所有操作都在 asyncio 事件循环中执行，
    不需要额外的锁。
    """

    def __init__(self) -> None:
        self._connections: dict[str, Connection] = {}

    def add(self, conn: Connection) -> None:
        """添加新连接"""
        self._connections[conn.connection_id] = conn
        conn.state = ConnectionState.ACTIVE
        logger.info(
            f"连接建立 [{conn.connection_id}]，"
            f"当前活跃连接数：{len(self._connections)}"
        )

    def remove(self, connection_id: str) -> Connection | None:
        """移除连接"""
        conn = self._connections.pop(connection_id, None)
        if conn:
            conn.state = ConnectionState.CLOSED
            conn.cancel_tasks()
            logger.info(
                f"连接移除 [{connection_id}]，"
                f"剩余活跃连接数：{len(self._connections)}"
            )
        return conn

    def get(self, connection_id: str) -> Connection | None:
        """获取连接"""
        return self._connections.get(connection_id)

    def get_by_session(self, session_id: str) -> Connection | None:
        """通过 session_id 查找连接"""
        for conn in self._connections.values():
            if conn.session_id == session_id:
                return conn
        return None

    def all_connections(self) -> Iterator[Connection]:
        """遍历所有连接"""
        yield from list(self._connections.values())

    @property
    def count(self) -> int:
        return len(self._connections)

    async def broadcast_text(self, message: ServerMessage) -> None:
        """向所有连接广播文本消息"""
        payload = message.to_json()
        failed = []
        for conn in list(self._connections.values()):
            try:
                await conn.websocket.send_text(payload)
            except Exception as e:
                logger.warning(f"广播失败 [{conn.connection_id}]: {e}")
                failed.append(conn.connection_id)
        for cid in failed:
            self.remove(cid)

    async def send_text(
        self, connection_id: str, message: ServerMessage
    ) -> bool:
        """向指定连接发送文本消息"""
        conn = self._connections.get(connection_id)
        if not conn or not conn.is_alive():
            return False
        try:
            await conn.websocket.send_text(message.to_json())
            return True
        except Exception as e:
            logger.warning(f"发送失败 [{connection_id}]: {e}")
            self.remove(connection_id)
            return False

    async def send_bytes(self, connection_id: str, data: bytes) -> bool:
        """向指定连接发送二进制数据"""
        conn = self._connections.get(connection_id)
        if not conn or not conn.is_alive():
            return False
        try:
            await conn.websocket.send_bytes(data)
            return True
        except Exception as e:
            logger.warning(f"发送失败 [{connection_id}]: {e}")
            self.remove(connection_id)
            return False
```

---

## 13.6 消息路由

消息路由负责根据消息类型分发到不同的处理函数：

```python
# src/voicebot/gateway/router.py

import logging
from typing import Callable, Awaitable
from .messages import ClientMessage, ClientMessageType
from .connection import Connection

logger = logging.getLogger(__name__)

# 消息处理器类型
MessageHandler = Callable[[Connection, ClientMessage], Awaitable[None]]


class MessageRouter:
    """
    消息路由器

    根据消息的 type 字段，把消息分发给对应的处理函数。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}

    def register(self, message_type: str, handler: MessageHandler) -> None:
        """注册消息处理器"""
        self._handlers[message_type] = handler
        logger.debug(f"注册消息处理器：{message_type}")

    def route(self, message_type: str) -> Callable:
        """装饰器：注册消息处理器"""
        def decorator(handler: MessageHandler) -> MessageHandler:
            self.register(message_type, handler)
            return handler
        return decorator

    async def dispatch(self, conn: Connection, message: ClientMessage) -> None:
        """分发消息到对应处理器"""
        handler = self._handlers.get(message.type)
        if handler is None:
            logger.warning(
                f"[{conn.connection_id}] 未知消息类型：{message.type}"
            )
            return
        try:
            await handler(conn, message)
        except Exception as e:
            logger.error(
                f"[{conn.connection_id}] 处理消息 {message.type} 时出错：{e}",
                exc_info=True,
            )
```

---

## 13.7 心跳检测

WebSocket 连接可能在网络中途悄悄断开，而两端都不知道。比如用户的手机屏幕关闭了，或者经过了一个会丢弃长连接的代理服务器。

心跳机制通过定期发送 ping/pong 消息来检测连接是否存活：

```
服务器                           客户端
  │                                │
  │── ping {"type":"ping"} ──────>│
  │                                │ (客户端必须在 30 秒内回复)
  │<── pong {"type":"pong"} ───────│
  │                                │
  │── ping ──────────────────────>│
  │                                │ (30秒内没收到 pong)
  │                                │
  │   [超时，关闭连接]              │
```

```python
# src/voicebot/gateway/heartbeat.py

import asyncio
import logging
import time
from .connection import Connection, ConnectionState
from .connection_manager import ConnectionManager
from .messages import ServerMessage, ServerMessageType

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """
    心跳管理器

    定期向所有连接发送 ping，
    超时未回复的连接将被强制关闭。
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        interval_seconds: float = 20.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._cm = connection_manager
        self._interval = interval_seconds
        self._timeout = timeout_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """启动心跳后台任务"""
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            f"心跳管理器启动，间隔 {self._interval}s，超时 {self._timeout}s"
        )

    def stop(self) -> None:
        """停止心跳"""
        if self._task and not self._task.done():
            self._task.cancel()

    async def _heartbeat_loop(self) -> None:
        """心跳主循环"""
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._check_all_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常：{e}", exc_info=True)

    async def _check_all_connections(self) -> None:
        """检查所有连接的心跳状态"""
        dead_connections = []
        ping_msg = ServerMessage(type=ServerMessageType.PONG)  # 复用 pong 类型

        for conn in self._cm.all_connections():
            if conn.state != ConnectionState.ACTIVE:
                continue

            # 检查是否超时
            if conn.pong_timeout(self._timeout):
                logger.warning(
                    f"[{conn.connection_id}] 心跳超时，准备关闭连接"
                )
                dead_connections.append(conn.connection_id)
                continue

            # 发送 ping
            ping_message = ServerMessage(
                type="ping",
                data={"timestamp": time.time()},
            )
            await self._cm.send_text(conn.connection_id, ping_message)
            conn.mark_ping()

        # 关闭超时连接
        for cid in dead_connections:
            conn = self._cm.get(cid)
            if conn:
                try:
                    await conn.websocket.close(code=1001, reason="Heartbeat timeout")
                except Exception:
                    pass
            self._cm.remove(cid)
```

---

## 13.8 完整的 WebSocket 网关

现在把所有组件组合成完整的网关：

```python
# src/voicebot/gateway/gateway.py

import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from .connection import Connection
from .connection_manager import ConnectionManager
from .heartbeat import HeartbeatManager
from .messages import (
    ClientMessage,
    ClientMessageType,
    ServerMessage,
    ServerMessageType,
)
from .router import MessageRouter

logger = logging.getLogger(__name__)


class VoiceBotGateway:
    """
    VoiceBot WebSocket 网关

    负责：
    - 接受/拒绝 WebSocket 连接
    - 管理连接生命周期
    - 消息路由
    - 心跳检测
    """

    def __init__(self) -> None:
        self._cm = ConnectionManager()
        self._router = MessageRouter()
        self._heartbeat = HeartbeatManager(self._cm)
        self._register_handlers()

    def _register_handlers(self) -> None:
        """注册各类消息的处理器"""

        @self._router.route(ClientMessageType.PING)
        async def handle_ping(conn: Connection, msg: ClientMessage) -> None:
            """响应客户端心跳"""
            conn.mark_pong()
            await self._cm.send_text(
                conn.connection_id,
                ServerMessage(
                    type=ServerMessageType.PONG,
                    data={"timestamp": msg.data.get("timestamp")},
                ),
            )

        @self._router.route(ClientMessageType.CONFIG)
        async def handle_config(conn: Connection, msg: ClientMessage) -> None:
            """处理会话配置"""
            logger.info(
                f"[{conn.connection_id}] 收到配置：{msg.data}"
            )
            # 这里可以更新会话配置，比如 TTS 声音、语速等
            # 实际项目中会通知 SessionManager 更新配置

        @self._router.route(ClientMessageType.INTERRUPT)
        async def handle_interrupt(conn: Connection, msg: ClientMessage) -> None:
            """处理打断请求（用户说话打断 TTS 播放）"""
            logger.info(f"[{conn.connection_id}] 用户打断")
            # 通知 TTS 停止当前播放
            # 这里发布一个 INTERRUPT 事件到事件总线（见第十四章）

    def attach_to_app(self, app: FastAPI, path: str = "/ws") -> None:
        """把网关挂载到 FastAPI 应用"""

        @app.on_event("startup")
        async def on_startup() -> None:
            self._heartbeat.start()
            logger.info("VoiceBot 网关启动")

        @app.on_event("shutdown")
        async def on_shutdown() -> None:
            self._heartbeat.stop()
            logger.info("VoiceBot 网关关闭")

        @app.websocket(path)
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await self._handle_connection(websocket)

    async def _handle_connection(self, websocket: WebSocket) -> None:
        """处理单个 WebSocket 连接的完整生命周期"""
        # 1. 建立连接
        await websocket.accept()
        conn = Connection(websocket=websocket)
        self._cm.add(conn)

        try:
            # 2. 发送会话就绪通知
            await self._cm.send_text(
                conn.connection_id,
                ServerMessage(
                    type=ServerMessageType.SESSION_READY,
                    data={"connection_id": conn.connection_id},
                ),
            )

            # 3. 启动消息接收循环
            await self._receive_loop(conn)

        except WebSocketDisconnect as e:
            logger.info(
                f"[{conn.connection_id}] 客户端主动断开，code={e.code}"
            )
        except Exception as e:
            logger.error(
                f"[{conn.connection_id}] 连接异常：{e}",
                exc_info=True,
            )
            # 尝试发送错误消息
            try:
                await self._cm.send_text(
                    conn.connection_id,
                    ServerMessage(
                        type=ServerMessageType.ERROR,
                        data={"message": str(e)},
                    ),
                )
            except Exception:
                pass
        finally:
            # 4. 清理连接
            await self._cleanup_connection(conn)

    async def _receive_loop(self, conn: Connection) -> None:
        """
        消息接收主循环

        WebSocket 消息分两种：
        - 文本帧（JSON）：控制消息，用 receive_text() 读取
        - 二进制帧：音频数据，用 receive_bytes() 读取

        FastAPI 的 websocket.receive() 返回一个 dict，
        包含 "type" 字段指示是文本还是二进制。
        """
        while conn.is_alive():
            try:
                # 使用底层 receive() 同时处理文本和二进制
                raw = await conn.websocket.receive()

                if raw["type"] == "websocket.disconnect":
                    break
                elif raw["type"] == "websocket.receive":
                    if "text" in raw and raw["text"]:
                        # 文本帧：JSON 控制消息
                        await self._handle_text_message(conn, raw["text"])
                    elif "bytes" in raw and raw["bytes"]:
                        # 二进制帧：音频数据
                        await self._handle_audio_data(conn, raw["bytes"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[{conn.connection_id}] 接收消息时出错：{e}",
                    exc_info=True,
                )
                break

    async def _handle_text_message(
        self, conn: Connection, raw_text: str
    ) -> None:
        """处理文本消息（JSON 格式）"""
        try:
            message = ClientMessage.from_json(raw_text)
            await self._router.dispatch(conn, message)
        except Exception as e:
            logger.warning(
                f"[{conn.connection_id}] 解析消息失败：{e}，原始：{raw_text[:100]}"
            )

    async def _handle_audio_data(self, conn: Connection, data: bytes) -> None:
        """处理音频数据（二进制帧）"""
        # 把音频数据发给 ASR 处理
        # 实际项目中这里会发布 AUDIO_CHUNK 事件到事件总线
        logger.debug(
            f"[{conn.connection_id}] 收到音频数据：{len(data)} bytes"
        )

    async def _cleanup_connection(self, conn: Connection) -> None:
        """清理连接资源"""
        self._cm.remove(conn.connection_id)
        # 如果有绑定的 session，通知 SessionManager 关闭 session
        if conn.session_id:
            logger.info(
                f"[{conn.connection_id}] 清理 session：{conn.session_id}"
            )

    async def send_audio(
        self, connection_id: str, audio_bytes: bytes
    ) -> None:
        """
        向指定连接发送 TTS 音频数据

        这个方法会被 TTS 管理器调用，把合成的音频推送给客户端。
        """
        await self._cm.send_bytes(connection_id, audio_bytes)

    async def send_message(
        self, connection_id: str, message: ServerMessage
    ) -> None:
        """向指定连接发送 JSON 消息"""
        await self._cm.send_text(connection_id, message)
```

---

## 13.9 会话绑定

一个 WebSocket 连接对应一个对话会话（Session）。Session 存储着对话历史、用户配置等状态。

```python
# src/voicebot/gateway/session_binding.py

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    对话会话

    存储一个用户对话的所有状态。
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    connection_id: str | None = None

    # 对话历史（LLM 上下文）
    messages: list[dict] = field(default_factory=list)

    # 配置
    tts_voice: str = "zf_xiaobei"
    tts_speed: float = 1.0
    language: str = "zh"

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def get_history(self) -> list[dict]:
        """获取对话历史（用于 LLM 上下文）"""
        return list(self.messages)


class SessionManager:
    """管理所有 Session"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create_session(self, connection_id: str) -> Session:
        """为新连接创建 Session"""
        session = Session(connection_id=connection_id)
        self._sessions[session.session_id] = session
        logger.info(
            f"创建 Session [{session.session_id}] "
            f"for connection [{connection_id}]"
        )
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_session_by_connection(
        self, connection_id: str
    ) -> Session | None:
        for session in self._sessions.values():
            if session.connection_id == connection_id:
                return session
        return None

    def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            logger.info(f"Session [{session_id}] 已关闭")
```

---

## 13.10 错误处理和优雅关闭

生产环境中，错误和关闭是常态，必须处理得好：

```python
# src/voicebot/gateway/error_handling.py

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理（替代 on_event 的现代写法）
    """
    # 启动
    logger.info("VoiceBot 服务启动中...")
    # 这里可以初始化数据库连接、加载模型等
    yield
    # 关闭
    logger.info("VoiceBot 服务关闭中...")
    # 这里执行清理工作


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="VoiceBot Gateway",
        lifespan=lifespan,
    )

    # 错误处理中间件
    @app.middleware("http")
    async def error_middleware(request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"未处理的 HTTP 错误：{e}", exc_info=True)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"},
            )

    return app
```

### 13.10.1 优雅关闭流程

```
收到 SIGTERM 信号
        │
        ▼
1. 停止接受新连接
        │
        ▼
2. 向所有活跃连接发送关闭通知
        │
        ▼
3. 等待进行中的 TTS 完成（最多 5 秒）
        │
        ▼
4. 强制关闭剩余连接
        │
        ▼
5. 释放资源（关闭模型、数据库等）
        │
        ▼
6. 进程退出
```

```python
# src/voicebot/gateway/graceful_shutdown.py

import asyncio
import signal
import logging
from .connection_manager import ConnectionManager
from .messages import ServerMessage, ServerMessageType

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """优雅关闭处理器"""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._cm = connection_manager
        self._timeout = timeout_seconds
        self._shutdown_event = asyncio.Event()

    def setup_signal_handlers(self) -> None:
        """注册系统信号处理器"""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.shutdown()),
            )

    async def shutdown(self) -> None:
        """执行优雅关闭"""
        logger.info("开始优雅关闭...")

        # 通知所有客户端
        shutdown_msg = ServerMessage(
            type=ServerMessageType.ERROR,
            data={"message": "服务器正在维护，请稍后重试"},
        )
        await self._cm.broadcast_text(shutdown_msg)

        # 等待连接自然断开（最多等 timeout 秒）
        try:
            await asyncio.wait_for(
                self._wait_for_all_disconnected(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"等待超时，强制关闭剩余 {self._cm.count} 个连接")

        # 标记关闭完成
        self._shutdown_event.set()
        logger.info("优雅关闭完成")

    async def _wait_for_all_disconnected(self) -> None:
        """等待所有连接断开"""
        while self._cm.count > 0:
            await asyncio.sleep(0.1)

    async def wait_for_shutdown(self) -> None:
        """等待关闭信号"""
        await self._shutdown_event.wait()
```

---

## 13.11 并发处理

asyncio 下，多个 WebSocket 连接同时存在，每个连接的消息接收是独立的协程：

```
事件循环
    │
    ├── 连接 A 的接收循环 (协程)
    │       │
    │       ├── 等待 receive() ← 大部分时间在这里等待 I/O
    │       └── 处理消息 → 短暂占用 CPU
    │
    ├── 连接 B 的接收循环 (协程)
    │       │
    │       ├── 等待 receive()
    │       └── 处理消息
    │
    ├── 连接 C 的接收循环 (协程)
    │       └── ...
    │
    ├── 心跳检测循环 (协程)
    │
    └── TTS 合成任务 (协程池)
```

asyncio 的关键特性：当一个协程在等待 I/O（比如 `await websocket.receive()`）时，事件循环会切换去运行其他协程，实现真正的并发而不是多线程。

```python
# 并发处理的正确姿势：避免在协程中做耗时同步操作

import asyncio

# 错误示范：直接在协程中做 CPU 密集操作
async def bad_handler(conn, audio_bytes):
    result = some_heavy_computation(audio_bytes)  # 会阻塞整个事件循环！
    await conn.send(result)

# 正确做法：把 CPU 密集操作放到线程池
async def good_handler(conn, audio_bytes):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,  # 使用默认线程池
        some_heavy_computation,
        audio_bytes,
    )
    await conn.send(result)
```

---

## 13.12 完整测试

```python
# tests/test_gateway.py

import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from voicebot.gateway.gateway import VoiceBotGateway


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用"""
    app = FastAPI()
    gateway = VoiceBotGateway()
    gateway.attach_to_app(app)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_websocket_connection(client):
    """测试基本连接建立"""
    with client.websocket_connect("/ws") as ws:
        # 应该收到 session_ready 消息
        data = ws.receive_json()
        assert data["type"] == "session_ready"
        assert "connection_id" in data["data"]


def test_ping_pong(client):
    """测试心跳 ping-pong"""
    with client.websocket_connect("/ws") as ws:
        # 跳过 session_ready
        ws.receive_json()

        # 发送 ping
        ws.send_json({
            "type": "ping",
            "data": {"timestamp": 1234567890.0},
        })

        # 应该收到 pong
        response = ws.receive_json()
        assert response["type"] == "pong"
        assert response["data"]["timestamp"] == 1234567890.0


def test_unknown_message_type(client):
    """测试未知消息类型不会导致连接崩溃"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 跳过 session_ready

        # 发送未知类型
        ws.send_json({"type": "unknown_type", "data": {}})

        # 发送 ping，验证连接还活着
        ws.send_json({"type": "ping", "data": {}})
        response = ws.receive_json()
        assert response["type"] == "pong"


def test_binary_audio_message(client):
    """测试接收二进制音频数据"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 跳过 session_ready

        # 发送模拟音频数据
        fake_audio = bytes(1024)  # 1024 字节的空音频
        ws.send_bytes(fake_audio)

        # 发送 ping 确认连接正常
        ws.send_json({"type": "ping", "data": {}})
        response = ws.receive_json()
        assert response["type"] == "pong"
```

---

## 本章小结

本章我们构建了 VoiceBot 的通信基础设施——WebSocket 网关：

- **WebSocket vs HTTP**：双向全双工通信，服务器可以主动推送，适合实时语音对话。
- **消息协议**：定义了清晰的客户端/服务器消息类型，文本帧用于控制，二进制帧用于音频。
- **连接管理器**：追踪所有活跃连接，支持按连接 ID 或 Session ID 查找。
- **消息路由**：通过注册机制把不同类型的消息分发给对应的处理函数，解耦业务逻辑。
- **会话绑定**：每个 WebSocket 连接对应一个 Session，存储对话历史和配置。
- **心跳检测**：定期 ping/pong，自动清理静默断开的连接。
- **优雅关闭**：收到 SIGTERM 时通知客户端，等待当前请求处理完成再退出。
- **并发处理**：asyncio 协程天然支持多连接并发，CPU 密集操作放线程池。

现在我们有了三个核心模块：ASR、LLM、TTS，还有一个 WebSocket 网关。但这些模块之间怎么通信？ASR 识别出文字了，怎么告诉 LLM？LLM 生成了回复，怎么触发 TTS 合成？

直接调用？那会产生紧耦合，难以测试和维护。

**下一章**我们来实现事件总线——一个让各模块互相解耦的发布-订阅系统，是整个 VoiceBot 架构的"神经系统"。
