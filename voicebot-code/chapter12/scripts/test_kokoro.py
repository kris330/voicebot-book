
import asyncio
import soundfile as sf
import numpy as np
from voicebot.tts.kokoro_local import KokoroLocalTTS


async def main() -> None:
    tts = KokoroLocalTTS(voice="zf_xiaobei", speed=1.0)

    test_text = "你好，我是 VoiceBot，很高兴认识你。"
    print(f"正在合成：{test_text}")

    audio_bytes = await tts.synthesize(test_text)

    # 转换回 numpy array 并保存为 wav
    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
    sf.write("test_output.wav", audio_array, tts.get_sample_rate())

    duration = len(audio_array) / tts.get_sample_rate()
    print(f"合成完成！时长：{duration:.2f}秒，文件：test_output.wav")


asyncio.run(main())
