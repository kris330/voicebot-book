import asyncio
import random


async def fake_asr(audio_bytes: bytes) -> str:
    """模拟 ASR：花费随机时间，返回识别文字"""
    await asyncio.sleep(random.uniform(0.3, 0.7))
    return "今天天气怎么样"


async def fake_llm_stream(text: str):
    """模拟 LLM 流式输出：逐 token 产出文字"""
    reply = "今天北京天气晴，气温十八度，适合出行。"
    for char in reply:
        await asyncio.sleep(0.05)   # 每个字间隔 50ms
        yield char


async def fake_tts(sentence: str) -> bytes:
    """模拟 TTS：合成一句话，返回音频字节"""
    await asyncio.sleep(0.2)
    return f"[audio:{sentence}]".encode()


async def handle_session(session_id: int) -> None:
    """处理一次完整的对话轮次"""
    print(f"[Session {session_id}] 开始处理")

    # 1. ASR：语音 → 文字
    user_text = await fake_asr(b"audio data")
    print(f"[Session {session_id}] ASR: {user_text}")

    # 2. LLM 流式输出 + 按句切分 → TTS
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
    sentence_buffer = ""
    sentence_enders = {"。", "！", "？", "…"}

    async def llm_to_tts_producer():
        nonlocal sentence_buffer
        async for token in fake_llm_stream(user_text):
            sentence_buffer += token
            # 遇到句末标点，把完整句子送入 TTS 队列
            if token in sentence_enders:
                print(f"[Session {session_id}] LLM -> TTS: {sentence_buffer}")
                await tts_queue.put(sentence_buffer)
                sentence_buffer = ""
        # 剩余内容
        if sentence_buffer.strip():
            await tts_queue.put(sentence_buffer)
        await tts_queue.put(None)   # 结束信号

    async def tts_consumer():
        while True:
            sentence = await tts_queue.get()
            if sentence is None:
                break
            audio = await fake_tts(sentence)
            print(f"[Session {session_id}] TTS 合成: {audio}")

    # LLM 生产者和 TTS 消费者并发运行
    await asyncio.gather(llm_to_tts_producer(), tts_consumer())
    print(f"[Session {session_id}] 对话完成")


async def main():
    # 模拟 3 个用户同时发起对话
    await asyncio.gather(
        handle_session(1),
        handle_session(2),
        handle_session(3),
    )

asyncio.run(main())
