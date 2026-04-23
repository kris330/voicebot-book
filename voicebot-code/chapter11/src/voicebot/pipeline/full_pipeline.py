
import asyncio
import logging
from typing import Optional

import numpy as np

from voicebot.vad.vad_manager import VADManager, VADConfig
from voicebot.asr.asr_manager import ASRManager
from voicebot.llm.agent import LLMAgent

logger = logging.getLogger(__name__)


class FullVoicePipeline:
    """
    完整的语音处理管道：
    音频输入 → VAD → ASR → LLMAgent → TTS（下一章）
    """

    def __init__(self):
        self._vad = VADManager(VADConfig())
        self._asr = ASRManager.from_env()
        self._llm = LLMAgent()

        # 回调函数
        self._on_user_speech: Optional[callable] = None  # VAD 检测到语音
        self._on_transcript: Optional[callable] = None   # ASR 识别完成
        self._on_llm_sentence: Optional[callable] = None # LLM 生成一个句子
        self._on_response_done: Optional[callable] = None # LLM 生成完成

        # 防止并发处理（用户说话时打断 AI 回复）
        self._current_response_task: Optional[asyncio.Task] = None

    async def init(self):
        await asyncio.gather(
            self._vad.init(),
            self._asr.init(),
        )
        logger.info("FullVoicePipeline 初始化完成")

    def on(self, event: str, callback: callable) -> "FullVoicePipeline":
        """注册事件回调（链式调用）"""
        setattr(self, f"_on_{event}", callback)
        return self

    async def process_audio(self, audio_chunk: bytes):
        """处理一帧音频"""
        chunk = np.frombuffer(audio_chunk, dtype=np.int16)
        segment = await self._vad.process_chunk(chunk)

        if segment is not None:
            # 用户说完了，取消正在进行的 AI 回复（打断功能）
            if self._current_response_task and not self._current_response_task.done():
                logger.info("[Pipeline] 检测到用户说话，打断 AI 回复")
                self._current_response_task.cancel()

            # 启动 ASR + LLM 处理（异步，不阻塞继续录音）
            self._current_response_task = asyncio.create_task(
                self._handle_speech(segment.audio)
            )

    async def _handle_speech(self, audio: np.ndarray):
        """处理一段语音：ASR → LLM → 触发 TTS"""
        try:
            # ASR 识别
            asr_result = await self._asr.transcribe(audio)

            if not asr_result.text.strip():
                logger.debug("[Pipeline] ASR 结果为空，忽略")
                return

            logger.info(f"[Pipeline] ASR: {asr_result.text!r}")

            if self._on_transcript:
                await self._on_transcript(asr_result.text)

            # LLM 生成回复
            async def on_sentence(sentence: str):
                """每生成一个句子，触发 TTS"""
                if self._on_llm_sentence:
                    await self._on_llm_sentence(sentence)

            full_response = await self._llm.chat_stream(
                user_input=asr_result.text,
                on_sentence=on_sentence,
            )

            if self._on_response_done:
                await self._on_response_done(full_response)

        except asyncio.CancelledError:
            logger.info("[Pipeline] 处理被取消（用户打断）")
        except Exception as e:
            logger.error(f"[Pipeline] 处理失败: {e}", exc_info=True)

    def new_session(self):
        """开始新会话，清除历史"""
        self._llm.clear_history()
        self._vad.reset()
        if self._current_response_task:
            self._current_response_task.cancel()
