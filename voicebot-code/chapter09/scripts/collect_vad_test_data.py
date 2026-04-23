"""
收集真实用户的语音数据，用于测试 VAD 参数
"""

import asyncio
import wave
import numpy as np
from datetime import datetime

async def record_and_save(duration_sec: int = 30, output_dir: str = "test_audio"):
    """录制一段音频并保存，标注每句话的开始和结束时间"""
    import sounddevice as sd

    sample_rate = 16000
    audio = sd.rec(
        int(duration_sec * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype='int16'
    )

    print(f"开始录制 {duration_sec} 秒，请正常说话...")
    sd.wait()
    print("录制完成")

    filename = f"{output_dir}/test_{datetime.now().strftime('%H%M%S')}.wav"
    with wave.open(filename, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # int16 = 2 bytes
        f.setframerate(sample_rate)
        f.writeframes(audio.tobytes())

    print(f"已保存到 {filename}")
    return filename
