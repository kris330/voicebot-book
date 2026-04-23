
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from .connection import Connection
from .connection_manager import ConnectionManager
from .heartbeat import HeartbeatManager
from .messages import (
    ClientMessage,
    ClientMessageType,
    ServerMessage,
    ServerMessageType,
)
from .router import MessageRouter

logger = logging.getLogger(__name__)


class VoiceBotGateway:
    """
    VoiceBot WebSocket 网关

    负责：
    - 接受/拒绝 WebSocket 连接
    - 管理连接生命周期
    - 消息路由
    - 心跳检测
    """

    def __init__(self) -> None:
        self._cm = ConnectionManager()
        self._router = MessageRouter()
        self._heartbeat = HeartbeatManager(self._cm)
        self._register_handlers()

    def _register_handlers(self) -> None:
        """注册各类消息的处理器"""

        @self._router.route(ClientMessageType.PING)
        async def handle_ping(conn: Connection, msg: ClientMessage) -> None:
            """响应客户端心跳"""
            conn.mark_pong()
            await self._cm.send_text(
                conn.connection_id,
                ServerMessage(
                    type=ServerMessageType.PONG,
                    data={"timestamp": msg.data.get("timestamp")},
                ),
            )

        @self._router.route(ClientMessageType.CONFIG)
        async def handle_config(conn: Connection, msg: ClientMessage) -> None:
            """处理会话配置"""
            logger.info(
                f"[{conn.connection_id}] 收到配置：{msg.data}"
            )
            # 这里可以更新会话配置，比如 TTS 声音、语速等
            # 实际项目中会通知 SessionManager 更新配置

        @self._router.route(ClientMessageType.INTERRUPT)
        async def handle_interrupt(conn: Connection, msg: ClientMessage) -> None:
            """处理打断请求（用户说话打断 TTS 播放）"""
            logger.info(f"[{conn.connection_id}] 用户打断")
            # 通知 TTS 停止当前播放
            # 这里发布一个 INTERRUPT 事件到事件总线（见第十四章）

    def attach_to_app(self, app: FastAPI, path: str = "/ws") -> None:
        """把网关挂载到 FastAPI 应用"""

        @app.on_event("startup")
        async def on_startup() -> None:
            self._heartbeat.start()
            logger.info("VoiceBot 网关启动")

        @app.on_event("shutdown")
        async def on_shutdown() -> None:
            self._heartbeat.stop()
            logger.info("VoiceBot 网关关闭")

        @app.websocket(path)
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await self._handle_connection(websocket)

    async def _handle_connection(self, websocket: WebSocket) -> None:
        """处理单个 WebSocket 连接的完整生命周期"""
        # 1. 建立连接
        await websocket.accept()
        conn = Connection(websocket=websocket)
        self._cm.add(conn)

        try:
            # 2. 发送会话就绪通知
            await self._cm.send_text(
                conn.connection_id,
                ServerMessage(
                    type=ServerMessageType.SESSION_READY,
                    data={"connection_id": conn.connection_id},
                ),
            )

            # 3. 启动消息接收循环
            await self._receive_loop(conn)

        except WebSocketDisconnect as e:
            logger.info(
                f"[{conn.connection_id}] 客户端主动断开，code={e.code}"
            )
        except Exception as e:
            logger.error(
                f"[{conn.connection_id}] 连接异常：{e}",
                exc_info=True,
            )
            # 尝试发送错误消息
            try:
                await self._cm.send_text(
                    conn.connection_id,
                    ServerMessage(
                        type=ServerMessageType.ERROR,
                        data={"message": str(e)},
                    ),
                )
            except Exception:
                pass
        finally:
            # 4. 清理连接
            await self._cleanup_connection(conn)

    async def _receive_loop(self, conn: Connection) -> None:
        """
        消息接收主循环

        WebSocket 消息分两种：
        - 文本帧（JSON）：控制消息，用 receive_text() 读取
        - 二进制帧：音频数据，用 receive_bytes() 读取

        FastAPI 的 websocket.receive() 返回一个 dict，
        包含 "type" 字段指示是文本还是二进制。
        """
        while conn.is_alive():
            try:
                # 使用底层 receive() 同时处理文本和二进制
                raw = await conn.websocket.receive()

                if raw["type"] == "websocket.disconnect":
                    break
                elif raw["type"] == "websocket.receive":
                    if "text" in raw and raw["text"]:
                        # 文本帧：JSON 控制消息
                        await self._handle_text_message(conn, raw["text"])
                    elif "bytes" in raw and raw["bytes"]:
                        # 二进制帧：音频数据
                        await self._handle_audio_data(conn, raw["bytes"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[{conn.connection_id}] 接收消息时出错：{e}",
                    exc_info=True,
                )
                break

    async def _handle_text_message(
        self, conn: Connection, raw_text: str
    ) -> None:
        """处理文本消息（JSON 格式）"""
        try:
            message = ClientMessage.from_json(raw_text)
            await self._router.dispatch(conn, message)
        except Exception as e:
            logger.warning(
                f"[{conn.connection_id}] 解析消息失败：{e}，原始：{raw_text[:100]}"
            )

    async def _handle_audio_data(self, conn: Connection, data: bytes) -> None:
        """处理音频数据（二进制帧）"""
        # 把音频数据发给 ASR 处理
        # 实际项目中这里会发布 AUDIO_CHUNK 事件到事件总线
        logger.debug(
            f"[{conn.connection_id}] 收到音频数据：{len(data)} bytes"
        )

    async def _cleanup_connection(self, conn: Connection) -> None:
        """清理连接资源"""
        self._cm.remove(conn.connection_id)
        # 如果有绑定的 session，通知 SessionManager 关闭 session
        if conn.session_id:
            logger.info(
                f"[{conn.connection_id}] 清理 session：{conn.session_id}"
            )

    async def send_audio(
        self, connection_id: str, audio_bytes: bytes
    ) -> None:
        """
        向指定连接发送 TTS 音频数据

        这个方法会被 TTS 管理器调用，把合成的音频推送给客户端。
        """
        await self._cm.send_bytes(connection_id, audio_bytes)

    async def send_message(
        self, connection_id: str, message: ServerMessage
    ) -> None:
        """向指定连接发送 JSON 消息"""
        await self._cm.send_text(connection_id, message)
