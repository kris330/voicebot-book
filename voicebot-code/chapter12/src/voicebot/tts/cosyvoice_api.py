
import asyncio
import aiohttp
import json
from typing import AsyncIterator
import logging

logger = logging.getLogger(__name__)


class CosyVoiceAPIClient:
    """CosyVoice 云端 API 客户端"""

    def __init__(
        self,
        api_url: str,
        voice: str = "longxiaochun",
        sample_rate: int = 22050,
    ) -> None:
        self.api_url = api_url
        self.voice = voice
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> bytes:
        """批量合成：返回完整音频 bytes"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": text,
                "voice": self.voice,
                "format": "pcm",
                "sample_rate": self.sample_rate,
            }
            async with session.post(
                f"{self.api_url}/synthesize",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.read()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成：逐块返回音频数据"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": text,
                "voice": self.voice,
                "format": "pcm",
                "sample_rate": self.sample_rate,
                "stream": True,
            }
            async with session.post(
                f"{self.api_url}/synthesize_stream",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        yield chunk
