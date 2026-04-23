
import asyncio
import logging
import os
from typing import AsyncGenerator, Optional

import numpy as np

# 阿里云 NLS SDK
import nls

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)


class AliyunASR(BaseASR):
    """
    阿里云实时语音识别（NLS）

    文档：https://help.aliyun.com/document_detail/84435.html
    """

    # 阿里云 NLS 服务地址
    ENDPOINT = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"

    def __init__(
        self,
        app_key: Optional[str] = None,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
    ):
        self._app_key = app_key or os.environ["ALIYUN_NLS_APP_KEY"]
        self._access_key_id = access_key_id or os.environ["ALIYUN_ACCESS_KEY_ID"]
        self._access_key_secret = (
            access_key_secret or os.environ["ALIYUN_ACCESS_KEY_SECRET"]
        )
        self._token = None
        self._token_expiry = 0

    @property
    def name(self) -> str:
        return "AliyunASR"

    async def init(self) -> None:
        """获取访问 Token"""
        await self._refresh_token()
        logger.info(f"[{self.name}] 初始化完成")

    async def _refresh_token(self):
        """获取或刷新 NLS Token（有效期 24 小时）"""
        import time
        from aliyunsdkcore.client import AcsClient
        from aliyunsdknls_cloud_meta.request.v20180518 import CreateTokenRequest

        if self._token and time.time() < self._token_expiry - 300:
            return  # Token 还有 5 分钟以上，不需要刷新

        loop = asyncio.get_event_loop()

        def _get_token():
            client = AcsClient(
                self._access_key_id,
                self._access_key_secret,
                "cn-shanghai"
            )
            request = CreateTokenRequest.CreateTokenRequest()
            response = client.do_action_with_exception(request)
            import json
            data = json.loads(response)
            return data["Token"]["Id"], data["Token"]["ExpireTime"]

        self._token, self._token_expiry = await loop.run_in_executor(None, _get_token)
        logger.debug(f"[{self.name}] Token 刷新成功，有效期至 {self._token_expiry}")

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """
        对一段完整音频进行识别

        内部使用流式 API，但等待最终结果后才返回
        """
        if isinstance(audio, np.ndarray):
            audio_bytes = audio.astype(np.int16).tobytes()
        else:
            audio_bytes = audio

        await self._refresh_token()

        # 使用 asyncio.Event 等待识别完成
        result_event = asyncio.Event()
        final_text = ""
        error_msg = None

        def on_sentence_end(message, *args):
            nonlocal final_text
            import json
            data = json.loads(message)
            if data.get("result"):
                final_text = data["result"]

        def on_recognition_complete(message, *args):
            result_event.set()

        def on_error(message, *args):
            nonlocal error_msg
            error_msg = str(message)
            result_event.set()

        # 创建识别器
        recognizer = nls.NlsSpeechRecognizer(
            url=self.ENDPOINT,
            token=self._token,
            appkey=self._app_key,
            on_sentence_end=on_sentence_end,
            on_recognition_complete=on_recognition_complete,
            on_error=on_error,
        )

        # 在线程池里运行同步的阿里云 SDK
        loop = asyncio.get_event_loop()

        def _run_recognition():
            recognizer.start(
                aformat="pcm",
                sample_rate=16000,
                enable_punctuation_prediction=True,
                enable_inverse_text_normalization=True,
            )

            # 分块发送音频（每次 3200 字节 = 100ms@16kHz）
            chunk_size = 3200
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i:i + chunk_size]
                recognizer.send_audio(chunk)

            recognizer.stop()

        await loop.run_in_executor(None, _run_recognition)

        # 等待识别完成（最多 10 秒）
        try:
            await asyncio.wait_for(result_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] 识别超时")
            return ASRResult(text="", is_final=True, confidence=0.0)

        if error_msg:
            logger.error(f"[{self.name}] 识别错误: {error_msg}")
            return ASRResult(text="", is_final=True, confidence=0.0)

        return ASRResult(
            text=final_text.strip(),
            is_final=True,
            confidence=1.0,
        )

    async def transcribe_stream(self, audio_generator: AsyncGenerator):
        raise NotImplementedError("阿里云实时识别流式接口请使用 NlsStreamingRecognizer")

    async def close(self) -> None:
        pass
