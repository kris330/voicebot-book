
import asyncio
import grpc
from typing import AsyncIterator
import logging

# 假设 proto 文件已经编译好
# from .grpc_pb import cosyvoice_pb2, cosyvoice_pb2_grpc

logger = logging.getLogger(__name__)


class CosyVoiceGRPCClient:
    """CosyVoice gRPC 客户端"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 50000,
        voice: str = "中文女",
        sample_rate: int = 22050,
    ) -> None:
        self.host = host
        self.port = port
        self.voice = voice
        self.sample_rate = sample_rate
        self._channel: grpc.aio.Channel | None = None
        self._stub = None

    async def connect(self) -> None:
        """建立 gRPC 连接"""
        self._channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
        # self._stub = cosyvoice_pb2_grpc.CosyVoiceStub(self._channel)
        logger.info(f"Connected to CosyVoice at {self.host}:{self.port}")

    async def close(self) -> None:
        """关闭连接"""
        if self._channel:
            await self._channel.close()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成"""
        if self._stub is None:
            raise RuntimeError("Not connected. Call connect() first.")

        # request = cosyvoice_pb2.Request(
        #     tts_text=text,
        #     spk_id=self.voice,
        # )
        # async for response in self._stub.Inference(request):
        #     if response.tts_audio:
        #         yield response.tts_audio
        #
        # 示意性代码，实际需要根据你的 proto 定义调整
        yield b""
