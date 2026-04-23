# 第四章：浏览器麦克风采集

## 从"开口说话"开始

---

打开一个语音助手网页，点击那个麦克风按钮——浏览器弹出一个权限请求框，你点了"允许"，然后开始说话。

看起来就这么简单，对吧？

但在这几秒钟里，浏览器实际上做了相当多的事情：请求麦克风权限、打开音频设备、建立一条音频数据流水线、把模拟声波转成数字信号、把原始的 Float32 采样值输送给你的 JavaScript 代码……

更麻烦的是：浏览器默认以 **48kHz** 的采样率采集音频，而大多数 ASR（语音识别）模型期望的是 **16kHz**。如果你直接把 48kHz 的音频扔给 ASR，轻则识别率下降，重则直接报错。

所以，在音频真正"上路"去服务端之前，我们得先在浏览器端做好采集和预处理。

本章的目标是搭建 VoiceBot 的音频采集模块，包括：
- 用 `getUserMedia` 打开麦克风
- 用 Web Audio API 处理音频流
- 用 AudioWorklet 实现采样率转换和格式转换
- 处理各种可能出错的情况

---

## 4.1 getUserMedia：打开麦克风的钥匙

`getUserMedia` 是浏览器提供的 API，用来请求访问用户的媒体设备（摄像头、麦克风等）。它是一切的起点。

```javascript
// 最简单的用法
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
```

这一行代码背后发生了什么：

```
用户点击按钮
      ↓
浏览器弹出权限请求框
      ↓
用户点击"允许"
      ↓
浏览器打开麦克风设备
      ↓
返回一个 MediaStream 对象（音频流）
```

`getUserMedia` 返回的是一个 Promise，因为它需要等待用户做出权限决定。用户可能允许，也可能拒绝，所以我们必须处理这两种情况。

### 详细的音频约束配置

只传 `{ audio: true }` 是最简单的配置，实际项目里我们通常需要更细致的控制：

```javascript
const constraints = {
  audio: {
    // 采样率：优先请求 16kHz，但浏览器不一定支持
    // 大多数浏览器会忽略这个请求，默认用 48kHz
    sampleRate: { ideal: 16000 },

    // 声道数：单声道足够，立体声反而浪费带宽
    channelCount: { exact: 1 },

    // 回声消除：非常重要，防止麦克风采到扬声器播出的声音
    echoCancellation: true,

    // 降噪：去除背景噪音
    noiseSuppression: true,

    // 自动增益控制：让音量保持在合适范围
    autoGainControl: true,
  }
};

const stream = await navigator.mediaDevices.getUserMedia(constraints);
```

> **注意**：关于 `sampleRate: { ideal: 16000 }`——这个约束在大多数浏览器上会被忽略。Chrome 和 Firefox 通常固定以 48kHz 采集，无论你怎么请求。所以不要依赖浏览器帮你降采样，我们需要自己做。

### 错误处理

`getUserMedia` 有几种常见的错误情形：

| 错误名 | 含义 | 用户侧原因 |
|--------|------|-----------|
| `NotAllowedError` | 权限被拒绝 | 用户点了"拒绝"，或系统级权限被关闭 |
| `NotFoundError` | 设备不存在 | 没有麦克风，或麦克风未被识别 |
| `NotReadableError` | 设备被占用 | 其他应用正在使用麦克风 |
| `OverconstrainedError` | 约束无法满足 | 要求的参数设备不支持 |
| `SecurityError` | 安全限制 | 非 HTTPS 页面调用（生产环境必须 HTTPS）|

```javascript
async function openMicrophone() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: { exact: 1 },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    });
    return stream;
  } catch (err) {
    if (err.name === 'NotAllowedError') {
      throw new Error('麦克风权限被拒绝。请在浏览器设置中允许访问麦克风。');
    } else if (err.name === 'NotFoundError') {
      throw new Error('未检测到麦克风设备。请检查设备连接。');
    } else if (err.name === 'NotReadableError') {
      throw new Error('麦克风正在被其他应用占用，请关闭后重试。');
    } else if (err.name === 'SecurityError') {
      throw new Error('请使用 HTTPS 连接访问此页面。');
    } else {
      throw new Error(`打开麦克风失败：${err.message}`);
    }
  }
}
```

---

## 4.2 Web Audio API 架构

拿到 `MediaStream` 之后，我们需要用 **Web Audio API** 来处理里面的音频数据。

Web Audio API 的设计理念是**音频节点图（Audio Node Graph）**：音频从一个源节点流出，经过若干处理节点，最终到达目标节点。每个节点负责一件事，节点之间用 `.connect()` 方法连接。

```
AudioContext（音频上下文，整个系统的控制中心）
        │
        ▼
MediaStreamSourceNode   ← 麦克风流进来
        │
        ▼
AudioWorkletNode        ← 我们的自定义处理节点（降采样、格式转换）
        │
        ▼
   [数据输出到主线程]    ← 通过 MessagePort 发送给 WebSocket
```

### AudioContext：整个音频系统的心脏

`AudioContext` 是 Web Audio API 的核心对象，它管理：
- 音频采样率（通常是 48000 Hz）
- 所有音频节点的生命周期
- 音频处理的时钟

```javascript
// 创建 AudioContext
const audioContext = new AudioContext({
  // 我们希望低延迟，latencyHint 影响缓冲区大小
  latencyHint: 'interactive',
  // 不强制指定采样率，让浏览器用默认值（通常 48000）
});

console.log(`AudioContext 采样率：${audioContext.sampleRate} Hz`);
// 通常输出：AudioContext 采样率：48000 Hz
```

> **重要**：`AudioContext` 必须在用户交互（点击事件等）之后才能创建，否则浏览器会把它挂起（suspended 状态）。这是浏览器的自动播放政策，防止网页自动发出声音。

### 把麦克风流接入 AudioContext

```javascript
// 把 MediaStream 变成一个音频源节点
const sourceNode = audioContext.createMediaStreamSource(stream);

// 现在 sourceNode 就是音频图的起点，
// 麦克风采集的声音从这里流出
```

---

## 4.3 AudioWorklet：现代的音频处理方式

我们需要访问原始的音频采样值（PCM 数据），来做降采样和格式转换。Web Audio API 提供了两种方式：

| 方式 | 状态 | 特点 |
|------|------|------|
| `ScriptProcessorNode` | 已废弃 | 在主线程运行，阻塞 UI，有延迟 |
| `AudioWorkletNode` | 现代标准 | 在独立线程运行，低延迟，不阻塞 UI |

**结论：用 AudioWorklet，忘掉 ScriptProcessorNode。**

AudioWorklet 由两部分组成：
1. **Worklet 处理器**（运行在 AudioWorkletGlobalScope，独立线程）
2. **主线程控制器**（AudioWorkletNode，运行在主线程）

两者之间通过 **MessagePort** 通信。

```
主线程                          AudioWorklet 线程
   │                                    │
   │  AudioWorkletNode                  │  AudioWorkletProcessor
   │  （控制、收发消息）                  │  （处理音频数据）
   │                                    │
   │ ←────── MessagePort ──────────────→ │
   │  port.onmessage(处理好的PCM数据)    │  this.port.postMessage(数据)
   │                                    │
   │                         ┌──────────┘
   │                         │ process(inputs, outputs, parameters)
   │                         │ 每 128 个采样调用一次（约 2.67ms @ 48kHz）
```

### 编写 AudioWorklet 处理器

AudioWorklet 处理器需要写在一个单独的 JS 文件里，通过 URL 加载：

```javascript
// audio-processor.worklet.js
// 这个文件运行在 AudioWorklet 线程，不能访问 DOM 和大部分 Web API

class AudioProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();

    // 从主线程传来的初始化参数
    const { targetSampleRate, sourceSampleRate } = options.processorOptions;

    this.sourceSampleRate = sourceSampleRate || 48000;
    this.targetSampleRate = targetSampleRate || 16000;

    // 降采样比率：48000 / 16000 = 3
    // 每 3 个输入采样，保留 1 个输出采样
    this.resampleRatio = this.sourceSampleRate / this.targetSampleRate;

    // 用于累积不足一帧的采样
    this.inputBuffer = [];

    // 每次发送给主线程的帧大小（以目标采样率计）
    // 20ms 的数据：16000 * 0.02 = 320 个采样
    this.frameSizeInSamples = Math.round(this.targetSampleRate * 0.02);

    // 降采样后的输出缓冲
    this.outputBuffer = [];
  }

  /**
   * 线性插值降采样
   * 简单但效果不错，适合语音（不适合音乐）
   *
   * @param {Float32Array} input  原始采样（48kHz）
   * @returns {Float32Array}      降采样后的数据（16kHz）
   */
  resample(input) {
    const outputLength = Math.floor(input.length / this.resampleRatio);
    const output = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i++) {
      // 在原始数组中的位置（可能是小数）
      const srcPos = i * this.resampleRatio;
      const srcIndex = Math.floor(srcPos);
      const fraction = srcPos - srcIndex;

      // 线性插值：在两个相邻采样之间插值
      const sample1 = input[srcIndex] || 0;
      const sample2 = input[srcIndex + 1] || 0;
      output[i] = sample1 + (sample2 - sample1) * fraction;
    }

    return output;
  }

  /**
   * Float32 转 Int16
   * ASR 模型通常期望 16-bit PCM，范围 [-32768, 32767]
   * Float32 的范围是 [-1.0, 1.0]
   *
   * @param {Float32Array} float32Data
   * @returns {Int16Array}
   */
  float32ToInt16(float32Data) {
    const int16Data = new Int16Array(float32Data.length);
    for (let i = 0; i < float32Data.length; i++) {
      // 先把 float32 限制在 [-1, 1] 范围内（防止削波失真）
      const clamped = Math.max(-1, Math.min(1, float32Data[i]));
      // 转换：-1.0 → -32768，0 → 0，1.0 → 32767
      int16Data[i] = clamped < 0
        ? clamped * 32768
        : clamped * 32767;
    }
    return int16Data;
  }

  /**
   * AudioWorklet 的核心方法，每 128 个采样调用一次
   * 在 48kHz 下，128 / 48000 ≈ 2.67ms 调用一次
   *
   * @param {Float32Array[][]} inputs  输入音频数据（二维：[通道][采样]）
   * @param {Float32Array[][]} outputs 输出音频数据（我们不用，只读取输入）
   * @returns {boolean} 返回 true 保持处理器活跃
   */
  process(inputs, outputs) {
    // inputs[0] 是第一个输入，inputs[0][0] 是第一个通道
    const channelData = inputs[0][0];

    // 没有输入数据时跳过
    if (!channelData || channelData.length === 0) {
      return true;
    }

    // 把输入数据追加到内部缓冲
    for (let i = 0; i < channelData.length; i++) {
      this.inputBuffer.push(channelData[i]);
    }

    // 处理完整帧
    // 需要多少输入采样才能产生一帧输出？
    const inputSamplesPerFrame = Math.round(
      this.frameSizeInSamples * this.resampleRatio
    ); // 320 * 3 = 960

    while (this.inputBuffer.length >= inputSamplesPerFrame) {
      // 取出一帧的输入数据
      const chunk = new Float32Array(
        this.inputBuffer.splice(0, inputSamplesPerFrame)
      );

      // 1. 降采样：48kHz → 16kHz
      const resampled = this.resample(chunk);

      // 2. 格式转换：Float32 → Int16
      const int16Data = this.float32ToInt16(resampled);

      // 3. 发送给主线程（transferable object，零拷贝传输）
      this.port.postMessage(
        { type: 'audio_frame', data: int16Data },
        [int16Data.buffer]  // 把 buffer 的所有权转移给主线程
      );
    }

    // 返回 true 让处理器保持活跃
    return true;
  }
}

// 注册处理器，名字要和主线程创建 AudioWorkletNode 时用的一致
registerProcessor('audio-processor', AudioProcessor);
```

---

## 4.4 采样率转换详解

这里稍微深入一下采样率转换，因为这是很多人踩坑的地方。

### 为什么是 48kHz → 16kHz？

```
48000 Hz 采样率 → 每秒 48000 个数字
16000 Hz 采样率 → 每秒 16000 个数字

降采样比 = 48000 / 16000 = 3
理论上：每 3 个输入采样，取 1 个（简单抽取）
```

但是，简单抽取（每 3 个取 1 个）会产生**混叠失真（Aliasing）**。正确做法是先用低通滤波器滤掉高频成分，再抽取。

我们代码里用的线性插值是一种简化的低通滤波，对语音效果还不错（语音的主要能量在 300-3400 Hz，远低于 16kHz 的奈奎斯特频率 8kHz）。

### 可视化理解

```
原始 48kHz 信号（每格代表一个采样）：
│█│ │█│ │█│ │█│ │█│ │█│ │█│ │█│ │█│
 1   2   3   4   5   6   7   8   9

简单抽取（每 3 个取第 1 个）：
│█│           │█│           │█│
 1             4             7

线性插值（计算每 3 个采样的加权平均）：
│◆│           │◆│           │◆│
↑保留了相邻采样的信息，更平滑
```

### Float32 → Int16 的数学

```
Float32 范围：[-1.0, 1.0]（归一化的音频振幅）
Int16 范围：[-32768, 32767]（16位有符号整数）

转换公式：
  正数：int16 = float32 × 32767
  负数：int16 = float32 × 32768

示例：
  0.5  → 0.5  × 32767 = 16383
  -0.5 → -0.5 × 32768 = -16384
  0.0  → 0
  1.0  → 32767（最大值）
 -1.0  → -32768（最小值）
```

---

## 4.5 主线程控制器

处理器文件写好之后，主线程需要加载它，创建 `AudioWorkletNode`，并接收数据：

```javascript
// mic-capture.js
// 运行在主线程

export class MicCapture {
  constructor({ onAudioFrame, onError } = {}) {
    this.onAudioFrame = onAudioFrame || (() => {});
    this.onError = onError || console.error;

    this.audioContext = null;
    this.sourceNode = null;
    this.workletNode = null;
    this.stream = null;
    this.isCapturing = false;
  }

  /**
   * 开始采集麦克风音频
   */
  async start() {
    if (this.isCapturing) {
      console.warn('麦克风已在采集中');
      return;
    }

    try {
      // 1. 请求麦克风权限，获取音频流
      this.stream = await this._openMicrophone();

      // 2. 创建 AudioContext
      //    注意：必须在用户交互后创建，且只在这里创建一次
      this.audioContext = new AudioContext({
        latencyHint: 'interactive',
        // 不指定 sampleRate，让浏览器使用默认值（通常 48000）
      });

      const sourceSampleRate = this.audioContext.sampleRate;
      const targetSampleRate = 16000;

      console.log(`AudioContext 采样率：${sourceSampleRate} Hz`);

      // 3. 加载 AudioWorklet 处理器模块
      await this.audioContext.audioWorklet.addModule(
        '/static/js/audio-processor.worklet.js'
      );

      // 4. 创建音频源节点（把 MediaStream 接入音频图）
      this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);

      // 5. 创建 AudioWorkletNode（连接到处理器）
      this.workletNode = new AudioWorkletNode(
        this.audioContext,
        'audio-processor',  // 对应 registerProcessor 里的名字
        {
          processorOptions: {
            sourceSampleRate,
            targetSampleRate,
          },
          // 不需要把音频输出到扬声器，只需要处理数据
          numberOfOutputs: 0,
        }
      );

      // 6. 监听处理器发过来的音频帧
      this.workletNode.port.onmessage = (event) => {
        if (event.data.type === 'audio_frame') {
          // event.data.data 是 Int16Array（16kHz PCM 数据）
          this.onAudioFrame(event.data.data);
        }
      };

      // 7. 连接音频图：麦克风源 → 处理器
      this.sourceNode.connect(this.workletNode);

      // 注意：workletNode 没有 connect 到 audioContext.destination
      // 因为我们不需要把处理后的音频播放出来（那是 TTS 的事）

      this.isCapturing = true;
      console.log('麦克风采集已开始');

    } catch (err) {
      this.onError(err);
      await this._cleanup();
      throw err;
    }
  }

  /**
   * 停止采集
   */
  async stop() {
    if (!this.isCapturing) return;

    await this._cleanup();
    this.isCapturing = false;
    console.log('麦克风采集已停止');
  }

  /**
   * 打开麦克风，返回 MediaStream
   */
  async _openMicrophone() {
    const constraints = {
      audio: {
        channelCount: { exact: 1 },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }
    };

    try {
      return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      throw this._translateMediaError(err);
    }
  }

  /**
   * 把浏览器的原始错误翻译成友好的中文错误
   */
  _translateMediaError(err) {
    const messages = {
      'NotAllowedError':      '麦克风权限被拒绝。请在浏览器地址栏左侧点击锁形图标，允许访问麦克风。',
      'NotFoundError':        '未检测到麦克风设备，请检查设备是否正确连接。',
      'NotReadableError':     '麦克风正在被其他程序占用，请关闭其他程序后重试。',
      'OverconstrainedError': '麦克风不支持所需的配置，请尝试使用其他麦克风。',
      'SecurityError':        '安全限制：请使用 HTTPS 连接访问此页面。',
      'AbortError':           '麦克风访问被中断，请重试。',
    };

    const message = messages[err.name] || `打开麦克风失败：${err.message}`;
    const error = new Error(message);
    error.originalError = err;
    return error;
  }

  /**
   * 清理所有资源
   */
  async _cleanup() {
    // 断开音频节点连接
    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }

    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode.port.close();
      this.workletNode = null;
    }

    // 关闭 AudioContext
    if (this.audioContext && this.audioContext.state !== 'closed') {
      await this.audioContext.close();
      this.audioContext = null;
    }

    // 停止所有媒体轨道（真正释放麦克风）
    if (this.stream) {
      this.stream.getTracks().forEach(track => track.stop());
      this.stream = null;
    }
  }

  /**
   * 获取当前状态
   */
  getState() {
    return {
      isCapturing: this.isCapturing,
      sampleRate: this.audioContext?.sampleRate ?? null,
      audioContextState: this.audioContext?.state ?? 'closed',
    };
  }
}
```

---

## 4.6 完整集成示例

把上面的模块接入到 VoiceBot 的页面控制代码中：

```javascript
// main.js

import { MicCapture } from './mic-capture.js';

// 状态变量
let micCapture = null;
let isRecording = false;

// 页面上的按钮
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusEl = document.getElementById('status');
const errorEl = document.getElementById('error');

/**
 * 收到一帧 16kHz PCM 音频数据
 * @param {Int16Array} frame  320 个 Int16 采样，代表 20ms 的音频
 */
function handleAudioFrame(frame) {
  // 现在拿到的是 16kHz、16-bit、单声道的 PCM 数据
  // 后续章节会把这里改成：发给 VAD 做端点检测，再发给 WebSocket
  console.log(`收到音频帧：${frame.length} 个采样，${frame.byteLength} 字节`);
}

/**
 * 处理错误
 */
function handleError(err) {
  console.error('麦克风错误：', err);
  errorEl.textContent = err.message;
  errorEl.style.display = 'block';

  // 恢复按钮状态
  startBtn.disabled = false;
  stopBtn.disabled = true;
  isRecording = false;
}

/**
 * 开始录音
 */
async function startRecording() {
  if (isRecording) return;

  // 清除之前的错误
  errorEl.style.display = 'none';
  startBtn.disabled = true;
  statusEl.textContent = '正在打开麦克风...';

  // 创建麦克风采集器
  micCapture = new MicCapture({
    onAudioFrame: handleAudioFrame,
    onError: handleError,
  });

  try {
    await micCapture.start();

    isRecording = true;
    stopBtn.disabled = false;
    statusEl.textContent = '正在录音...';
  } catch (err) {
    // handleError 已经在 onError 回调里处理了
    // 这里只需要恢复按钮
    startBtn.disabled = false;
    statusEl.textContent = '录音失败';
  }
}

/**
 * 停止录音
 */
async function stopRecording() {
  if (!isRecording || !micCapture) return;

  await micCapture.stop();
  micCapture = null;
  isRecording = false;

  startBtn.disabled = false;
  stopBtn.disabled = true;
  statusEl.textContent = '已停止';
}

// 绑定按钮事件
startBtn.addEventListener('click', startRecording);
stopBtn.addEventListener('click', stopRecording);

// 初始状态
stopBtn.disabled = true;
```

对应的 HTML：

```html
<!-- index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>VoiceBot</title>
</head>
<body>
  <h1>VoiceBot 语音助手</h1>

  <div id="status">就绪</div>
  <div id="error" style="display:none; color:red;"></div>

  <button id="start-btn">开始说话</button>
  <button id="stop-btn">停止</button>

  <script type="module" src="/static/js/main.js"></script>
</body>
</html>
```

---

## 4.7 数据流全景

到这里，我们的音频采集模块已经完整了。回顾一下数据是怎么流动的：

```
麦克风硬件
    │ 模拟声波
    ↓
浏览器音频驱动
    │ 48000 Hz，Float32，单声道
    ↓
MediaStream
    │
    ↓
AudioContext.createMediaStreamSource()
    │ 转成音频节点
    ↓
MediaStreamSourceNode
    │ 128个采样/次，Float32，48kHz
    ↓
AudioWorkletNode（audio-processor）
    │ 线性插值降采样：48kHz → 16kHz
    │ 格式转换：Float32 → Int16
    │ 攒够 320 个采样（20ms）
    ↓
MessagePort.postMessage()
    │ Int16Array，16kHz，20ms/帧
    ↓
主线程 onAudioFrame 回调
    │
    ↓
[下一章：VAD 端点检测]
```

每 20ms，我们的 `handleAudioFrame` 就会收到一个 `Int16Array`，包含 320 个采样，这就是后续 VAD 和 WebSocket 传输的基本单位。

---

## 4.8 常见问题排查

### 问题：在本地 http:// 开发时无法访问麦克风

浏览器要求麦克风访问必须在安全上下文（HTTPS 或 localhost）下进行。

**解决方案：**
- 开发时：用 `localhost` 或 `127.0.0.1` 访问，这被视为安全上下文
- 生产时：必须部署 HTTPS

### 问题：AudioWorklet 加载失败（404 错误）

AudioWorklet 模块文件必须通过 URL 加载，不能用相对路径引用打包后的模块。

**解决方案：**
```javascript
// 确保文件路径正确，并且服务器能访问到它
await audioContext.audioWorklet.addModule('/static/js/audio-processor.worklet.js');
```

如果使用 webpack/vite 等打包工具，需要特殊配置把 worklet 文件单独输出：
```javascript
// vite.config.js
export default {
  // ...
  worker: {
    format: 'es',
  }
}
```

### 问题：麦克风采集到的声音很小或者很响

自动增益控制（`autoGainControl: true`）通常能解决这个问题。如果还是有问题，可以在 AudioWorklet 里加一个增益节点：

```javascript
// 在主线程，创建增益节点
const gainNode = audioContext.createGain();
gainNode.gain.value = 1.5;  // 放大 1.5 倍

// 连接：麦克风 → 增益 → 处理器
sourceNode.connect(gainNode);
gainNode.connect(workletNode);
```

### 问题：AudioContext 处于 suspended 状态

浏览器的自动播放政策要求在用户交互后才能启动 AudioContext。

```javascript
// 在用户点击事件处理函数里调用
async function onUserClick() {
  if (audioContext.state === 'suspended') {
    await audioContext.resume();
  }
  // ...
}
```

---

## 本章小结

本章搭建了 VoiceBot 的音频采集基础：

- **`getUserMedia`** 是打开麦克风的入口，需要处理各种权限和设备错误
- **Web Audio API** 以节点图的方式处理音频，每个节点专注一件事
- **AudioWorklet** 是现代音频处理标准，运行在独立线程，不阻塞 UI
- **采样率转换**：用线性插值把 48kHz 降到 16kHz，对语音效果足够
- **格式转换**：Float32（-1 到 1）转 Int16（-32768 到 32767）
- 每 20ms 产出一帧 320 个 Int16 采样，这是后续处理的基本单位

现在我们有了稳定的音频数据流，但还有一个问题：用户不是一直在说话的，大量的静音帧如果全部发给服务端，既浪费带宽，又让 ASR 模型承受不必要的压力。

**下一章**，我们在浏览器端实现 **VAD（语音活动检测）**，让 VoiceBot 只在用户真正说话时才处理和发送音频。
