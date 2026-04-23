
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
