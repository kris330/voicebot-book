
import asyncio
import logging
from collections.abc import AsyncGenerator

from .asr.openai_asr import OpenAIASR
from .llm.openai_llm import OpenAILLM
from .tts.openai_tts import OpenAITTS
from .session import Session

logger = logging.getLogger(__name__)

# 触发 TTS 合成的最小文本单位
# 遇到这些标点符号时，立即把前面积累的文字送去 TTS
TTS_TRIGGER_PUNCTUATION = {"。", "！", "？", "，", "；", "…", ".", "!", "?", ","}

# 积累多少字符后强制触发 TTS（即使没遇到标点）
TTS_FORCE_TRIGGER_CHARS = 50


class VoicePipeline:
    """
    ASR → LLM → TTS 完整流水线。

    核心思路：
    1. 用 ASR 把音频转成文字
    2. 把文字送给 LLM，流式获取回复
    3. 不等 LLM 全部输出完——第一个句子出来就立刻送给 TTS
    4. TTS 流式合成，第一帧音频出来就放入 Session 的 TTS 队列
    5. 独立的发送协程从队列里取音频，发给客户端
    """

    def __init__(self, asr: OpenAIASR, llm: OpenAILLM, tts: OpenAITTS) -> None:
        self._asr = asr
        self._llm = llm
        self._tts = tts

    async def process(self, session: Session, audio_data: bytes) -> None:
        """
        处理一次完整的语音输入。

        Args:
            session: 当前会话
            audio_data: 用户的语音 PCM 数据
        """
        # 步骤 1：ASR 识别
        logger.info(f"[{session.session_id}] 开始 ASR 识别...")
        user_text = await self._asr.transcribe(audio_data)

        if not user_text:
            logger.info(f"[{session.session_id}] ASR 结果为空，跳过")
            return

        # 把用户消息加入历史
        session.add_user_message(user_text)

        # 通知客户端识别结果（可选，用于显示字幕）
        import json
        await session.websocket.send(json.dumps({
            "type": "asr_result",
            "text": user_text,
        }))

        # 步骤 2 & 3：LLM 生成 + TTS 合成（同步流水线）
        logger.info(f"[{session.session_id}] 开始 LLM 生成...")

        # 创建 LLM + TTS 流水线任务
        pipeline_task = asyncio.create_task(
            self._llm_tts_pipeline(session),
            name=f"pipeline-{session.session_id}"
        )
        session.current_llm_task = pipeline_task

        try:
            await pipeline_task
        except asyncio.CancelledError:
            logger.info(f"[{session.session_id}] 流水线已被取消（打断）")
        except Exception as e:
            logger.error(f"[{session.session_id}] 流水线错误: {e}", exc_info=True)

    async def _llm_tts_pipeline(self, session: Session) -> None:
        """
        LLM 生成 → 按句子切分 → TTS 合成 → 放入播放队列。

        关键优化：不等 LLM 全部输出完，第一句就送 TTS。
        """
        messages = session.get_llm_messages()
        full_response = []
        pending_text = ""  # 积累中的文字，等待凑够一句话

        async for token in self._llm.generate_stream(messages):
            full_response.append(token)
            pending_text += token

            # 判断是否应该触发 TTS
            should_trigger = (
                any(p in pending_text for p in TTS_TRIGGER_PUNCTUATION)
                or len(pending_text) >= TTS_FORCE_TRIGGER_CHARS
            )

            if should_trigger:
                text_to_synthesize = pending_text.strip()
                pending_text = ""

                if text_to_synthesize:
                    logger.debug(
                        f"[{session.session_id}] TTS 触发: '{text_to_synthesize[:30]}...'"
                    )
                    await self._synthesize_and_enqueue(session, text_to_synthesize)

        # 处理最后剩余的文字
        if pending_text.strip():
            await self._synthesize_and_enqueue(session, pending_text.strip())

        # 记录完整回复到对话历史
        full_response_text = "".join(full_response)
        session.add_assistant_message(full_response_text)

        # 发送结束信号
        import json
        await session.websocket.send(json.dumps({
            "type": "tts_end",
        }))

        logger.info(f"[{session.session_id}] 流水线完成，回复长度: {len(full_response_text)} 字")

    async def _synthesize_and_enqueue(self, session: Session, text: str) -> None:
        """
        合成一段文字，把音频块逐一放入 TTS 队列。
        """
        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                # 检查是否被取消
                if session.is_closed:
                    return
                await session.tts_queue.put(audio_chunk)
        except asyncio.CancelledError:
            raise  # 向上传播，让调用方知道被取消了
        except Exception as e:
            logger.error(
                f"[{session.session_id}] TTS 合成失败 ('{text[:20]}...'): {e}"
            )
