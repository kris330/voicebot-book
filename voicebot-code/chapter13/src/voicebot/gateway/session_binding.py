
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
