
import asyncio
import wave
import numpy as np
import time

from voicebot.asr.sensevoice_asr import SenseVoiceASR


async def benchmark():
    """测试 SenseVoice 的识别准确率和速度"""

    asr = SenseVoiceASR(model_name="iic/SenseVoiceSmall", device="cpu")

    print("加载模型中...")
    load_start = time.monotonic()
    await asr.init()
    print(f"模型加载耗时: {(time.monotonic() - load_start) * 1000:.0f}ms")

    # 测试音频列表
    test_cases = [
        ("test_audio/short_sentence.wav", "今天天气怎么样"),
        ("test_audio/long_sentence.wav", "帮我查一下明天北京到上海的高铁票"),
        ("test_audio/numbers.wav", "我需要预订3间房间，价格在500元以内"),
    ]

    for audio_file, expected in test_cases:
        with wave.open(audio_file, "r") as f:
            audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
            duration_ms = len(audio) / 16000 * 1000

        infer_start = time.monotonic()
        result = await asr.transcribe(audio)
        infer_ms = (time.monotonic() - infer_start) * 1000

        rtf = infer_ms / duration_ms  # 实时率（< 1 才能用于实时场景）

        print(f"\n音频时长: {duration_ms:.0f}ms")
        print(f"推理耗时: {infer_ms:.0f}ms (RTF={rtf:.3f})")
        print(f"预期: {expected!r}")
        print(f"识别: {result.text!r}")

    await asr.close()


if __name__ == "__main__":
    asyncio.run(benchmark())
