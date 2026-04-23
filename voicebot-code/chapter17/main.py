
import asyncio
import json
import logging
import os
import signal
import sys

import websockets

from voicebot.config import load_config
from voicebot.session_manager import SessionManager
from voicebot.asr.openai_asr import OpenAIASR
from voicebot.llm.openai_llm import OpenAILLM
from voicebot.tts.openai_tts import OpenAITTS
from voicebot.pipeline import VoicePipeline


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


class VoiceBotServer:
    """VoiceBot WebSocket 服务器。"""

    def __init__(self, config_path: str = "config.json") -> None:
        self._config = load_config(config_path)

        # 初始化各模块
        self._asr = OpenAIASR(self._config.asr)
        self._llm = OpenAILLM(self._config.llm)
        self._tts = OpenAITTS(self._config.tts)
        self._pipeline = VoicePipeline(self._asr, self._llm, self._tts)

        self._session_manager = SessionManager(
            session_timeout_seconds=self._config.session.timeout_seconds,
            system_prompt=self._config.session.system_prompt,
        )

    async def start(self) -> None:
        """启动服务器。"""
        setup_logging(self._config.server.log_level)
        logger = logging.getLogger(__name__)

        await self._session_manager.start()

        host = self._config.server.host
        port = self._config.server.port

        async with websockets.serve(
            self._handle_connection,
            host,
            port,
            # 增大消息大小限制，音频数据可能很大
            max_size=10 * 1024 * 1024,  # 10MB
            # 保持连接活跃
            ping_interval=20,
            ping_timeout=60,
        ) as server:
            logger.info(f"VoiceBot 已启动！WebSocket 地址: ws://{host}:{port}")
            logger.info(f"打开浏览器访问: http://localhost:{port}")

            # 等待关闭信号
            stop_event = asyncio.Event()

            def _handle_signal():
                logger.info("收到关闭信号，正在停止服务器...")
                stop_event.set()

            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _handle_signal)

            await stop_event.wait()

        await self._session_manager.stop()
        logger.info("服务器已停止")

    async def _handle_connection(self, websocket) -> None:
        """处理单个 WebSocket 连接。"""
        logger = logging.getLogger(__name__)

        # 创建 Session
        session = await self._session_manager.create_session(websocket)

        # 启动 TTS 发送协程
        tts_sender_task = asyncio.create_task(
            self._tts_sender(session),
            name=f"tts-sender-{session.session_id}"
        )

        try:
            # 发送欢迎消息
            await websocket.send(json.dumps({
                "type": "connected",
                "session_id": session.session_id,
                "message": "VoiceBot 已连接，请开始说话",
            }))

            # 主消息处理循环
            async for raw_message in websocket:
                await self._handle_message(session, raw_message)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info(
                f"[{session.session_id}] 连接关闭: code={e.code}"
            )
        except Exception as e:
            logger.error(
                f"[{session.session_id}] 连接处理错误: {e}",
                exc_info=True
            )
        finally:
            await self._session_manager.remove_session(session.session_id)
            tts_sender_task.cancel()
            try:
                await tts_sender_task
            except asyncio.CancelledError:
                pass

    async def _handle_message(self, session, raw_message) -> None:
        """处理来自客户端的消息。"""
        session.touch()

        if isinstance(raw_message, bytes):
            # 音频数据
            session.append_asr_audio(raw_message)
        else:
            try:
                data = json.loads(raw_message)
                await self._handle_control(session, data)
            except json.JSONDecodeError:
                pass

    async def _handle_control(self, session, data: dict) -> None:
        """处理控制消息。"""
        logger = logging.getLogger(__name__)
        msg_type = data.get("type")

        if msg_type == "vad_end":
            # 用户说完话了，触发流水线
            audio_data = session.clear_asr_buffer()
            if audio_data:
                # 异步处理，不阻塞主消息循环
                asyncio.create_task(
                    self._pipeline.process(session, audio_data),
                    name=f"pipeline-{session.session_id}"
                )

        elif msg_type == "interrupt":
            # 打断：取消当前任务，清空队列
            logger.info(f"[{session.session_id}] 处理打断信号")
            await session.cancel_current_tasks()
            await session.drain_tts_queue()

        elif msg_type == "ping":
            await session.websocket.send(json.dumps({"type": "pong"}))

    async def _tts_sender(self, session) -> None:
        """从 TTS 队列发送音频到客户端。"""
        logger = logging.getLogger(__name__)

        while True:
            try:
                audio_chunk = await session.tts_queue.get()
                if audio_chunk is None:
                    break
                await session.websocket.send(audio_chunk)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{session.session_id}] TTS 发送错误: {e}")
                break


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    server = VoiceBotServer(config_path)
    asyncio.run(server.start())
