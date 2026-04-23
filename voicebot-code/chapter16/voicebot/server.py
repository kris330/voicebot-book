
import asyncio
import json
import logging

import websockets

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

# 全局 SessionManager 实例
session_manager = SessionManager(
    session_timeout_seconds=1800,
    system_prompt="你是一个友好的语音助手，回答要简洁，适合语音播放。",
)


async def handle_connection(websocket) -> None:
    """
    处理单个 WebSocket 连接的完整生命周期。
    """
    # 1. 创建 Session
    session = await session_manager.create_session(websocket)

    try:
        # 2. 发送欢迎消息
        await websocket.send(json.dumps({
            "type": "session_created",
            "session_id": session.session_id,
        }))

        # 3. 启动 TTS 发送协程
        tts_sender_task = asyncio.create_task(
            tts_sender(session),
            name=f"tts-sender-{session.session_id}"
        )

        # 4. 主消息处理循环
        async for message in websocket:
            await handle_message(session, message)

    except websockets.exceptions.ConnectionClosed as e:
        logger.info(f"[{session.session_id}] WebSocket 连接关闭: {e.code} {e.reason}")
    except Exception as e:
        logger.error(f"[{session.session_id}] 处理连接时发生错误: {e}", exc_info=True)
    finally:
        # 5. 无论如何都要清理 Session
        await session_manager.remove_session(session.session_id)
        if "tts_sender_task" in locals():
            tts_sender_task.cancel()
            try:
                await tts_sender_task
            except asyncio.CancelledError:
                pass


async def handle_message(session, raw_message) -> None:
    """处理来自客户端的单条消息。"""
    session.touch()  # 更新活跃时间

    try:
        if isinstance(raw_message, bytes):
            # 二进制消息 = 音频数据
            await handle_audio_chunk(session, raw_message)
        else:
            # 文本消息 = JSON 控制消息
            data = json.loads(raw_message)
            await handle_control_message(session, data)
    except json.JSONDecodeError:
        logger.warning(f"[{session.session_id}] 收到无效 JSON: {raw_message[:100]}")
    except Exception as e:
        logger.error(f"[{session.session_id}] 处理消息时发生错误: {e}", exc_info=True)


async def handle_audio_chunk(session, audio_chunk: bytes) -> None:
    """处理音频数据块（详见 ASR、VAD 相关章节）。"""
    session.append_asr_audio(audio_chunk)


async def handle_control_message(session, data: dict) -> None:
    """处理控制消息。"""
    msg_type = data.get("type")

    if msg_type == "vad_start":
        # 用户开始说话
        session.vad_state = "speaking"
        logger.debug(f"[{session.session_id}] VAD: 开始说话")

    elif msg_type == "vad_end":
        # 用户说完了，送去 ASR
        session.vad_state = "silent"
        audio_data = session.clear_asr_buffer()
        if audio_data:
            logger.info(
                f"[{session.session_id}] 收到完整语音，"
                f"大小: {len(audio_data)} bytes"
            )
            # 触发 ASR → LLM → TTS 流水线（详见后续章节）

    elif msg_type == "interrupt":
        # 用户打断 AI 的说话
        logger.info(f"[{session.session_id}] 收到打断信号")
        await session.cancel_current_tasks()
        await session.drain_tts_queue()

    else:
        logger.warning(f"[{session.session_id}] 未知消息类型: {msg_type}")


async def tts_sender(session) -> None:
    """
    从 TTS 队列中取出音频块，发送给客户端。
    这是一个独立的协程，和主处理循环并行运行。
    """
    logger.debug(f"[{session.session_id}] TTS 发送协程已启动")

    while True:
        try:
            # 等待 TTS 队列中有数据
            audio_chunk = await session.tts_queue.get()

            # None 是终止信号
            if audio_chunk is None:
                logger.debug(f"[{session.session_id}] TTS 发送协程收到终止信号")
                break

            # 发送音频数据给客户端
            await session.websocket.send(audio_chunk)

        except asyncio.CancelledError:
            logger.debug(f"[{session.session_id}] TTS 发送协程已取消")
            break
        except Exception as e:
            logger.error(
                f"[{session.session_id}] TTS 发送时发生错误: {e}",
                exc_info=True
            )
            break


async def main() -> None:
    """启动 WebSocket 服务器。"""
    await session_manager.start()

    async with websockets.serve(handle_connection, "0.0.0.0", 8765) as server:
        logger.info("VoiceBot 服务器已启动，监听端口 8765")
        await asyncio.Future()  # 永远运行

    await session_manager.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
