# 第五章：客户端 VAD

## 用户停顿了——你怎么知道他说完了？

---

想象一个场景：你正在和语音助手对话，说了一句话，然后停下来等它回答。

助手怎么知道你说完了？它不能一直等——如果等太久，用户体验会很差。也不能马上就处理——用户可能只是在思考，下一个词马上就来了。

更重要的问题是：用户说话之前和之后，麦克风一直在采集数据。大量的静音帧如果全部传给服务端，那带宽和计算资源就白白浪费了。

这就是 **VAD（Voice Activity Detection，语音活动检测）** 要解决的问题。

VAD 就像一个"开关"：只有检测到人在说话，才打开开关，开始处理和传输音频；检测到说话结束，关闭开关，发送一个"说话结束"的信号，告诉 ASR 可以开始识别了。

本章我们在浏览器端实现 VAD：先理解原理，再手写一个简单但实用的能量 VAD，最后把它集成到上一章的麦克风采集模块中。

---

## 5.1 为什么要在客户端做 VAD？

VAD 可以在客户端做，也可以在服务端做。我们选择客户端，原因如下：

```
方案 A：没有客户端 VAD（全量上传）
─────────────────────────────────
用户按下按钮
    │
    ▼
麦克风持续采集 ──────────────────────→ WebSocket
（静音、说话、静音、说话……全部上传）    │
                                       ▼
                                   服务端 ASR
                                  （需要处理大量静音）
```

```
方案 B：有客户端 VAD（按需上传）
──────────────────────────────────
用户按下按钮
    │
    ▼
麦克风持续采集 → VAD 检测 → 只在说话时传输 → WebSocket
（在本地过滤掉静音）                          │
                                             ▼
                                         服务端 ASR
                                         （只收到语音段）
```

方案 B 的好处：

| 维度 | 无客户端 VAD | 有客户端 VAD |
|------|------------|------------|
| 带宽占用 | 持续占用（假设 20% 时间在说话，浪费 80% 带宽）| 按需占用，节省约 80% |
| 服务端压力 | ASR 模型持续运行 | 只在语音段运行 |
| 响应延迟 | 需要某种超时判断说话结束 | VAD 精准检测结束点 |
| 实现复杂度 | 简单 | 需要实现 VAD 逻辑 |

对于多用户并发的 VoiceBot 服务，客户端 VAD 几乎是必选项。

---

## 5.2 能量检测法：简单但够用

VAD 最直觉的方法是**能量检测**：声音越大，能量越高；静音时能量接近零。

### 什么是音频能量？

对于一段 PCM 音频数据，能量通常用 **RMS（均方根）** 来表示：

```
                ┌─────────────────────────────┐
                │         N 个采样            │
RMS = √(  (1/N) × Σ  sample[i]²  )
                │                             │
                └─────────────────────────────┘

其中：
- N 是采样数量
- sample[i] 是第 i 个采样的值（Float32，范围 -1 到 1）
- Σ 是求和
```

用 JavaScript 来算：

```javascript
function calculateRMS(float32Array) {
  let sumOfSquares = 0;
  for (let i = 0; i < float32Array.length; i++) {
    sumOfSquares += float32Array[i] * float32Array[i];
  }
  return Math.sqrt(sumOfSquares / float32Array.length);
}

// 静音时：RMS 约 0.001 ~ 0.005
// 正常说话：RMS 约 0.02 ~ 0.1
// 大声说话：RMS 约 0.1 ~ 0.5
```

### 转成分贝（dBFS）

原始 RMS 值不直观，通常转成**分贝（dBFS）**来设置阈值：

```javascript
function rmsToDBFS(rms) {
  if (rms === 0) return -Infinity;
  return 20 * Math.log10(rms);
}

// 对应关系：
// RMS 0.001 → -60 dBFS（很安静的环境噪音）
// RMS 0.01  → -40 dBFS（轻声说话）
// RMS 0.05  → -26 dBFS（正常说话）
// RMS 0.1   → -20 dBFS（较响的说话）
```

通常把阈值设在 **-40 dBFS 到 -30 dBFS** 之间，高于阈值判断为"有声音"，低于则为"静音"。

---

## 5.3 VAD 状态机

光有能量检测还不够。真实场景里，用户说话会有短暂停顿（比如"嗯……然后……"），我们不能一检测到静音就立刻判断说话结束。

正确的做法是引入一个**状态机**：

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                                                         │
                    ▼                                                         │
              ┌──────────┐                                                    │
              │          │  能量 > 阈值                                        │
              │  SILENCE │ ─────────────────────→  ┌──────────────┐           │
              │  （静音） │  连续 N 帧（起始帧）      │   SPEAKING   │           │
              │          │ ←─────────────────────  │   （说话中）  │           │
              └──────────┘  能量 < 阈值             │              │           │
                            连续 M 帧（结束帧）      └──────────────┘           │
                                                          │                    │
                                                          │ 能量 < 阈值         │
                                                          │ 但还不够 M 帧        │
                                                          ▼                    │
                                                   ┌──────────────┐           │
                                                   │   TRAILING   │ ──────────┘
                                                   │  （尾音等待） │  能量 < 阈值
                                                   └──────────────┘  连续 M 帧
                                                          │          → 判定结束
                                                          │ 能量 > 阈值
                                                          ▼
                                                      回到 SPEAKING
```

三个状态：
- **SILENCE（静音）**：没人在说话，不传输音频
- **SPEAKING（说话中）**：检测到声音，传输音频
- **TRAILING（尾音等待）**：声音变小了，但还没确定是否结束，继续等待

关键参数：
- **`speechStartFrames`**：连续多少帧有声音才算开始说话（防止一个"啪"的噪音触发）
- **`speechEndFrames`**：连续多少帧静音才算说话结束（给用户留有停顿的余地）
- **`energyThreshold`**：能量阈值（dBFS）

---

## 5.4 手写能量 VAD

理解了原理，现在来实现。我们把 VAD 写成一个独立的类，方便测试和替换：

```javascript
// energy-vad.js

export const VADState = {
  SILENCE:  'silence',   // 静音
  SPEAKING: 'speaking',  // 说话中
  TRAILING: 'trailing',  // 尾音等待
};

export class EnergyVAD {
  /**
   * @param {object} options
   * @param {number} options.energyThreshold   能量阈值，dBFS，默认 -35
   * @param {number} options.speechStartFrames 连续多少帧有声才算开始，默认 3（约 60ms）
   * @param {number} options.speechEndFrames   连续多少帧静音才算结束，默认 20（约 400ms）
   * @param {function} options.onSpeechStart   说话开始回调
   * @param {function} options.onSpeechEnd     说话结束回调，参数是说话时长（ms）
   * @param {function} options.onFrame         每帧回调，参数是 {state, energyDB, isSpeech}
   */
  constructor({
    energyThreshold = -35,
    speechStartFrames = 3,
    speechEndFrames = 20,
    onSpeechStart = () => {},
    onSpeechEnd = () => {},
    onFrame = () => {},
  } = {}) {
    this.energyThreshold = energyThreshold;
    this.speechStartFrames = speechStartFrames;
    this.speechEndFrames = speechEndFrames;
    this.onSpeechStart = onSpeechStart;
    this.onSpeechEnd = onSpeechEnd;
    this.onFrame = onFrame;

    // 状态机
    this.state = VADState.SILENCE;

    // 连续有声帧计数（用于判断说话开始）
    this.consecutiveSpeechFrames = 0;

    // 连续静音帧计数（用于判断说话结束）
    this.consecutiveSilenceFrames = 0;

    // 说话开始的时间戳
    this.speechStartTime = null;

    // 在 TRAILING 阶段缓存的帧（以防最终判定是继续说话）
    this.trailingFrames = [];
  }

  /**
   * 处理一帧音频数据
   *
   * @param {Float32Array | Int16Array} frame  一帧音频采样
   * @param {number} frameDurationMs          这帧对应的时长，默认 20ms
   * @returns {object} { state, energyDB, isSpeech }
   */
  processFrame(frame, frameDurationMs = 20) {
    const energyDB = this._calculateEnergyDB(frame);
    const isSpeech = energyDB > this.energyThreshold;

    const prevState = this.state;
    this._updateState(isSpeech, frameDurationMs);

    const result = {
      state: this.state,
      energyDB,
      isSpeech,
      stateChanged: this.state !== prevState,
    };

    this.onFrame(result);
    return result;
  }

  /**
   * 重置状态机到初始状态
   */
  reset() {
    this.state = VADState.SILENCE;
    this.consecutiveSpeechFrames = 0;
    this.consecutiveSilenceFrames = 0;
    this.speechStartTime = null;
    this.trailingFrames = [];
  }

  // ─── 私有方法 ────────────────────────────────────────────────────────────

  /**
   * 计算音频帧的能量（dBFS）
   * 支持 Float32Array 和 Int16Array
   */
  _calculateEnergyDB(frame) {
    let sumOfSquares = 0;
    const n = frame.length;

    if (frame instanceof Float32Array) {
      for (let i = 0; i < n; i++) {
        sumOfSquares += frame[i] * frame[i];
      }
    } else if (frame instanceof Int16Array) {
      // Int16 范围是 [-32768, 32767]，归一化到 [-1, 1]
      const scale = 1 / 32768;
      for (let i = 0; i < n; i++) {
        const normalized = frame[i] * scale;
        sumOfSquares += normalized * normalized;
      }
    } else {
      throw new Error(`不支持的音频格式：${frame.constructor.name}`);
    }

    const rms = Math.sqrt(sumOfSquares / n);
    if (rms === 0) return -100; // 避免 log(0)

    return 20 * Math.log10(rms);
  }

  /**
   * 状态机转换逻辑
   */
  _updateState(isSpeech, frameDurationMs) {
    switch (this.state) {
      case VADState.SILENCE:
        if (isSpeech) {
          this.consecutiveSpeechFrames++;
          if (this.consecutiveSpeechFrames >= this.speechStartFrames) {
            // 连续 N 帧有声 → 转为 SPEAKING
            this.state = VADState.SPEAKING;
            this.consecutiveSpeechFrames = 0;
            this.consecutiveSilenceFrames = 0;
            this.speechStartTime = Date.now();
            this.onSpeechStart();
          }
        } else {
          // 静音时重置有声帧计数
          this.consecutiveSpeechFrames = 0;
        }
        break;

      case VADState.SPEAKING:
        if (!isSpeech) {
          // 检测到静音，进入 TRAILING 阶段
          this.state = VADState.TRAILING;
          this.consecutiveSilenceFrames = 1;
          this.trailingFrames = [];
        }
        // 有声时保持 SPEAKING，不做状态变化
        break;

      case VADState.TRAILING:
        if (isSpeech) {
          // 又检测到声音，说明用户只是短暂停顿，回到 SPEAKING
          this.state = VADState.SPEAKING;
          this.consecutiveSilenceFrames = 0;
          this.trailingFrames = [];
        } else {
          this.consecutiveSilenceFrames++;
          if (this.consecutiveSilenceFrames >= this.speechEndFrames) {
            // 连续 M 帧静音 → 说话结束
            const speechDurationMs = Date.now() - this.speechStartTime;
            this.state = VADState.SILENCE;
            this.consecutiveSilenceFrames = 0;
            this.speechStartTime = null;
            this.trailingFrames = [];
            this.onSpeechEnd(speechDurationMs);
          }
        }
        break;
    }
  }
}
```

### 调试 VAD：可视化能量

在开发阶段，能量可视化能帮你快速调整阈值：

```javascript
// debug-energy-meter.js
// 在页面上显示一个能量条，帮助调试 VAD 阈值

export class EnergyMeter {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) return;

    this.container.innerHTML = `
      <div style="font-size:12px; color:#666; margin-bottom:4px;">
        能量计（dBFS）
      </div>
      <div style="background:#eee; height:20px; border-radius:4px; overflow:hidden;">
        <div id="energy-bar" style="
          height:100%;
          background:#4CAF50;
          transition:width 0.05s;
          width:0%;
        "></div>
      </div>
      <div id="energy-value" style="font-size:11px; color:#999; margin-top:2px;">
        -∞ dBFS
      </div>
    `;
    this.bar = document.getElementById('energy-bar');
    this.valueEl = document.getElementById('energy-value');
  }

  update(energyDB) {
    if (!this.bar) return;

    // 把 -80dBFS 到 0dBFS 映射到 0% 到 100%
    const minDB = -80, maxDB = 0;
    const percent = Math.max(0, Math.min(100,
      ((energyDB - minDB) / (maxDB - minDB)) * 100
    ));

    this.bar.style.width = `${percent}%`;
    this.bar.style.background = energyDB > -35 ? '#f44336' : '#4CAF50';
    this.valueEl.textContent = `${energyDB.toFixed(1)} dBFS`;
  }
}
```

---

## 5.5 使用 @ricky0123/vad-web 现成方案

如果你不想手写 VAD，可以使用 `@ricky0123/vad-web` 库。这个库内置了一个基于机器学习的 VAD 模型（Silero VAD），比纯能量检测更准确，对方言、口音、噪音环境的适应性更好。

```bash
npm install @ricky0123/vad-web
```

基本用法：

```javascript
import { MicVAD } from '@ricky0123/vad-web';

const vad = await MicVAD.new({
  // 每当检测到用户开始说话时触发
  onSpeechStart: () => {
    console.log('用户开始说话');
  },

  // 每当用户说完一句话时触发
  // audio 是 Float32Array，包含这句话的完整音频
  onSpeechEnd: (audio) => {
    console.log(`用户说完了一句话，时长 ${audio.length / 16000} 秒`);
    // audio 的采样率是 16000 Hz
  },

  // 静音超时配置
  positiveSpeechThreshold: 0.8,  // 判定为"有声"的置信度阈值
  negativeSpeechThreshold: 0.3,  // 判定为"静音"的置信度阈值
  redemptionFrames: 8,           // 静音帧数超过多少判定说话结束
});

// 开始监听
vad.start();

// 停止监听
// vad.pause();
```

**选哪个？**

| 场景 | 推荐 |
|------|------|
| 快速原型，安静环境 | 手写能量 VAD（本章实现）|
| 生产环境，有噪音 | `@ricky0123/vad-web`（Silero VAD）|
| 需要完全控制数据流 | 手写能量 VAD |
| 对延迟要求极高 | 手写能量 VAD（更轻量）|

本章我们以手写能量 VAD 为主，理解原理最重要。在第 6、7 章你完全可以替换成 Silero VAD，接口设计几乎一样。

---

## 5.6 与麦克风采集模块集成

把 VAD 接入到上一章的音频采集流水线中。整体架构变成：

```
AudioWorklet（每 20ms）
    │ Int16Array，320 个采样
    ↓
EnergyVAD.processFrame()
    │
    ├─ SILENCE：丢弃，不传输
    │
    ├─ SPEAKING：传给 WebSocket 发送
    │
    └─ TRAILING：继续传（等待确认是否真的结束）
         │
         ├─ 又检测到声音 → 继续 SPEAKING
         └─ 超过阈值帧数 → 触发 onSpeechEnd，停止传输
```

更新 `mic-capture.js`，加入 VAD：

```javascript
// mic-capture-with-vad.js

import { EnergyVAD, VADState } from './energy-vad.js';

export class MicCaptureWithVAD {
  constructor({
    // VAD 配置
    energyThreshold = -35,
    speechStartFrames = 3,
    speechEndFrames = 20,

    // 回调
    onSpeechStart = () => {},
    onSpeechEnd = () => {},
    onAudioFrame = () => {},   // 只在说话时触发
    onAllFrame = () => {},     // 每帧都触发（用于调试）
    onError = console.error,
  } = {}) {
    this.onSpeechStart = onSpeechStart;
    this.onSpeechEnd = onSpeechEnd;
    this.onAudioFrame = onAudioFrame;
    this.onAllFrame = onAllFrame;
    this.onError = onError;

    // 麦克风状态
    this.audioContext = null;
    this.sourceNode = null;
    this.workletNode = null;
    this.stream = null;
    this.isCapturing = false;

    // 创建 VAD 实例
    this.vad = new EnergyVAD({
      energyThreshold,
      speechStartFrames,
      speechEndFrames,
      onSpeechStart: () => {
        console.log('[VAD] 说话开始');
        this.onSpeechStart();
      },
      onSpeechEnd: (durationMs) => {
        console.log(`[VAD] 说话结束，时长 ${durationMs}ms`);
        this.onSpeechEnd(durationMs);
      },
    });
  }

  async start() {
    if (this.isCapturing) return;

    try {
      // 1. 打开麦克风（和上一章一样）
      this.stream = await this._openMicrophone();

      // 2. 创建 AudioContext
      this.audioContext = new AudioContext({ latencyHint: 'interactive' });

      // 3. 加载 AudioWorklet
      await this.audioContext.audioWorklet.addModule(
        '/static/js/audio-processor.worklet.js'
      );

      // 4. 创建节点并连接
      this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);

      this.workletNode = new AudioWorkletNode(
        this.audioContext,
        'audio-processor',
        {
          processorOptions: {
            sourceSampleRate: this.audioContext.sampleRate,
            targetSampleRate: 16000,
          },
          numberOfOutputs: 0,
        }
      );

      // 5. 处理每帧音频数据，接入 VAD
      this.workletNode.port.onmessage = (event) => {
        if (event.data.type !== 'audio_frame') return;

        const frame = event.data.data; // Int16Array，320 个采样，20ms

        // VAD 处理
        const vadResult = this.vad.processFrame(frame);

        // 调试回调（每帧都触发）
        this.onAllFrame({ frame, vadResult });

        // 只在说话期间（SPEAKING 或 TRAILING）传输音频
        if (vadResult.state === VADState.SPEAKING ||
            vadResult.state === VADState.TRAILING) {
          this.onAudioFrame(frame, vadResult);
        }
      };

      this.sourceNode.connect(this.workletNode);
      this.isCapturing = true;

    } catch (err) {
      this.onError(err);
      await this._cleanup();
      throw err;
    }
  }

  async stop() {
    if (!this.isCapturing) return;
    await this._cleanup();
    this.vad.reset();
    this.isCapturing = false;
  }

  // 动态调整 VAD 阈值（方便用户自己调）
  setEnergyThreshold(dbfs) {
    this.vad.energyThreshold = dbfs;
  }

  async _openMicrophone() {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { exact: 1 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });
    } catch (err) {
      const messages = {
        'NotAllowedError': '麦克风权限被拒绝',
        'NotFoundError': '未找到麦克风设备',
        'NotReadableError': '麦克风被其他应用占用',
      };
      throw new Error(messages[err.name] || `麦克风错误：${err.message}`);
    }
  }

  async _cleanup() {
    if (this.sourceNode) { this.sourceNode.disconnect(); this.sourceNode = null; }
    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode.port.close();
      this.workletNode = null;
    }
    if (this.audioContext?.state !== 'closed') {
      await this.audioContext?.close();
      this.audioContext = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }
}
```

---

## 5.7 主页面集成演示

```javascript
// main.js（含 VAD 版本）

import { MicCaptureWithVAD } from './mic-capture-with-vad.js';
import { EnergyMeter } from './debug-energy-meter.js';

let mic = null;
const energyMeter = new EnergyMeter('energy-meter');

// 累积当前这句话的音频帧
let currentSpeechFrames = [];

async function startListening() {
  mic = new MicCaptureWithVAD({
    // VAD 参数
    energyThreshold: -35,    // 高于 -35 dBFS 算有声
    speechStartFrames: 3,    // 连续 3 帧（60ms）有声才开始
    speechEndFrames: 20,     // 连续 20 帧（400ms）静音才结束

    // 说话开始：清空缓存，准备接收
    onSpeechStart() {
      currentSpeechFrames = [];
      document.getElementById('status').textContent = '正在听...';
    },

    // 收到音频帧：存起来
    onAudioFrame(frame) {
      // 深拷贝一份（原始 ArrayBuffer 可能被转移）
      currentSpeechFrames.push(new Int16Array(frame));
    },

    // 说话结束：可以发给服务端了
    onSpeechEnd(durationMs) {
      document.getElementById('status').textContent = `处理中... (${durationMs}ms)`;

      // 把所有帧合并成一个大的 Int16Array
      const totalSamples = currentSpeechFrames.reduce(
        (sum, f) => sum + f.length, 0
      );
      const combined = new Int16Array(totalSamples);
      let offset = 0;
      for (const frame of currentSpeechFrames) {
        combined.set(frame, offset);
        offset += frame.length;
      }

      console.log(
        `说话结束，共 ${combined.length} 个采样，` +
        `时长 ${(combined.length / 16000).toFixed(2)} 秒`
      );

      // TODO 第 6 章：通过 WebSocket 发送 combined 给服务端
      currentSpeechFrames = [];
    },

    // 每帧都触发，用于更新能量计
    onAllFrame({ vadResult }) {
      energyMeter.update(vadResult.energyDB);
    },

    onError(err) {
      alert(err.message);
    },
  });

  await mic.start();
}

document.getElementById('start-btn').addEventListener('click', startListening);
document.getElementById('stop-btn').addEventListener('click', () => mic?.stop());
```

对应的 HTML 加一个能量计容器：

```html
<div id="energy-meter"></div>
```

---

## 5.8 VAD 参数调优

不同环境下需要不同的参数配置：

```javascript
// 安静的办公室环境
const quietOffice = {
  energyThreshold: -40,    // 阈值可以设低一点，灵敏度高
  speechStartFrames: 2,    // 2 帧就触发，响应更快
  speechEndFrames: 15,     // 15 帧（300ms）结束
};

// 嘈杂的环境（咖啡厅、户外）
const noisyEnv = {
  energyThreshold: -25,    // 阈值高，避免噪音误触发
  speechStartFrames: 5,    // 5 帧才触发，减少误报
  speechEndFrames: 25,     // 25 帧（500ms）结束，给用户更多停顿空间
};

// 通话质量差（低比特率音频）
const lowQuality = {
  energyThreshold: -30,
  speechStartFrames: 4,
  speechEndFrames: 20,
};
```

最佳实践：**让用户自己调**。在界面上提供一个灵敏度滑块：

```javascript
const sensitivitySlider = document.getElementById('sensitivity');
sensitivitySlider.addEventListener('input', (e) => {
  // 滑块值 0-100 映射到 dBFS -50 到 -20
  const dbfs = -50 + (e.target.value / 100) * 30;
  mic?.setEnergyThreshold(dbfs);
});
```

---

## 5.9 VAD 的局限性

能量 VAD 有一些固有的局限，使用时要知道：

**误报（False Positive）**：把噪音当成语音
- 突然的敲击声、咳嗽声可能触发 VAD
- 解决：增加 `speechStartFrames`，或者用更智能的 VAD（Silero）

**漏报（False Negative）**：把轻声说话当成静音
- 声音很小时 RMS 低，可能低于阈值
- 解决：降低 `energyThreshold`，或者配合 AGC（自动增益控制）

**切断长句末尾**：说话结束时声音渐小，可能被提前判定为静音
- 解决：适当增加 `speechEndFrames`

**回声问题**：扬声器播放 TTS 声音，麦克风采集到，VAD 误判为用户说话
- 这就是为什么 `getUserMedia` 里要开启 `echoCancellation: true`
- 第 7 章会详细讨论这个问题

---

## 本章小结

本章实现了 VoiceBot 的客户端 VAD 模块：

- **为什么在客户端做 VAD**：节省带宽（约 80%），减轻服务端压力，精准检测说话结束点
- **能量检测法**：计算 RMS，转成 dBFS，与阈值比较判断是否有声音
- **三状态 VAD 状态机**：SILENCE → SPEAKING → TRAILING，处理停顿不误判
- **关键参数**：能量阈值、起始帧数、结束帧数，需根据环境调整
- **与麦克风采集集成**：每帧音频经过 VAD，只在 SPEAKING/TRAILING 状态下传递
- **现成方案**：`@ricky0123/vad-web` 基于 Silero 模型，在嘈杂环境下更可靠

现在，浏览器端的音频采集和 VAD 都有了。每当用户说完一句话，我们会得到一段干净的 PCM 音频数据（Int16Array）。

**下一章**，我们来设计传输协议：如何把这段音频通过 WebSocket 发给服务端，同时还要处理 TTS 音频从服务端流回来——这是 VoiceBot 实时双工通信的核心。
