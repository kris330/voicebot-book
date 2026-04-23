
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
