
import asyncio
import logging
import uuid

from .asr.openai_asr import OpenAIASR
from .llm.openai_llm import OpenAILLM
from .tts.openai_tts import OpenAITTS
from .session import Session
from .latency import LatencyRecord, LatencyTracker

logger = logging.getLogger(__name__)
latency_tracker = LatencyTracker()

TTS_TRIGGER_PUNCTUATION = {"。", "！", "？", "，", "；", "…", ".", "!", "?", ","}
TTS_FORCE_TRIGGER_CHARS = 50


class VoicePipeline:

    def __init__(self, asr: OpenAIASR, llm: OpenAILLM, tts: OpenAITTS) -> None:
        self._asr = asr
        self._llm = llm
        self._tts = tts

    async def process(self, session: Session, audio_data: bytes) -> None:
        request_id = str(uuid.uuid4())[:8]
        record = latency_tracker.new_record(session.session_id, request_id)

        # ① VAD 结束时刻（这里我们从收到请求开始算，VAD 延迟在客户端）
        record.mark("vad_end")

        # ② 服务端收到完整音频
        record.mark("audio_received")

        logger.info(
            f"[{session.session_id}] 开始处理请求 {request_id}，"
            f"音频大小: {len(audio_data)} bytes"
        )

        # ③ ASR 识别
        user_text = await self._asr.transcribe(audio_data)
        record.mark("asr_done")

        asr_ms = record.elapsed_ms("audio_received", "asr_done")
        logger.info(f"[{session.session_id}] ASR 完成: '{user_text}' ({asr_ms:.0f}ms)")

        if not user_text:
            return

        session.add_user_message(user_text)

        import json
        await session.websocket.send(json.dumps({
            "type": "asr_result",
            "text": user_text,
        }))

        # ④⑤⑥ LLM + TTS 流水线
        pipeline_task = asyncio.create_task(
            self._llm_tts_pipeline(session, record),
            name=f"pipeline-{session.session_id}"
        )
        session.current_llm_task = pipeline_task

        try:
            await pipeline_task
        except asyncio.CancelledError:
            logger.info(f"[{session.session_id}] 流水线已被打断")
        except Exception as e:
            logger.error(f"[{session.session_id}] 流水线错误: {e}", exc_info=True)

        # 打印延迟报告
        logger.info(record.report())

    async def _llm_tts_pipeline(self, session: Session, record: LatencyRecord) -> None:
        messages = session.get_llm_messages()
        full_response = []
        pending_text = ""
        first_token_received = False
        first_tts_triggered = False

        async for token in self._llm.generate_stream(messages):
            if not first_token_received:
                first_token_received = True
                record.mark("llm_first_token")
                llm_ttft = record.elapsed_ms("asr_done", "llm_first_token")
                logger.debug(
                    f"[{session.session_id}] LLM 首 token ({llm_ttft:.0f}ms)"
                )

            full_response.append(token)
            pending_text += token

            should_trigger = (
                any(p in pending_text for p in TTS_TRIGGER_PUNCTUATION)
                or len(pending_text) >= TTS_FORCE_TRIGGER_CHARS
            )

            if should_trigger:
                text_to_synthesize = pending_text.strip()
                pending_text = ""

                if text_to_synthesize:
                    if not first_tts_triggered:
                        first_tts_triggered = True
                        record.mark("tts_triggered")
                        text_accum_ms = record.elapsed_ms("llm_first_token", "tts_triggered")
                        logger.debug(
                            f"[{session.session_id}] TTS 触发，"
                            f"文字积累耗时 {text_accum_ms:.0f}ms: "
                            f"'{text_to_synthesize[:30]}'"
                        )

                    await self._synthesize_and_enqueue(session, text_to_synthesize, record)

        if pending_text.strip():
            if not first_tts_triggered:
                record.mark("tts_triggered")
            await self._synthesize_and_enqueue(session, pending_text.strip(), record)

        full_response_text = "".join(full_response)
        session.add_assistant_message(full_response_text)

        import json
        await session.websocket.send(json.dumps({"type": "tts_end"}))

    async def _synthesize_and_enqueue(
        self,
        session: Session,
        text: str,
        record: LatencyRecord,
    ) -> None:
        first_chunk = True

        try:
            async for audio_chunk in self._tts.synthesize_stream(text):
                if session.is_closed:
                    return

                if first_chunk:
                    first_chunk = False
                    record.mark("tts_first_chunk")
                    tts_first_ms = record.elapsed_ms("tts_triggered", "tts_first_chunk")
                    logger.debug(
                        f"[{session.session_id}] TTS 首帧 ({tts_first_ms:.0f}ms)"
                    )

                await session.tts_queue.put(audio_chunk)

                # 记录第一帧音频入队（近似为"发出"的时刻）
                if record.audio_sent_at is None:
                    record.mark("audio_sent")

        except asyncio.CancelledError:
            raise
