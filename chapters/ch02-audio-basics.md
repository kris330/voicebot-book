# 第二章：音频信号基础

## 你的麦克风采集到的数据，到底长什么样

---

在写任何音频处理代码之前，你需要先回答一个问题：**声音在计算机里是怎么存储的？**

这不是在刁难你。如果你跳过这一章，等你遇到"ASR 识别率很差"、"TTS 播放出来是噪音"、"两段音频拼起来有爆音"这些问题的时候，你会完全不知道从哪里排查。

这一章没有任何高深的数学，只有你需要知道的最基础的概念，以及用 Python 直接操作音频文件的方法。

---

## 2.1 声音是如何变成数字的

声音的本质是空气的振动——你的声带振动，带动周围空气压缩和膨胀，这个压力变化以波的形式传播到麦克风的振膜，振膜随之振动，最终被转换成电压信号。

但计算机只能处理数字，所以需要把这个连续的电压信号变成离散的数字。这个过程叫做 **模数转换（ADC，Analog-to-Digital Conversion）**，分两步完成：

**第一步：采样（Sampling）**

每隔固定的时间间隔，记录一次当前的电压值。采样的频率叫做**采样率（Sample Rate）**，单位是 Hz（每秒采样次数）。

```
原始声波（连续）：
       ╭─╮   ╭──╮  ╭╮
──────╯  ╰───╯  ╰──╯ ╰─────
时间 →

采样后（16000 Hz，每秒 16000 个点）：
  •  •  •  •  •  •  •  •
──•──────•────•──•─────•──
时间 →
```

**第二步：量化（Quantization）**

把每个采样点的值用整数表示。用多少位来表示，叫做**位深（Bit Depth）**。16-bit 意味着每个采样点的值在 -32768 到 32767 之间。

这两步完成之后，原本连续的声音就变成了一串整数，这就是 **PCM（Pulse Code Modulation，脉冲编码调制）**——音频的最基础存储格式。

---

## 2.2 三个你必须知道的参数

### 采样率（Sample Rate）

常见的采样率：

| 采样率 | 常见用途 |
|--------|----------|
| 8000 Hz | 电话语音（PSTN） |
| **16000 Hz** | **ASR 模型的标准输入** |
| 22050 Hz | 部分 TTS 模型输出 |
| **24000 Hz** | **现代 TTS 模型的常用输出** |
| 44100 Hz | CD 音质 |
| **48000 Hz** | **浏览器麦克风的默认采样率** |

> **VoiceBot 里会用到的规则：**
> - 浏览器麦克风采集：48000 Hz
> - 发给 ASR 前，需要降采样到：16000 Hz
> - TTS 输出（Kokoro）：24000 Hz
> - 录音文件（RecordingManager）：48000 Hz

采样率不匹配是 VoiceAI 系统里最常见的 bug 之一。一段 24000 Hz 的音频，如果被当成 48000 Hz 来播放，听起来就是 **2 倍速**——这是因为播放器每秒取 48000 个采样点，但实际上只有 24000 个，相当于用双倍速度消费数据。

### 位深（Bit Depth）

本书统一使用 **16-bit**（int16），这是 ASR 和 TTS 模型最常见的要求。浮点格式（float32）会在内部计算中用到，但对外传输时通常转成 int16。

16-bit 的取值范围是 -32768 到 32767。音量越大，数值的绝对值越大；完全静音时，所有采样点的值都是 0。

### 声道数（Channels）

- **单声道（Mono）**：一个声道，一组采样数据
- **立体声（Stereo）**：两个声道，左右各一组

VoiceAI 系统里的音频通常是**单声道**：麦克风采集是单声道，ASR 输入是单声道，TTS 输出也是单声道。

立体声的数据格式是两个声道的采样点**交错存放**：

```
立体声数据在内存里的排列：
[L0][R0][L1][R1][L2][R2][L3][R3]...
  ↑
左声道第 0 个采样点
```

这个细节在做录音分析时很重要（第 1 章提到的录音文件就是这种格式）。

---

## 2.3 WAV 文件格式

WAV 是最简单的音频文件格式，本质上是在 PCM 数据前面加了一个头部，描述这段音频的参数：

```
WAV 文件结构：
┌─────────────────────────────┐
│  文件头（44 字节）            │
│  - 采样率：16000             │
│  - 位深：16                  │
│  - 声道数：1                 │
│  - 总采样数：...              │
├─────────────────────────────┤
│  PCM 数据                    │
│  -9800, 1200, -450, 3300... │
└─────────────────────────────┘
```

读 WAV 文件，就是读这个头部拿到参数，然后读 PCM 数据做处理。

---

## 2.4 用 Python 操作音频

Python 标准库的 `wave` 模块可以直接读写 WAV 文件，配合 `numpy` 可以方便地做数值运算。

### 安装依赖

```bash
pip install numpy
```

（`wave` 是标准库，不需要安装。）

### 读取 WAV 文件

```python
import wave
import numpy as np

def read_wav(file_path: str) -> tuple[np.ndarray, int]:
    """
    读取 WAV 文件，返回 (音频数据, 采样率)。
    音频数据是 np.int16 类型的一维数组（单声道）或二维数组（立体声）。
    """
    with wave.open(file_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()

        raw_bytes = wf.readframes(n_frames)

    # 将字节数据转换为 int16 数组
    samples = np.frombuffer(raw_bytes, dtype=np.int16)

    # 立体声：将交错数组 reshape 成 (n_frames, 2)
    if n_channels == 2:
        samples = samples.reshape(-1, 2)

    return samples, sample_rate


# 使用示例
samples, sr = read_wav("recording.wav")
print(f"采样率：{sr} Hz")
print(f"时长：{len(samples) / sr:.2f} 秒")
print(f"采样点数：{len(samples)}")
print(f"最大振幅：{np.abs(samples).max()}")
```

### 写入 WAV 文件

```python
import wave
import numpy as np

def write_wav(file_path: str, samples: np.ndarray, sample_rate: int) -> None:
    """
    将 int16 音频数据写入 WAV 文件。
    samples: 一维数组（单声道）或 shape=(n, 2) 的二维数组（立体声）
    """
    # 确保是 int16 类型
    samples = samples.astype(np.int16)

    # 判断声道数
    if samples.ndim == 1:
        n_channels = 1
    else:
        n_channels = samples.shape[1]
        # 立体声需要重新交错
        samples = samples.flatten()

    with wave.open(file_path, "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(2)          # 16-bit = 2 字节
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())


# 使用示例：生成一段 1 秒的 440Hz 正弦波（标准音 A）
sr = 16000
duration = 1.0
t = np.linspace(0, duration, int(sr * duration), endpoint=False)
sine_wave = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

write_wav("a440.wav", sine_wave, sr)
print("已生成 a440.wav")
```

### 重采样

将音频从一个采样率转换到另一个采样率。VoiceBot 里最常见的操作：48000 Hz → 16000 Hz（发给 ASR 之前）。

```python
import numpy as np

def resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """
    简单的线性插值重采样。
    生产环境建议使用 soxr 或 librosa 获得更好的音质。
    """
    if src_sr == dst_sr:
        return samples

    samples_float = samples.astype(np.float32)
    old_n = len(samples_float)
    new_n = int(round(old_n * dst_sr / src_sr))

    x_old = np.linspace(0.0, 1.0, old_n, endpoint=False)
    x_new = np.linspace(0.0, 1.0, new_n, endpoint=False)

    resampled = np.interp(x_new, x_old, samples_float)
    return np.clip(resampled, -32768, 32767).astype(np.int16)


# 示例：48kHz → 16kHz
samples_48k, _ = read_wav("mic_48k.wav")
samples_16k = resample(samples_48k, src_sr=48000, dst_sr=16000)
write_wav("asr_input_16k.wav", samples_16k, 16000)

print(f"原始：{len(samples_48k)} 采样点，{len(samples_48k)/48000:.2f}s")
print(f"重采样后：{len(samples_16k)} 采样点，{len(samples_16k)/16000:.2f}s")
# 时长应该相同，采样点数减少为 1/3
```

---

## 2.5 用代码看懂音频

光看数字不够直观，我们来把音频画出来，建立感性认识。

```python
import wave
import numpy as np
import matplotlib.pyplot as plt


def plot_waveform(file_path: str) -> None:
    """绘制音频波形图。"""
    samples, sr = read_wav(file_path)

    # 单声道直接用，立体声取左声道
    if samples.ndim == 2:
        left = samples[:, 0]
        right = samples[:, 1]
    else:
        left = samples
        right = None

    duration = len(left) / sr
    time_axis = np.linspace(0, duration, len(left))

    fig, axes = plt.subplots(2 if right is not None else 1, 1, figsize=(12, 4))

    if right is None:
        axes = [axes]

    axes[0].plot(time_axis, left, linewidth=0.5, color="#2196F3")
    axes[0].set_ylabel("振幅")
    axes[0].set_title(f"{'左声道（用户）' if right is not None else '波形'}")
    axes[0].axhline(y=0, color="gray", linewidth=0.5)
    axes[0].set_ylim(-33000, 33000)

    if right is not None:
        axes[1].plot(time_axis, right, linewidth=0.5, color="#4CAF50")
        axes[1].set_ylabel("振幅")
        axes[1].set_title("右声道（TTS）")
        axes[1].axhline(y=0, color="gray", linewidth=0.5)
        axes[1].set_ylim(-33000, 33000)

    axes[-1].set_xlabel("时间（秒）")
    plt.tight_layout()
    plt.savefig(file_path.replace(".wav", "_waveform.png"), dpi=150)
    plt.show()
    print(f"波形图已保存")


# 运行（需要 matplotlib：pip install matplotlib）
plot_waveform("logs/session_audio/your_recording.wav")
```

用这段代码打开你在第 1 章录制的对话音频，你会看到：

```
左声道（用户）：
│    ∧∧       ∧                           │
│───╯  ╰──∧──╯╰────────────────────────── │  ← 用户说话时有波形
│                                         │
│──────────────────────────────────────── │  ← 静音时是平线

右声道（TTS）：
│                        ∧∧∧  ∧∧         │
│────────────────────────╯  ╰─╯╰───────── │  ← AI 回复时有波形
│                                         │
│──────────────────────────────────────── │  ← 其他时候是平线
```

用户说话 → AI 回复 → 用户说话 —— 这就是一次正常对话的波形结构。

---

## 2.6 几个常见问题

**Q：采样率写错了会怎样？**

最常见的表现是音频播放速度不对：
- 实际 24kHz，声明为 48kHz → **2 倍速播放**（音调升高、速度加快）
- 实际 48kHz，声明为 24kHz → **0.5 倍速播放**（音调降低、速度减慢）

遇到"合成出来的声音很奇怪"，先确认采样率是否匹配。

**Q：float32 和 int16 有什么区别，什么时候用哪个？**

| 格式 | 取值范围 | 典型用途 |
|------|----------|----------|
| `int16` | -32768 到 32767 | 存储、传输、ASR/TTS 输入 |
| `float32` | -1.0 到 1.0 | 内部运算（神经网络、信号处理） |

转换方式：
```python
# int16 → float32（归一化到 [-1, 1]）
audio_float = samples_int16.astype(np.float32) / 32768.0

# float32 → int16
audio_int16 = (audio_float * 32767).clip(-32768, 32767).astype(np.int16)
```

大多数 Python 音频库（soundfile、librosa）内部使用 float32，与外部系统交互时使用 int16。

**Q：bytes 和 ndarray 怎么互转？**

```python
# bytes → ndarray（从 WebSocket 收到的音频数据）
samples = np.frombuffer(audio_bytes, dtype=np.int16)

# ndarray → bytes（发送给 WebSocket 或写入文件）
audio_bytes = samples.astype(np.int16).tobytes()
```

这两行代码在 VoiceBot 的服务端代码里会频繁出现，记住它们。

---

## 2.7 实践：分析一段录音

把这些知识串起来，写一个简单的音频分析工具：

```python
# audio_inspector.py
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
```

运行：

```bash
python audio_inspector.py logs/session_audio/20260303_152706_885.wav
```

输出示例：

```
文件：logs/session_audio/20260303_152706_885.wav
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
采样率：48000 Hz
位深：16-bit
声道数：2 (立体声)
总帧数：8,098,416
时长：168.72 秒
文件大小：30.9 MB（PCM 部分）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
左声道（用户）：
  最大振幅：18158
  平均振幅：140.1
  静音比例：41.2%
右声道（TTS）：
  最大振幅：26759
  平均振幅：529.7
  静音比例：79.6%
```

读懂这些数字：
- 用户的平均振幅（140）比 TTS（529）低约 4 倍——麦克风收音加上网络传输，人声的音量通常比合成语音低
- 右声道（TTS）静音比例 79.6%——这很正常，AI 说话的时间只占整个对话时长的约 20%

---

## 2.8 本章小结

本章建立了音频的基础认知：

- **PCM**：声音的数字表示，就是一串整数
- **采样率**：每秒采多少个点，VoiceBot 里会遇到 48kHz（麦克风）、16kHz（ASR）、24kHz（TTS）
- **位深**：每个点用多少位表示，本书统一使用 16-bit
- **声道数**：单声道（ASR/TTS 处理）和立体声（录音存档）
- **WAV 格式**：头部 + PCM 数据，Python `wave` 模块直接读写
- **重采样**：采样率转换，VoiceBot 里的必要操作

掌握这些之后，"音频在 VoiceBot 里流转"的过程就变得清晰了：

```
浏览器麦克风 → 48kHz float32
     ↓ 降采样 + 转 int16
WebSocket 传输 → 16kHz int16
     ↓ 字节流解包
ASR 模型输入 → 16kHz int16 numpy array
     ...
TTS 模型输出 → 24kHz int16
     ↓ 升采样（可选）
WebSocket 传回 → 24kHz int16
     ↓ 播放
浏览器扬声器
```

下一章，我们来看为什么这个系统必须用异步编程实现——以及什么是 `asyncio`。

---

> **本章代码**已包含在项目仓库的 `tools/` 目录下，可以直接拿来分析任意 WAV 文件。
