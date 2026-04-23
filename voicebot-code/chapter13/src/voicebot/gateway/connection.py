
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
