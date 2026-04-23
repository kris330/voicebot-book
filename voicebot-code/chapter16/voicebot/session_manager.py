
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
