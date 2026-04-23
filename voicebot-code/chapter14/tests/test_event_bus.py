
import asyncio
import pytest
from voicebot.event_bus import EventBus
from voicebot.events import (
    ASRResultEvent,
    EventType,
    InterruptEvent,
)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.mark.asyncio
async def test_basic_publish_subscribe(bus: EventBus) -> None:
    """测试基本发布-订阅"""
    received_events = []

    async def handler(event: ASRResultEvent) -> None:
        received_events.append(event)

    bus.subscribe(EventType.ASR_FINAL_RESULT, handler)

    event = ASRResultEvent(
        session_id="s1",
        text="你好",
        is_final=True,
    )
    await bus.publish(event)

    assert len(received_events) == 1
    assert received_events[0].text == "你好"


@pytest.mark.asyncio
async def test_priority_ordering(bus: EventBus) -> None:
    """测试处理器按优先级顺序执行"""
    execution_order = []

    async def handler_high(event) -> None:
        execution_order.append("high")

    async def handler_medium(event) -> None:
        execution_order.append("medium")

    async def handler_low(event) -> None:
        execution_order.append("low")

    # 注意：先注册 low，后注册 high，但应该按优先级执行
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_low, priority=90)
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_high, priority=10)
    bus.subscribe(EventType.ASR_FINAL_RESULT, handler_medium, priority=50)

    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    await bus.publish(event)

    assert execution_order == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_error_isolation(bus: EventBus) -> None:
    """测试错误隔离：一个处理器出错不影响其他处理器"""
    executed = []

    async def failing_handler(event) -> None:
        executed.append("failing")
        raise ValueError("故意的错误")

    async def ok_handler(event) -> None:
        executed.append("ok")

    bus.subscribe(EventType.ASR_FINAL_RESULT, failing_handler, priority=10)
    bus.subscribe(EventType.ASR_FINAL_RESULT, ok_handler, priority=50)

    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    # 不应该抛出异常
    await bus.publish(event)

    # failing_handler 执行了，ok_handler 也执行了
    assert "failing" in executed
    assert "ok" in executed


@pytest.mark.asyncio
async def test_unsubscribe(bus: EventBus) -> None:
    """测试取消订阅"""
    call_count = 0

    async def handler(event) -> None:
        nonlocal call_count
        call_count += 1

    bus.subscribe(EventType.ASR_FINAL_RESULT, handler)
    event = ASRResultEvent(session_id="s1", text="test", is_final=True)

    await bus.publish(event)
    assert call_count == 1

    bus.unsubscribe(EventType.ASR_FINAL_RESULT, handler)
    await bus.publish(event)
    assert call_count == 1  # 取消订阅后不再调用


@pytest.mark.asyncio
async def test_decorator_style(bus: EventBus) -> None:
    """测试装饰器风格的订阅"""
    received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handler(event: ASRResultEvent) -> None:
        received.append(event.text)

    event = ASRResultEvent(session_id="s1", text="装饰器测试", is_final=True)
    await bus.publish(event)

    assert received == ["装饰器测试"]


@pytest.mark.asyncio
async def test_no_handlers_no_error(bus: EventBus) -> None:
    """测试没有处理器时不报错"""
    event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    # 不应该抛出任何异常
    await bus.publish(event)


@pytest.mark.asyncio
async def test_multiple_event_types(bus: EventBus) -> None:
    """测试多种事件类型互不干扰"""
    asr_received = []
    interrupt_received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handle_asr(event) -> None:
        asr_received.append(event)

    @bus.on(EventType.INTERRUPT)
    async def handle_interrupt(event) -> None:
        interrupt_received.append(event)

    asr_event = ASRResultEvent(session_id="s1", text="test", is_final=True)
    interrupt_event = InterruptEvent(session_id="s1")

    await bus.publish(asr_event)
    await bus.publish(interrupt_event)

    assert len(asr_received) == 1
    assert len(interrupt_received) == 1
    # 两者没有互相干扰


@pytest.mark.asyncio
async def test_publish_nowait(bus: EventBus) -> None:
    """测试非阻塞发布"""
    received = []

    @bus.on(EventType.ASR_FINAL_RESULT)
    async def handler(event) -> None:
        await asyncio.sleep(0.01)  # 模拟异步操作
        received.append(event.text)

    event = ASRResultEvent(session_id="s1", text="nowait test", is_final=True)

    # publish_nowait 立即返回
    await bus.publish_nowait(event)
    assert len(received) == 0  # 还没执行完

    # 等待后台任务完成
    await asyncio.sleep(0.1)
    assert len(received) == 1
