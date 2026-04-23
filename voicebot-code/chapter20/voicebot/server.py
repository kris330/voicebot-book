
import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket
from .emotion import Emotion
from .emotion_pipeline import EmotionPipeline
from .tts.openai_tts import OpenAITTSWithEmotion
from .llm_client import LLMClient

logger = logging.getLogger(__name__)
app = FastAPI()


async def handle_voice_session(websocket: WebSocket) -> None:
    await websocket.accept()

    tts_engine = OpenAITTSWithEmotion(api_key="YOUR_KEY")
    pipeline = EmotionPipeline(tts_engine=tts_engine)
    llm_client = LLMClient()

    try:
        while True:
            # 接收用户语音（已经过 ASR 转成文字）
            data = await websocket.receive_json()
            user_text = data.get("text", "")

            if not user_text:
                continue

            logger.info(f"User said: {user_text}")

            # 定义情感检测回调，通知前端更新 UI
            def on_emotion(emotion: Emotion) -> None:
                # 这里用 asyncio.create_task 避免阻塞
                asyncio.create_task(
                    websocket.send_json({
                        "type": "emotion",
                        "value": emotion.value,
                        "name": emotion.name,
                    })
                )

            # 获取 LLM 流式输出
            llm_stream = llm_client.stream_chat(user_text)

            # 通过情感流水线生成音频
            audio_chunks = []
            async for audio_chunk in pipeline.process(
                llm_stream, on_emotion_detected=on_emotion
            ):
                # 实时发送音频块给前端
                await websocket.send_bytes(audio_chunk)

            # 发送结束信号
            await websocket.send_json({"type": "audio_end"})

    except Exception as e:
        logger.error(f"Session error: {e}", exc_info=True)
    finally:
        await websocket.close()
