
import asyncio
import logging
from voicebot.tts.kokoro_local import KokoroLocalTTS
from voicebot.tts.manager import TTSManager

logging.basicConfig(level=logging.INFO)


async def demo_streaming_tts() -> None:
    """演示流式 TTS 合成"""

    # 初始化 TTS 引擎
    engine = KokoroLocalTTS(voice="zf_xiaobei", speed=1.0)

    # 创建 TTS 管理器，目标采样率 16kHz
    manager = TTSManager(engine=engine, target_sample_rate=16000)

    # 模拟 LLM 的一段输出
    llm_response = """
    好的！Python 是一门非常适合初学者的编程语言。
    它的语法简洁清晰，读起来几乎像英文一样。
    你可以用它做数据分析、网站开发、自动化脚本，甚至人工智能！
    我建议你从基础语法开始，比如变量、循环、函数这些概念。
    有什么具体想学的方向吗？
    """

    total_bytes = 0
    chunk_count = 0

    print("开始流式 TTS 合成...")
    async for audio_chunk in manager.speak(llm_response):
        total_bytes += len(audio_chunk)
        chunk_count += 1
        print(f"收到音频块 {chunk_count}：{len(audio_chunk)} bytes")
        # 实际使用中这里会把音频块发送给播放器

    print(f"\n合成完成！共 {chunk_count} 个音频块，总计 {total_bytes} bytes")

    # 计算时长
    duration_s = total_bytes / (16000 * 2)  # 16kHz, 16-bit
    print(f"音频总时长：{duration_s:.2f} 秒")


asyncio.run(demo_streaming_tts())
