# 第七章：TTS 音频播放

## 声音是怎么"流"出来的？

---

想象你在和语音助手聊天。你问了一个问题，助手开始回答。它不是等全部想好了再说，而是一边想一边说——就像一个真正的人。

这种体验背后有一个关键技术：**流式 TTS 播放**。

服务端的 TTS 引擎不会等全部音频生成完才发给你，而是生成一小段就立刻发一小段。浏览器收到第一块音频时，不等后面的，立刻开始播放。后面的音频陆续到来，无缝衔接，听起来就像一段连续的声音。

这比"等全部生成完再播放"能减少 **1-3 秒**的感知延迟。

但要实现这个效果，有几个难题需要解决：

1. **Web Audio API 怎么播放原始 PCM 数据？**（不是 MP3，不是 WAV，是裸的采样值）
2. **多个音频块怎么无缝拼接，中间不能有空隙？**
3. **播放 TTS 的同时，用户又开口说话，麦克风会不会采集到扬声器的声音？**（回声问题）
4. **播放完一句话，怎么通知服务端？**

本章逐一解决这些问题，最终交付一个完整的 `TTSPlayer` 类。

---

## 7.1 Web Audio API 播放 PCM 音频

浏览器原生支持播放 MP3、AAC、Ogg 等编码格式的音频，但对于 PCM（原始采样数据）就没有直接的支持——你不能把一个 `Int16Array` 丢给 `<audio>` 标签让它播放。

Web Audio API 提供了底层播放能力：**`AudioBuffer` + `AudioBufferSourceNode`**。

### AudioBuffer：音频数据的容器

`AudioBuffer` 是 Web Audio API 中存放音频数据的对象。它持有的是 **Float32** 格式的采样值（不是 Int16），因为 Web Audio API 内部统一使用 Float32。

```javascript
// 创建一个 AudioBuffer
// 参数：声道数、采样数量、采样率
const audioContext = new AudioContext();

const numChannels = 1;       // 单声道
const numSamples = 3200;     // 200ms @ 16kHz = 3200 个采样
const sampleRate = 16000;

const audioBuffer = audioContext.createBuffer(
  numChannels,
  numSamples,
  sampleRate
);

// 获取第 0 个声道的数据（Float32Array）
const channelData = audioBuffer.getChannelData(0);
// channelData 现在是一个全 0 的 Float32Array，可以往里填数据
```

### AudioBufferSourceNode：播放节点

每次播放一个 `AudioBuffer`，都需要创建一个新的 `AudioBufferSourceNode`。注意：**源节点只能播放一次，播放完后就失效了**，不能 reuse。

```javascript
// 创建播放节点
const sourceNode = audioContext.createBufferSource();
sourceNode.buffer = audioBuffer;

// 连接到输出（扬声器）
sourceNode.connect(audioContext.destination);

// 在指定时间点开始播放
// audioContext.currentTime 是当前时间（秒）
sourceNode.start(audioContext.currentTime);

// 可选：播放完毕的回调
sourceNode.onended = () => {
  console.log('这个音频块播完了');
};
```

### Int16 → Float32 的转换

服务端发来的 TTS 数据是 `Int16Array`（16-bit PCM），需要转成 `Float32Array`：

```javascript
/**
 * Int16Array → Float32Array
 * 和第 4 章的方向相反
 *
 * @param {Int16Array} int16Data
 * @returns {Float32Array}
 */
function int16ToFloat32(int16Data) {
  const float32Data = new Float32Array(int16Data.length);
  for (let i = 0; i < int16Data.length; i++) {
    // int16 范围 [-32768, 32767]，归一化到 [-1.0, 1.0]
    float32Data[i] = int16Data[i] / 32768;
  }
  return float32Data;
}
```

---

## 7.2 无缝播放的核心：时间调度

流式播放最大的挑战是：**音频块到达的时间不规律，但播放必须连续**。

如果你这样朴素地播放：

```javascript
// ❌ 错误做法：收到就立刻播
function onTTSChunk(chunk) {
  const buffer = createAudioBuffer(chunk);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  source.start();  // 立刻播
}
```

结果是：每个 chunk 都从"现在"开始播放，覆盖掉之前的声音，或者中间有空隙，听起来像破损的录音。

正确做法是用 **Web Audio API 的精确时间调度**：

```javascript
// ✅ 正确做法：排队调度，首尾相接

let nextPlayTime = 0;  // 下一个 chunk 应该在什么时间开始播放

function onTTSChunk(chunk) {
  const buffer = createAudioBuffer(chunk);
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);

  // 如果队列空了，从"稍后一点"开始（留一点缓冲防止卡顿）
  const now = audioContext.currentTime;
  if (nextPlayTime < now) {
    nextPlayTime = now + 0.05;  // 50ms 的起始缓冲
  }

  // 在 nextPlayTime 开始播放
  source.start(nextPlayTime);

  // 更新下一个 chunk 的开始时间 = 当前 chunk 的结束时间
  nextPlayTime += buffer.duration;
}
```

这就是关键所在：`nextPlayTime` 像一个游标，每次向前推进 `buffer.duration`，保证每个 chunk 恰好接在上一个的末尾。

```
时间轴：
0s          1s          2s          3s
│           │           │           │
├── chunk1 ─┤
            ├─── chunk2 ──┤
                          ├── chunk3 ─┤
                                      ├── chunk4 ──┤

每个 chunk 的开始时间 = 上一个 chunk 的结束时间
中间没有空隙，听起来是连续的
```

---

## 7.3 播放队列管理

实际场景比上面更复杂：
- 用户可能打断 TTS（说了新的话），需要停止当前播放
- 多轮对话下，上一轮的 TTS 还没播完，新一轮的就来了
- 需要知道当前播放状态，决定是否允许打断

我们用一个 **PlayQueue** 来管理：

```
TTS chunk 到达 → 转换格式 → 加入队列 → 按时间调度播放
                                           ↓
                                    播放完毕 → 回调通知
                                           ↓
                              队列空 → 空闲状态
```

---

## 7.4 完整的 TTSPlayer 实现

```javascript
// tts-player.js

/**
 * TTS 音频播放器
 *
 * 功能：
 * - 边收 chunk 边播放，无缝拼接
 * - 播放队列管理，支持打断
 * - 播放完毕回调（通知服务端）
 * - 回声消除配合（播放时暂停麦克风输入）
 */
export class TTSPlayer {
  /**
   * @param {object} options
   * @param {number}   [options.sampleRate=16000]  TTS 音频采样率
   * @param {number}   [options.bufferAheadSec=0.05] 预缓冲时间（秒），防止卡顿
   * @param {function} [options.onPlaybackStart]   开始播放某条 TTS 时的回调
   * @param {function} [options.onPlaybackEnd]     一条 TTS 全部播放完的回调，参数: utteranceId
   * @param {function} [options.onStateChange]     播放状态变化回调，参数: 'playing' | 'idle'
   */
  constructor({
    sampleRate = 16000,
    bufferAheadSec = 0.05,
    onPlaybackStart = () => {},
    onPlaybackEnd   = () => {},
    onStateChange   = () => {},
  } = {}) {
    this.sampleRate = sampleRate;
    this.bufferAheadSec = bufferAheadSec;
    this.onPlaybackStart = onPlaybackStart;
    this.onPlaybackEnd   = onPlaybackEnd;
    this.onStateChange   = onStateChange;

    // AudioContext（懒加载，在第一次播放时创建）
    this._audioContext = null;

    // 正在播放的 utterance id
    this._currentUtteranceId = null;

    // 下一个 chunk 的计划播放时间（AudioContext 时间轴上的秒数）
    this._nextPlayTime = 0;

    // 正在运行的 AudioBufferSourceNode 列表（用于打断播放）
    this._activeSources = [];

    // 是否正在播放
    this._isPlaying = false;

    // 当前 utterance 的总 chunk 数和已播放完的 chunk 数
    // 用于判断什么时候整条 TTS 播完了
    this._chunkCount = 0;
    this._endedChunkCount = 0;
    this._ttsEnded = false;   // 是否已收到 tts_end 消息
  }

  // ─── 公开 API ─────────────────────────────────────────────────────────────

  /**
   * 开始接收一条新的 TTS（收到 tts_start 消息时调用）
   * @param {string} utteranceId
   */
  beginUtterance(utteranceId) {
    // 如果上一条 TTS 还没播完，直接打断它
    if (this._isPlaying) {
      this._stopCurrent();
    }

    this._currentUtteranceId = utteranceId;
    this._chunkCount = 0;
    this._endedChunkCount = 0;
    this._ttsEnded = false;

    // 重置播放时间游标
    this._ensureAudioContext();
    this._nextPlayTime = this._audioContext.currentTime + this.bufferAheadSec;

    console.log(`[Player] 开始接收 TTS：${utteranceId}`);
    this.onPlaybackStart({ utteranceId });
  }

  /**
   * 接收一个 TTS 音频块并加入播放队列（收到 Binary:tts_chunk 时调用）
   * @param {Int16Array} chunk  Int16 PCM 数据，采样率为 this.sampleRate
   */
  pushChunk(chunk) {
    if (!this._currentUtteranceId) {
      console.warn('[Player] 收到 TTS chunk，但还没有 beginUtterance，忽略');
      return;
    }

    this._ensureAudioContext();
    this._chunkCount++;

    const chunkIndex = this._chunkCount;  // 用于回调顺序追踪

    try {
      // 1. Int16 → Float32
      const float32Data = this._int16ToFloat32(chunk);

      // 2. 创建 AudioBuffer
      const audioBuffer = this._audioContext.createBuffer(
        1,                   // 单声道
        float32Data.length,  // 采样数
        this.sampleRate      // 采样率
      );
      audioBuffer.getChannelData(0).set(float32Data);

      // 3. 计算这个 chunk 的播放时间
      const now = this._audioContext.currentTime;
      if (this._nextPlayTime < now) {
        // 如果游标落后了（比如 chunk 到达太慢），重新对齐
        const gap = now - this._nextPlayTime;
        if (gap > 0.1) {
          // 落后超过 100ms，重置游标
          console.warn(`[Player] 播放游标落后 ${(gap * 1000).toFixed(0)}ms，重置`);
          this._nextPlayTime = now + this.bufferAheadSec;
        }
      }

      const startTime = this._nextPlayTime;
      this._nextPlayTime += audioBuffer.duration;

      // 4. 创建并启动播放节点
      const source = this._audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this._audioContext.destination);

      source.onended = () => {
        // 从活跃节点列表中移除
        const idx = this._activeSources.indexOf(source);
        if (idx !== -1) this._activeSources.splice(idx, 1);

        this._endedChunkCount++;
        this._checkPlaybackComplete();
      };

      this._activeSources.push(source);
      source.start(startTime);

      if (!this._isPlaying) {
        this._isPlaying = true;
        this.onStateChange('playing');
      }

    } catch (err) {
      console.error('[Player] 播放 chunk 失败：', err);
    }
  }

  /**
   * 标记当前 TTS 的所有 chunk 已经发完（收到 tts_end 时调用）
   */
  endUtterance() {
    this._ttsEnded = true;
    this._checkPlaybackComplete();
  }

  /**
   * 立刻停止播放，清空队列
   */
  stop() {
    this._stopCurrent();
    this._setState('idle');
  }

  /**
   * 是否正在播放
   */
  get isPlaying() {
    return this._isPlaying;
  }

  /**
   * 获取当前播放的 utteranceId
   */
  get currentUtteranceId() {
    return this._currentUtteranceId;
  }

  // ─── 私有方法 ─────────────────────────────────────────────────────────────

  /**
   * 确保 AudioContext 已创建并处于运行状态
   * 注意：AudioContext 必须在用户交互后创建
   */
  _ensureAudioContext() {
    if (!this._audioContext) {
      this._audioContext = new AudioContext({
        latencyHint: 'interactive',
        sampleRate: this.sampleRate,
      });
    }

    // 如果 AudioContext 被挂起（浏览器自动播放策略），恢复它
    if (this._audioContext.state === 'suspended') {
      this._audioContext.resume().catch(console.error);
    }
  }

  /**
   * 停止当前播放的所有节点
   */
  _stopCurrent() {
    // 停止所有活跃的播放节点
    for (const source of this._activeSources) {
      try {
        source.stop();
        source.disconnect();
      } catch (e) {
        // 节点可能已经停止，忽略错误
      }
    }
    this._activeSources = [];
    this._isPlaying = false;
    this._currentUtteranceId = null;
    this._nextPlayTime = 0;
  }

  /**
   * 检查当前 utterance 是否已全部播完
   * 条件：已收到 tts_end + 所有 chunk 都播完了
   */
  _checkPlaybackComplete() {
    if (this._ttsEnded && this._endedChunkCount >= this._chunkCount) {
      const utteranceId = this._currentUtteranceId;
      this._isPlaying = false;
      this._currentUtteranceId = null;
      this.onStateChange('idle');
      this.onPlaybackEnd({ utteranceId });
      console.log(`[Player] TTS 播放完毕：${utteranceId}`);
    }
  }

  /**
   * Int16Array → Float32Array 格式转换
   */
  _int16ToFloat32(int16Data) {
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
      float32Data[i] = int16Data[i] / 32768;
    }
    return float32Data;
  }

  _setState(state) {
    if (state === 'idle') {
      this._isPlaying = false;
    }
  }
}
```

---

## 7.5 回声消除：播放时处理双工冲突

当 TTS 在播放声音时，麦克风会采集到扬声器的声音。如果 VAD 此时也在工作，扬声器的声音可能被误判为用户说话，产生回声和干扰。

这是语音交互中最棘手的问题之一，有几种处理策略：

### 方案一：硬件/系统回声消除（推荐）

这是最干净的方案。`getUserMedia` 里开启 `echoCancellation: true`，让浏览器（或操作系统）的回声消除算法帮你处理：

```javascript
const stream = await navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: true,    // 浏览器级回声消除
    noiseSuppression: true,
    autoGainControl: true,
  }
});
```

浏览器的回声消除通过参考信号（扬声器输出的信号）来从麦克风信号中减去回声。在大多数场景下效果很好。

**局限**：如果用的是蓝牙耳机或外接扬声器，回声消除可能不如内置麦克风/扬声器那么好。

### 方案二：软件静音（简单粗暴）

TTS 播放期间，完全停止麦克风采集（或忽略 VAD 的输出）：

```javascript
// 集成到 TTSPlayer 的回调里
const player = new TTSPlayer({
  onPlaybackStart() {
    // TTS 开始播放：暂停 VAD（不处理麦克风数据）
    mic.pauseVAD();
  },
  onPlaybackEnd() {
    // TTS 播放完毕：恢复 VAD
    mic.resumeVAD();
  },
});
```

在 `MicCaptureWithVAD` 里加一个暂停/恢复方法：

```javascript
// 在 mic-capture-with-vad.js 里添加
pauseVAD() {
  this._vadPaused = true;
}

resumeVAD() {
  this._vadPaused = false;
  this.vad.reset();  // 重置 VAD 状态，避免残留的静音帧影响判断
}
```

然后在音频帧处理里检查：

```javascript
this.workletNode.port.onmessage = (event) => {
  if (event.data.type !== 'audio_frame') return;
  if (this._vadPaused) return;  // TTS 播放时忽略所有帧

  const frame = event.data.data;
  const vadResult = this.vad.processFrame(frame);
  // ...
};
```

**局限**：TTS 播放期间用户无法打断——这对某些场景不友好。

### 方案三：允许打断（高级）

更好的用户体验是：TTS 播放时，用户可以说话打断。检测到用户说话，立刻停止 TTS。

```javascript
// 检测到用户说话时打断 TTS
mic = new MicCaptureWithVAD({
  onSpeechStart() {
    if (player.isPlaying) {
      console.log('用户打断了 TTS');
      player.stop();             // 停止播放
      client.sendInterrupt();    // 通知服务端（可选）
    }
    client.sendVADStart();
  },
  // ...
});
```

打断场景下，硬件回声消除更重要，因为 VAD 需要在有扬声器声音的同时，准确识别出用户的说话。

**VoiceBot 推荐配置**：

```javascript
// 开发/原型阶段：方案二（简单）
// 生产阶段：方案一 + 方案三（最佳体验）

// 两者结合：开启硬件回声消除，同时允许打断
const mic = new MicCaptureWithVAD({
  onSpeechStart() {
    if (player.isPlaying) {
      player.stop();
      // 给 VAD 一点恢复时间（回声消除需要时间适应）
      setTimeout(() => {
        client.sendVADStart();
      }, 100);
    } else {
      client.sendVADStart();
    }
  },
});
```

---

## 7.6 播放确认回调

播放完毕后，我们需要通知服务端。服务端收到通知后可以决定：
- 是否继续等待用户说话
- 是否显示某些 UI 状态（比如"对方已播放"）
- 用于统计 TTS 到播放的端到端延迟

```javascript
// 在主页面代码里把 TTSPlayer 和 VoiceBotClient 连起来

const player = new TTSPlayer({
  sampleRate: 16000,

  onPlaybackEnd({ utteranceId }) {
    // 通知服务端：TTS 已播放完毕
    client.sendTTSPlayed(utteranceId);
    console.log(`TTS ${utteranceId} 播放完毕`);
  },

  onStateChange(state) {
    if (state === 'playing') {
      statusEl.textContent = '正在播放...';
    } else if (state === 'idle') {
      statusEl.textContent = '可以说话';
    }
  },
});

// 在 WebSocket 客户端的回调里调用播放器
const client = new VoiceBotClient({
  url: WS_URL,

  onTTSStart({ utteranceId }) {
    player.beginUtterance(utteranceId);
  },

  onTTSChunk(int16Data) {
    player.pushChunk(int16Data);
  },

  onTTSEnd({ utteranceId }) {
    player.endUtterance();
  },

  // ...
});
```

---

## 7.7 采样率不匹配的处理

服务端的 TTS 可能以不同的采样率输出音频。常见情况：

| TTS 引擎 | 输出采样率 |
|----------|-----------|
| CosyVoice | 22050 Hz |
| Edge TTS  | 16000 Hz |
| OpenAI TTS | 24000 Hz |
| 大多数本地模型 | 16000 Hz 或 22050 Hz |

如果 TTS 输出是 22050 Hz，而你的 `AudioContext` 是 16000 Hz，直接播放会音调失真（声音变快/变慢）。

解决方案：在创建 `AudioBuffer` 时，**指定正确的采样率**，让 Web Audio API 负责重采样：

```javascript
// 如果 TTS 采样率和 AudioContext 不同
// 在创建 AudioBuffer 时用 TTS 的采样率
const audioBuffer = audioContext.createBuffer(
  1,
  float32Data.length,
  ttsSampleRate   // 用 TTS 的实际采样率，而不是 AudioContext 的采样率
);
```

Web Audio API 会在播放时自动把音频重采样到 AudioContext 的采样率。

如果你需要精确控制，可以在 TTSPlayer 里加一个 `ttsSampleRate` 参数：

```javascript
const player = new TTSPlayer({
  sampleRate: 22050,   // TTS 输出采样率
});
```

修改 `pushChunk` 里创建 `AudioBuffer` 的部分：

```javascript
const audioBuffer = this._audioContext.createBuffer(
  1,
  float32Data.length,
  this.sampleRate  // 现在这里用的是 TTS 的采样率
);
```

---

## 7.8 完整集成：从 WebSocket 到扬声器

把本章和前几章的所有模块拼在一起，这是最终的主程序：

```javascript
// app.js —— VoiceBot 前端完整主程序

import { MicCaptureWithVAD } from './mic-capture-with-vad.js';
import { VoiceBotClient }    from './voicebot-client.js';
import { TTSPlayer }          from './tts-player.js';

class VoiceBotApp {
  constructor() {
    const WS_URL = `ws://${location.host}/ws/voice`;

    // ── TTS 播放器 ───────────────────────────────────────────────────────
    this.player = new TTSPlayer({
      sampleRate: 16000,

      onPlaybackEnd: ({ utteranceId }) => {
        // 通知服务端播放完毕
        this.client.sendTTSPlayed(utteranceId);
        this._updateStatus('可以说话');
      },

      onStateChange: (state) => {
        if (state === 'playing') {
          this._updateStatus('正在播放...');
          // TTS 播放中：暂停 VAD，避免回声干扰
          this.mic?.pauseVAD();
        } else {
          // 播放完毕：恢复 VAD
          this.mic?.resumeVAD();
        }
      },
    });

    // ── WebSocket 客户端 ─────────────────────────────────────────────────
    this.client = new VoiceBotClient({
      url: WS_URL,

      onConnect: ()   => this._updateStatus('已连接，可以说话'),
      onDisconnect: () => this._updateStatus('连接断开，尝试重连...'),
      onFailed: ()    => this._updateStatus('连接失败，请刷新页面'),

      onASRResult: ({ text, isFinal }) => {
        this._showTranscript(text, isFinal, 'user');
      },

      onLLMText: ({ text, isFinal }) => {
        this._showTranscript(text, isFinal, 'bot');
      },

      onTTSStart: ({ utteranceId }) => {
        this.player.beginUtterance(utteranceId);
      },

      onTTSChunk: (int16Data) => {
        this.player.pushChunk(int16Data);
      },

      onTTSEnd: () => {
        this.player.endUtterance();
      },

      onError: ({ code, message }) => {
        console.error(`服务端错误 [${code}]：${message}`);
        this._showError(message);
      },
    });

    // ── 麦克风采集（含 VAD）──────────────────────────────────────────────
    this.mic = new MicCaptureWithVAD({
      energyThreshold: -35,
      speechStartFrames: 3,
      speechEndFrames: 20,

      onSpeechStart: () => {
        // 如果 TTS 正在播放，允许用户打断
        if (this.player.isPlaying) {
          console.log('[App] 用户打断了 TTS');
          this.player.stop();
        }
        this.client.sendVADStart();
        this._updateStatus('正在听...');
      },

      onAudioFrame: (frame) => {
        this.client.sendAudioFrame(frame);
      },

      onSpeechEnd: (durationMs) => {
        this.client.sendVADEnd(durationMs);
        this._updateStatus('识别中...');
      },

      onError: (err) => {
        this._showError(err.message);
      },
    });

    // ── UI 引用 ───────────────────────────────────────────────────────────
    this._statusEl    = document.getElementById('status');
    this._errorEl     = document.getElementById('error');
    this._transcriptEl = document.getElementById('transcript');

    // 用于暂存 LLM 流式文字（打字机效果）
    this._currentBotBubble = null;
  }

  /**
   * 启动（在用户点击按钮后调用）
   */
  async start() {
    try {
      // 先连 WebSocket（不需要用户交互）
      this.client.connect();

      // 再开麦克风（需要用户授权）
      await this.mic.start();

    } catch (err) {
      this._showError(err.message);
      throw err;
    }
  }

  /**
   * 停止
   */
  async stop() {
    this.player.stop();
    await this.mic.stop();
    this.client.disconnect();
    this._updateStatus('已停止');
  }

  // ─── UI 辅助方法 ──────────────────────────────────────────────────────────

  _updateStatus(text) {
    if (this._statusEl) this._statusEl.textContent = text;
  }

  _showError(message) {
    if (this._errorEl) {
      this._errorEl.textContent = message;
      this._errorEl.style.display = 'block';
    }
  }

  _showTranscript(text, isFinal, role) {
    if (!this._transcriptEl) return;

    if (role === 'user') {
      if (isFinal) {
        const p = document.createElement('p');
        p.className = 'user-msg';
        p.textContent = `你：${text}`;
        this._transcriptEl.appendChild(p);
        this._transcriptEl.scrollTop = this._transcriptEl.scrollHeight;
      }
    } else {
      // bot 消息：流式更新
      if (!this._currentBotBubble) {
        this._currentBotBubble = document.createElement('p');
        this._currentBotBubble.className = 'bot-msg';
        this._transcriptEl.appendChild(this._currentBotBubble);
      }
      this._currentBotBubble.textContent = `助手：${text}`;
      this._transcriptEl.scrollTop = this._transcriptEl.scrollHeight;

      if (isFinal) {
        this._currentBotBubble = null;
      }
    }
  }
}

// ── 页面入口 ────────────────────────────────────────────────────────────────
let app = null;

document.getElementById('start-btn').addEventListener('click', async () => {
  document.getElementById('start-btn').disabled = true;
  document.getElementById('stop-btn').disabled = false;

  app = new VoiceBotApp();
  try {
    await app.start();
  } catch (err) {
    document.getElementById('start-btn').disabled = false;
    document.getElementById('stop-btn').disabled = true;
  }
});

document.getElementById('stop-btn').addEventListener('click', async () => {
  if (app) {
    await app.stop();
    app = null;
  }
  document.getElementById('start-btn').disabled = false;
  document.getElementById('stop-btn').disabled = true;
});
```

---

## 7.9 完整数据流总览

到这里，VoiceBot 的前端部分已经完整了。让我们回顾一下整个数据流：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           浏览器端                                       │
│                                                                         │
│  麦克风硬件                                                              │
│      │ 模拟声波                                                          │
│      ↓                                                                  │
│  getUserMedia (第4章)                                                    │
│      │ MediaStream                                                       │
│      ↓                                                                  │
│  AudioWorklet (第4章)                                                    │
│      │ Int16Array, 16kHz, 20ms/帧                                        │
│      ↓                                                                  │
│  EnergyVAD (第5章)                                                       │
│      │ 只在 SPEAKING/TRAILING 状态通过                                    │
│      ↓                                                                  │
│  VoiceBotClient (第6章)                                                  │
│      │ Binary: packAudioFrame(frame)      ── WebSocket ──→  服务端       │
│      │ JSON:   VAD_START / VAD_END        ── WebSocket ──→  服务端       │
│      │                                                                  │
│      ↑ JSON: ASR_RESULT / LLM_TEXT       ←─ WebSocket ──  服务端        │
│      ↑ JSON: TTS_START / TTS_END         ←─ WebSocket ──  服务端        │
│      ↑ Binary: TTS_CHUNK (Int16Array)    ←─ WebSocket ──  服务端        │
│      │                                                                  │
│      ↓                                                                  │
│  TTSPlayer (第7章)                                                       │
│      │ Float32Array, 16kHz                                              │
│      ↓                                                                  │
│  AudioContext.destination                                               │
│      │                                                                  │
│  扬声器                                                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7.10 调试建议

### 用 AudioContext 时间戳验证播放时序

```javascript
// 在 pushChunk 里加日志，验证时序是否正确
console.log(
  `[Player] chunk#${this._chunkCount} 计划在 ${startTime.toFixed(3)}s 播放，` +
  `当前时间 ${this._audioContext.currentTime.toFixed(3)}s，` +
  `提前量 ${((startTime - this._audioContext.currentTime) * 1000).toFixed(0)}ms`
);
```

正常情况下，"提前量"应该在 50ms 左右（我们设置的 bufferAheadSec）。如果提前量变得很小甚至为负数，说明音频到达太慢，可能出现卡顿。

### 测试打断功能

```javascript
// 临时测试：3 秒后模拟用户说话打断 TTS
setTimeout(() => {
  if (player.isPlaying) {
    console.log('模拟用户打断');
    player.stop();
  }
}, 3000);
```

### 检查 AudioContext 状态

```javascript
// 如果没有声音，先检查这个
console.log('AudioContext state:', audioContext.state);
// 如果是 'suspended'，需要 audioContext.resume()
```

---

## 本章小结

本章完成了 VoiceBot 前端的最后一块拼图——TTS 音频播放：

- **Web Audio API 播放 PCM**：`AudioBuffer` + `AudioBufferSourceNode`，Int16 先转 Float32 再播放
- **无缝拼接**：用 `nextPlayTime` 游标调度，每个 chunk 恰好接在上一个末尾，无间隙
- **TTSPlayer 封装**：管理播放队列、打断、采样率差异，通过 `onPlaybackEnd` 回调通知服务端
- **回声消除**：优先依赖浏览器硬件回声消除（`echoCancellation: true`），同时支持软件静音和允许打断两种策略
- **播放完毕回调**：`onPlaybackEnd` 触发 `client.sendTTSPlayed()`，形成完整的交互闭环

至此，VoiceBot 的完整前端数据流已经搭建完毕：麦克风采集 → VAD → WebSocket 发送 → WebSocket 接收 → TTS 播放。

**下一章**，我们转到服务端，用 Python + FastAPI + asyncio 搭建接收音频、调用 ASR、LLM、TTS 的后端服务，把前后端真正连接起来，跑通第一个端到端的 VoiceBot 对话。
