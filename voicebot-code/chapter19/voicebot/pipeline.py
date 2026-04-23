
async def _synthesize_and_enqueue(
    self,
    session: Session,
    text: str,
    record,
) -> None:
    """合成音频并放入队列，每次放入前检查是否已被打断。"""
    first_chunk = True

    try:
        async for audio_chunk in self._tts.synthesize_stream(text):
            # 检查 Session 是否已关闭或被打断
            if session.is_closed or getattr(session, "_interrupted", False):
                logger.debug(
                    f"[{session.session_id}] Session 已关闭/被打断，停止放入音频"
                )
                return

            if first_chunk:
                first_chunk = False
                if record.tts_first_chunk_at is None:
                    record.mark("tts_first_chunk")

            await session.tts_queue.put(audio_chunk)

            if record.audio_sent_at is None:
                record.mark("audio_sent")

    except asyncio.CancelledError:
        logger.debug(f"[{session.session_id}] TTS 合成被取消")
        raise
    except Exception as e:
        logger.error(
            f"[{session.session_id}] TTS 合成失败 ('{text[:20]}'): {e}"
        )
