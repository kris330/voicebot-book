
async def _handle_control(self, session, data: dict) -> None:
    """处理控制消息。"""
    logger = logging.getLogger(__name__)
    msg_type = data.get("type")

    if msg_type == "vad_end":
        audio_data = session.clear_asr_buffer()
        if audio_data:
            asyncio.create_task(
                self._pipeline.process(session, audio_data),
                name=f"pipeline-{session.session_id}"
            )

    elif msg_type == "interrupt":
        await self._handle_interrupt(session)

    elif msg_type == "ping":
        import json
        await session.websocket.send(json.dumps({"type": "pong"}))


async def _handle_interrupt(self, session) -> None:
    """
    处理打断信号。

    步骤：
    1. 取消当前 LLM 生成任务
    2. 取消当前 TTS 合成任务
    3. 清空 TTS 音频队列
    4. 重置 Session 状态
    """
    logger = logging.getLogger(__name__)
    logger.info(f"[{session.session_id}] 处理打断信号")

    # 取消所有进行中的任务
    await session.cancel_current_tasks()

    # 清空 TTS 队列（丢弃所有未发送的音频）
    await session.drain_tts_queue()

    # 清空 ASR 缓冲区（上一轮的残留音频）
    session.clear_asr_buffer()

    # 通知客户端打断已处理
    import json
    try:
        await session.websocket.send(json.dumps({
            "type": "interrupt_ack",
        }))
    except Exception:
        pass  # 连接可能已经断开

    logger.info(f"[{session.session_id}] 打断处理完成，等待新的用户输入")
