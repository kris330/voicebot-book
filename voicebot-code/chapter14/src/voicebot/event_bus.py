
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable

from .events import BaseEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]


@dataclass
class HandlerEntry:
    handler: EventHandler
    priority: int
    handler_name: str


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HandlerEntry]] = defaultdict(list)

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 50,
        name: str | None = None,
    ) -> None:
        handler_name = name or handler.__name__
        entry = HandlerEntry(
            handler=handler,
            priority=priority,
            handler_name=handler_name,
        )
        handlers = self._handlers[event_type]
        handlers.append(entry)
        handlers.sort(key=lambda e: e.priority)
        logger.debug(
            f"订阅 [{event_type}] 处理器：{handler_name}，优先级：{priority}"
        )

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        handlers = self._handlers.get(event_type, [])
        original_count = len(handlers)
        self._handlers[event_type] = [
            e for e in handlers if e.handler is not handler
        ]
        return len(self._handlers[event_type]) < original_count

    def on(self, event_type: str, priority: int = 50) -> Callable:
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler, priority=priority)
            return handler
        return decorator

    async def publish(self, event: BaseEvent) -> None:
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            return
        logger.debug(
            f"发布 [{event.event_type}] session={event.session_id}"
        )
        for entry in handlers:
            try:
                await entry.handler(event)
            except Exception as e:
                logger.error(
                    f"处理器 [{entry.handler_name}] 出错：{e}",
                    exc_info=True,
                )

    async def publish_nowait(self, event: BaseEvent) -> None:
        asyncio.create_task(self.publish(event))

    def subscriber_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        self._handlers.clear()
