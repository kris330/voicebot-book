
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
