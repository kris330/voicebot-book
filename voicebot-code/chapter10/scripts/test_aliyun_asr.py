
import asyncio
import wave
import numpy as np
import os

from voicebot.asr.aliyun_asr import AliyunASR


async def main():
    # 设置环境变量（实际使用时放在 .env 文件里）
    os.environ["ALIYUN_NLS_APP_KEY"] = "你的 AppKey"
    os.environ["ALIYUN_ACCESS_KEY_ID"] = "你的 AccessKeyId"
    os.environ["ALIYUN_ACCESS_KEY_SECRET"] = "你的 AccessKeySecret"

    # 读取测试音频
    with wave.open("test_audio/sample.wav", "r") as f:
        assert f.getframerate() == 16000, "需要 16kHz 音频"
        assert f.getnchannels() == 1, "需要单声道音频"
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    asr = AliyunASR()
    await asr.init()

    print("开始识别...")
    result = await asr.transcribe(audio)
    print(f"识别结果: {result.text!r}")
    print(f"置信度: {result.confidence}")


if __name__ == "__main__":
    asyncio.run(main())
