import wave
import numpy as np


def inspect_wav(file_path: str) -> None:
    """打印一个 WAV 文件的完整信息。"""
    with wave.open(file_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()  # 字节数
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    samples = np.frombuffer(raw, dtype=np.int16)

    if n_channels == 2:
        samples_2d = samples.reshape(-1, 2)
        L = samples_2d[:, 0]
        R = samples_2d[:, 1]
    else:
        L = samples
        R = None

    duration = n_frames / sample_rate

    print(f"文件：{file_path}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"采样率：{sample_rate} Hz")
    print(f"位深：{sample_width * 8}-bit")
    print(f"声道数：{n_channels} ({'立体声' if n_channels == 2 else '单声道'})")
    print(f"总帧数：{n_frames:,}")
    print(f"时长：{duration:.2f} 秒")
    print(f"文件大小：{len(raw) / 1024 / 1024:.1f} MB（PCM 部分）")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    def channel_stats(ch: np.ndarray, name: str) -> None:
        nonzero = np.count_nonzero(ch)
        silence_ratio = 1.0 - nonzero / len(ch)
        print(f"{name}：")
        print(f"  最大振幅：{np.abs(ch).max()}")
        print(f"  平均振幅：{np.abs(ch).mean():.1f}")
        print(f"  静音比例：{silence_ratio:.1%}")

    channel_stats(L, "左声道（用户）" if R is not None else "声道")
    if R is not None:
        channel_stats(R, "右声道（TTS）")


if __name__ == "__main__":
    import sys
    inspect_wav(sys.argv[1])
