
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
