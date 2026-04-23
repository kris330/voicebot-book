
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
