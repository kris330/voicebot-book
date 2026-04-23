
import asyncio
import logging
from fastapi import FastAPI

from .config_loader import load_config
from .pipeline_factory import PipelineFactory
from .gateway.gateway import VoiceBotGateway
from .gateway.session_binding import SessionManager
from .event_bus import EventBus
from .gateway.messages import ServerMessage, ServerMessageType
from .events import (
    AudioChunkEvent,
    ASRResultEvent,
    TTSAudioChunkEvent,
    EventType,
)

logger = logging.getLogger(__name__)


def create_voicebot_app(config_path: str) -> FastAPI:
    """
    从配置文件创建完整的 VoiceBot FastAPI 应用

    Args:
        config_path: JSON 配置文件路径

    Returns:
        可直接运行的 FastAPI 应用
    """
    # 1. 加载配置
    config_data = load_config(config_path)

    # 2. 创建 Pipeline（加载模型）
    logger.info("加载模型...")
    pipeline = PipelineFactory.from_dict(config_data)

    # 3. 创建核心组件
    gateway = VoiceBotGateway()
    session_manager = SessionManager()

    # 4. 创建 FastAPI 应用
    app = FastAPI(title="VoiceBot")

    # 5. 挂载网关
    gateway.attach_to_app(app, path="/ws")

    # 6. 注册连接事件处理：新连接时创建 Session
    original_handle = gateway._handle_connection

    async def handle_connection_with_session(websocket):
        """扩展连接处理，绑定 Session"""
        # 实际项目中这里会在连接建立后立即绑定 session
        # 为简洁起见这里省略详细实现
        await original_handle(websocket)

    # 7. 设置音频数据处理流程
    _setup_audio_pipeline(gateway, session_manager, pipeline)

    return app


def _setup_audio_pipeline(gateway, session_manager, pipeline):
    """
    设置音频处理流水线

    连接网关 → ASR → LLM → TTS → 网关 的完整数据流
    """
    # 覆盖网关的音频处理方法
    original_handle_audio = gateway._handle_audio_data

    async def handle_audio_with_pipeline(conn, audio_bytes):
        """处理音频：ASR → LLM → TTS"""
        session = session_manager.get_session_by_connection(conn.connection_id)
        if session is None:
            return

        session_pipeline = pipeline.clone(session.session_id)

        # ASR 识别
        text = await pipeline.asr.transcribe(audio_bytes)
        if not text.strip():
            return

        # 发送 ASR 结果给客户端
        await gateway.send_message(
            conn.connection_id,
            ServerMessage(
                type=ServerMessageType.ASR_RESULT,
                data={"text": text, "is_final": True},
            ),
        )

        # LLM + TTS 流式处理
        await gateway.send_message(
            conn.connection_id,
            ServerMessage(type=ServerMessageType.TTS_START, data={}),
        )

        async for audio_chunk in await session_pipeline.process_user_input(text):
            await gateway.send_audio(conn.connection_id, audio_chunk)

        await gateway.send_message(
            conn.connection_id,
            ServerMessage(type=ServerMessageType.TTS_END, data={}),
        )

    gateway._handle_audio_data = handle_audio_with_pipeline
