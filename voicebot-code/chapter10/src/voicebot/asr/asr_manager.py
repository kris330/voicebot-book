
import asyncio
import logging
import os
from typing import Optional

import numpy as np

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)


class ASRManager:
    """
    ASR 管理器：
    - 支持主/备双 ASR（主用云端，备用本地；或主用本地，备用云端）
    - 自动降级：主 ASR 失败时切换到备用 ASR
    - 统计识别成功率和延迟
    """

    def __init__(
        self,
        primary: Optional[BaseASR] = None,
        fallback: Optional[BaseASR] = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._stats = {
            "primary_success": 0,
            "primary_failure": 0,
            "fallback_used": 0,
        }

    @classmethod
    def from_env(cls) -> "ASRManager":
        """
        根据环境变量自动选择 ASR 配置

        环境变量：
          ASR_MODE=local | cloud | hybrid（默认 hybrid）
          ALIYUN_NLS_APP_KEY=...（cloud 模式需要）
        """
        mode = os.environ.get("ASR_MODE", "hybrid")

        if mode == "local":
            from voicebot.asr.sensevoice_asr import SenseVoiceASR
            return cls(primary=SenseVoiceASR())

        elif mode == "cloud":
            from voicebot.asr.aliyun_asr import AliyunASR
            return cls(primary=AliyunASR())

        else:  # hybrid：云端优先，本地兜底
            from voicebot.asr.aliyun_asr import AliyunASR
            from voicebot.asr.sensevoice_asr import SenseVoiceASR
            return cls(
                primary=AliyunASR(),
                fallback=SenseVoiceASR(),
            )

    async def init(self) -> None:
        """并发初始化所有 ASR 实例"""
        tasks = []
        if self._primary:
            tasks.append(self._primary.init())
        if self._fallback:
            tasks.append(self._fallback.init())

        await asyncio.gather(*tasks)
        logger.info(
            f"ASRManager 初始化完成，"
            f"主={self._primary.name if self._primary else 'None'}，"
            f"备={self._fallback.name if self._fallback else 'None'}"
        )

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """
        识别音频，主 ASR 失败时自动降级到备用 ASR
        """
        if self._primary:
            try:
                result = await asyncio.wait_for(
                    self._primary.transcribe(audio),
                    timeout=8.0,  # 云端 ASR 超时时间
                )
                self._stats["primary_success"] += 1
                return result
            except asyncio.TimeoutError:
                logger.warning(f"主 ASR ({self._primary.name}) 超时，切换到备用")
                self._stats["primary_failure"] += 1
            except Exception as e:
                logger.error(f"主 ASR ({self._primary.name}) 出错: {e}")
                self._stats["primary_failure"] += 1

        if self._fallback:
            logger.info(f"使用备用 ASR: {self._fallback.name}")
            self._stats["fallback_used"] += 1
            return await self._fallback.transcribe(audio)

        return ASRResult(text="", is_final=True)

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._stats["primary_success"] + self._stats["primary_failure"]
        success_rate = self._stats["primary_success"] / total if total > 0 else 0
        return {
            **self._stats,
            "primary_success_rate": f"{success_rate:.1%}",
        }

    async def close(self) -> None:
        tasks = []
        if self._primary:
            tasks.append(self._primary.close())
        if self._fallback:
            tasks.append(self._fallback.close())
        await asyncio.gather(*tasks)
