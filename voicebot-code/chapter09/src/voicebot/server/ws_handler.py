
import asyncio
import logging
import json

from aiohttp import web, WSMsgType

from voicebot.pipeline.voice_pipeline import VoicePipeline

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理单个 WebSocket 连接的生命周期"""

    def __init__(self, ws: web.WebSocketResponse, session_id: str):
        self._ws = ws
        self._session_id = session_id
        self._pipeline = VoicePipeline()

    async def init(self):
        """初始化 pipeline（加载模型等耗时操作）"""
        await self._pipeline.init()

        # 注册回调
        self._pipeline.on_transcript(self._on_transcript)
        self._pipeline.on_speech_start(self._on_speech_start)

        logger.info(f"[{self._session_id}] 连接初始化完成")

    async def handle_message(self, msg):
        """处理 WebSocket 消息"""
        if msg.type == WSMsgType.BINARY:
            # 收到音频数据
            await self._pipeline.process_audio(msg.data)

        elif msg.type == WSMsgType.TEXT:
            # 收到控制指令
            data = json.loads(msg.data)
            await self._handle_control(data)

    async def _handle_control(self, data: dict):
        cmd = data.get('type')
        if cmd == 'start_session':
            self._pipeline.reset()
            await self._send_json({'type': 'session_started'})
        elif cmd == 'stop_session':
            await self._send_json({'type': 'session_stopped'})

    async def _on_transcript(self, text: str):
        """ASR 识别完成，发送给客户端"""
        await self._send_json({
            'type': 'transcript',
            'text': text,
            'is_final': True,
        })

    async def _on_speech_start(self):
        """语音开始，通知客户端（可以用于打断 TTS）"""
        await self._send_json({'type': 'speech_start'})

    async def _send_json(self, data: dict):
        try:
            await self._ws.send_json(data)
        except Exception as e:
            logger.warning(f"[{self._session_id}] 发送消息失败: {e}")

    async def cleanup(self):
        logger.info(f"[{self._session_id}] 连接关闭，清理资源")


# WebSocket 路由处理函数
async def ws_voice_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=64 * 1024)  # 64KB 消息大小限制
    await ws.prepare(request)

    session_id = request.headers.get('X-Session-ID', f"sess_{id(ws)}")
    manager = ConnectionManager(ws, session_id)

    try:
        await manager.init()
        await ws.send_json({'type': 'ready'})

        async for msg in ws:
            if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
            await manager.handle_message(msg)

    except Exception as e:
        logger.error(f"[{session_id}] 连接异常: {e}", exc_info=True)
    finally:
        await manager.cleanup()

    return ws
