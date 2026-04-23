
import json
import logging

from aiohttp import web, WSMsgType

from voicebot.pipeline.full_pipeline import FullVoicePipeline

logger = logging.getLogger(__name__)


async def ws_voice_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=64 * 1024)
    await ws.prepare(request)

    session_id = f"sess_{id(ws)}"
    pipeline = FullVoicePipeline()

    # 注册回调：把各阶段结果发回给客户端
    async def send(data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    pipeline.on("transcript", lambda text: send({"type": "transcript", "text": text}))
    pipeline.on("llm_sentence", lambda text: send({"type": "llm_sentence", "text": text}))
    pipeline.on("response_done", lambda text: send({"type": "response_done", "text": text}))

    await pipeline.init()
    await ws.send_json({"type": "ready"})
    logger.info(f"[{session_id}] 连接就绪")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await pipeline.process_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "new_session":
                    pipeline.new_session()
                    await ws.send_json({"type": "session_cleared"})
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    except Exception as e:
        logger.error(f"[{session_id}] 连接错误: {e}")

    logger.info(f"[{session_id}] 连接关闭")
    return ws
