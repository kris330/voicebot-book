
import wave
import numpy as np
import matplotlib.pyplot as plt


def analyze_audio(wav_file: str):
    with wave.open(wav_file, "r") as f:
        sample_rate = f.getframerate()
        channels = f.getnchannels()
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    print(f"采样率: {sample_rate} Hz {'✓' if sample_rate == 16000 else '✗ (需要16000)'}")
    print(f"声道数: {channels} {'✓' if channels == 1 else '✗ (需要单声道)'}")
    print(f"时长: {len(audio) / sample_rate:.2f} 秒")
    print(f"峰值振幅: {np.max(np.abs(audio))} (正常范围: 5000-25000)")
    print(f"均方根: {np.sqrt(np.mean(audio.astype(np.float32)**2)):.0f}")

    # 检测是否有裁剪（限幅失真）
    clipped = np.sum(np.abs(audio) >= 32700)
    if clipped > 0:
        print(f"⚠ 检测到 {clipped} 个采样点可能过载（振幅接近32767）")
    else:
        print("✓ 无明显过载")


analyze_audio("test_audio/sample.wav")
