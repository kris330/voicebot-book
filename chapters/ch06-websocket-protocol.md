# 第六章：音频流传输协议

## 数据如何在浏览器和服务端之间流动？

---

前两章，我们在浏览器端搭好了音频采集和 VAD 模块。现在有一段干净的用户语音 PCM 数据摆在那里，需要发给服务端做 ASR 识别。

问题来了：**怎么发？**

最朴素的想法是 HTTP 请求——用户说完一句话，`POST` 一段 base64 编码的音频给服务端，等返回识别结果，再发 LLM 请求，再等 TTS 结果……

但这条路走不通，至少对流式 TTS 不行。TTS 生成的音频是一段一段流式返回的（就像 ChatGPT 打字一样，是一个字一个字出来的），用 HTTP 请求你要等全部生成完才能收到响应，延迟会非常高。

**WebSocket** 是正确答案。它是一条持久的全双工通道：客户端可以随时往里发数据，服务端也可以随时往外推数据，两个方向互不干扰。

本章的任务是：
1. 设计客户端和服务端的通信协议（消息格式）
2. 实现可靠的 WebSocket 客户端封装（含断线重连）
3. 把上一章的 VAD 输出接入 WebSocket 发送

---

## 6.1 WebSocket 基础回顾

WebSocket 协议建立在 HTTP 之上（通过 HTTP Upgrade 握手升级），一旦连接建立，就变成一条 TCP 长连接，支持：

- **文本消息**：任意字符串，我们用 JSON 格式发控制消息
- **二进制消息**：`ArrayBuffer` 或 `Blob`，我们用来发音频数据

```
浏览器                              服务端
  │                                   │
  │── HTTP GET /ws (Upgrade: websocket) ──→│
  │←── 101 Switching Protocols ─────────│
  │                                   │
  │  ←─────── WebSocket 全双工通道 ───────→│
  │                                   │
  │──→ JSON: {"type":"session_start"} ─→│
  │←── JSON: {"type":"session_info"}  ──│
  │                                   │
  │──→ Binary: PCM 音频帧 ─────────────→│
  │──→ Binary: PCM 音频帧 ─────────────→│
  │──→ JSON: {"type":"vad_end"} ───────→│
  │                                   │
  │←── JSON: {"type":"asr_result"} ───→│
  │←── Binary: TTS 音频 chunk ──────────│
  │←── Binary: TTS 音频 chunk ──────────│
  │←── JSON: {"type":"tts_end"} ───────│
```

---

## 6.2 协议设计

一个好的协议要解决两个问题：
1. **区分消息类型**：这条消息是控制信令还是音频数据？
2. **消息边界**：这条消息从哪里开始、到哪里结束？

WebSocket 天然解决了消息边界问题（WebSocket 帧有长度字段），所以我们只需要处理消息类型的区分。

### 两种消息通道

我们用消息**类型**区分控制消息和数据消息：

**文本通道（JSON）**：传控制信令
```json
{"type": "消息类型", "data": {...}, "seq": 1}
```

**二进制通道**：传音频数据
```
[1字节 消息类型标识][payload...]
```

> 为什么音频数据用二进制而不是 JSON？因为把 Int16Array 编码成 base64 会增加约 33% 的体积，而且还要 JSON 序列化/反序列化，对高频（每 20ms 一帧）的音频数据太浪费了。

### 文本消息类型

| 方向 | 类型 | 含义 | 主要字段 |
|------|------|------|----------|
| 客→服 | `session_start` | 建立会话 | `sample_rate`, `language` |
| 服→客 | `session_info` | 会话确认 | `session_id`, `server_time` |
| 客→服 | `vad_start` | 用户开始说话 | `timestamp` |
| 客→服 | `vad_end` | 用户说话结束 | `timestamp`, `duration_ms` |
| 服→客 | `asr_result` | ASR 识别结果 | `text`, `is_final` |
| 服→客 | `llm_text` | LLM 生成的文字 | `text`, `is_final` |
| 服→客 | `tts_start` | TTS 开始输出 | `utterance_id` |
| 服→客 | `tts_end` | TTS 结束输出 | `utterance_id`, `duration_ms` |
| 客→服 | `tts_played` | 客户端播放完毕 | `utterance_id` |
| 双向 | `ping` / `pong` | 心跳检测 | `timestamp` |
| 服→客 | `error` | 错误通知 | `code`, `message` |

### 二进制消息格式

二进制消息的第一个字节作为类型标识：

```
[类型字节 1B][payload N bytes]

类型字节定义：
  0x01 = audio_frame  （客→服，PCM 音频帧）
  0x02 = tts_chunk    （服→客，TTS 音频块）
```

这样服务端收到二进制消息时，只需要读第一个字节就知道这是什么数据。

### 完整消息定义

```javascript
// protocol.js
// 协议常量定义

// 文本消息类型
export const MessageType = Object.freeze({
  // 会话控制
  SESSION_START: 'session_start',
  SESSION_INFO:  'session_info',

  // VAD 事件
  VAD_START: 'vad_start',
  VAD_END:   'vad_end',

  // ASR 结果
  ASR_RESULT: 'asr_result',

  // LLM 输出
  LLM_TEXT: 'llm_text',

  // TTS 控制
  TTS_START:  'tts_start',
  TTS_END:    'tts_end',
  TTS_PLAYED: 'tts_played',

  // 心跳
  PING: 'ping',
  PONG: 'pong',

  // 错误
  ERROR: 'error',
});

// 二进制帧类型（第一个字节）
export const BinaryType = Object.freeze({
  AUDIO_FRAME: 0x01,   // 客→服：麦克风 PCM 数据
  TTS_CHUNK:   0x02,   // 服→客：TTS PCM 数据
});

/**
 * 构造一个 JSON 控制消息
 * @param {string} type     MessageType 中的一个
 * @param {object} data     消息体
 * @param {number} [seq]    序列号（可选）
 */
export function makeTextMessage(type, data = {}, seq = undefined) {
  const msg = { type, ...data };
  if (seq !== undefined) msg.seq = seq;
  return JSON.stringify(msg);
}

/**
 * 把 Int16Array 音频帧打包成二进制消息
 * 格式：[0x01][Int16 采样数据...]
 *
 * @param {Int16Array} frame
 * @returns {ArrayBuffer}
 */
export function packAudioFrame(frame) {
  // 总长度 = 1字节类型 + N字节音频数据
  const buffer = new ArrayBuffer(1 + frame.byteLength);
  const view = new DataView(buffer);

  // 第一个字节：类型标识
  view.setUint8(0, BinaryType.AUDIO_FRAME);

  // 后续字节：音频数据（直接内存拷贝）
  new Int16Array(buffer, 1).set(frame);

  return buffer;
}

/**
 * 解析服务端发来的二进制消息
 * @param {ArrayBuffer} buffer
 * @returns {{ type: number, data: Int16Array }}
 */
export function unpackBinaryMessage(buffer) {
  const view = new DataView(buffer);
  const type = view.getUint8(0);

  // 音频数据从第 1 个字节开始，以 Int16 方式解释
  // 注意：offset 必须是 2 的倍数（Int16Array 的对齐要求）
  // 如果原始 buffer 的偏移不对齐，需要先 slice
  const audioBuffer = buffer.slice(1);
  const data = new Int16Array(audioBuffer);

  return { type, data };
}
```

---

## 6.3 分帧策略：流式传输 vs 整句传输

上一章我们讨论过 VAD 的两种工作模式，对应两种不同的分帧策略：

### 策略 A：整句传输（推荐新手起步）

```
说话开始 ──────────────────── 说话结束
    │                              │
    ▼                              ▼
  [VAD_START]  [保存音频到内存]  [VAD_END + 发送完整音频]
```

实现简单，但有一个问题：如果用户说了 10 秒，这 10 秒的音频要到说话结束才发出去，ASR 才开始工作，延迟很高。

### 策略 B：流式传输（推荐生产环境）

```
说话开始后，每 20ms 发一帧
    │
    ▼
[VAD_START] → [AUDIO_FRAME] → [AUDIO_FRAME] → ... → [VAD_END]
```

ASR 可以在用户还在说话时就开始识别，大幅降低首字延迟。这是我们 VoiceBot 采用的方案。

```
时间线（用户说"今天天气怎么样"，耗时约 1.5 秒）：

t=0.0s  用户开口 → 发送 VAD_START
t=0.0s  发送 AUDIO_FRAME #1（前 20ms）
t=0.02s 发送 AUDIO_FRAME #2
t=0.04s 发送 AUDIO_FRAME #3
...
t=0.5s  ASR 已经识别出"今天天"（部分结果）
...
t=1.5s  用户说完 → 发送 VAD_END
t=1.6s  ASR 返回最终结果"今天天气怎么样"

相比整句传输（t=1.5s 才发数据，t=2.0s 才有结果）
流式传输可以节省约 0.5 秒延迟
```

---

## 6.4 断线重连机制

网络不稳定是现实，WebSocket 连接可能随时断开。一个好的客户端必须能自动重连。

重连策略：**指数退避（Exponential Backoff）**

```
第 1 次断线 → 等 1 秒重连
第 2 次断线 → 等 2 秒重连
第 3 次断线 → 等 4 秒重连
第 4 次断线 → 等 8 秒重连
...最多等 30 秒
```

```
┌─────────────┐     连接成功      ┌────────────┐
│  CONNECTING │ ─────────────────→ │  CONNECTED │
│  （连接中）  │ ←───────────────── │  （已连接） │
└─────────────┘     连接断开       └────────────┘
       ↑                                 │
       │                           网络断开/主动关闭
       │                                 │
       │           ┌──────────────┐      │
       └─────────── │ RECONNECTING │ ←───┘
         等待后重连  │  （重连中）   │
                   └──────────────┘
                         │
                   超过最大重试次数
                         │
                         ▼
                   ┌──────────┐
                   │  FAILED  │
                   │  （失败） │
                   └──────────┘
```

---

## 6.5 完整的 WebSocket 客户端封装

```javascript
// voicebot-client.js

import {
  MessageType,
  BinaryType,
  makeTextMessage,
  packAudioFrame,
  unpackBinaryMessage,
} from './protocol.js';

// WebSocket 连接状态
export const ConnectionState = Object.freeze({
  DISCONNECTED:  'disconnected',
  CONNECTING:    'connecting',
  CONNECTED:     'connected',
  RECONNECTING:  'reconnecting',
  FAILED:        'failed',
});

export class VoiceBotClient {
  /**
   * @param {object} options
   * @param {string}   options.url               WebSocket 服务端地址
   * @param {number}   [options.sampleRate=16000] 音频采样率
   * @param {string}   [options.language='zh']   语言代码
   * @param {number}   [options.maxRetries=5]    最大重试次数
   * @param {number}   [options.baseDelay=1000]  初始重试延迟（ms）
   * @param {number}   [options.maxDelay=30000]  最大重试延迟（ms）
   * @param {number}   [options.pingInterval=15000] 心跳间隔（ms）
   *
   * 事件回调：
   * @param {function} [options.onConnect]       连接成功
   * @param {function} [options.onDisconnect]    连接断开，参数: {code, reason}
   * @param {function} [options.onReconnecting]  正在重连，参数: {attempt, delay}
   * @param {function} [options.onFailed]        重连失败放弃
   * @param {function} [options.onASRResult]     ASR 结果，参数: {text, isFinal}
   * @param {function} [options.onLLMText]       LLM 文字，参数: {text, isFinal}
   * @param {function} [options.onTTSStart]      TTS 开始，参数: {utteranceId}
   * @param {function} [options.onTTSChunk]      TTS 音频块，参数: Int16Array
   * @param {function} [options.onTTSEnd]        TTS 结束，参数: {utteranceId, durationMs}
   * @param {function} [options.onError]         服务端错误，参数: {code, message}
   */
  constructor(options) {
    const {
      url,
      sampleRate = 16000,
      language = 'zh',
      maxRetries = 5,
      baseDelay = 1000,
      maxDelay = 30000,
      pingInterval = 15000,

      onConnect     = () => {},
      onDisconnect  = () => {},
      onReconnecting = () => {},
      onFailed      = () => {},
      onASRResult   = () => {},
      onLLMText     = () => {},
      onTTSStart    = () => {},
      onTTSChunk    = () => {},
      onTTSEnd      = () => {},
      onError       = console.error,
    } = options;

    this.url = url;
    this.sampleRate = sampleRate;
    this.language = language;
    this.maxRetries = maxRetries;
    this.baseDelay = baseDelay;
    this.maxDelay = maxDelay;
    this.pingInterval = pingInterval;

    // 事件回调
    this.handlers = {
      onConnect, onDisconnect, onReconnecting, onFailed,
      onASRResult, onLLMText, onTTSStart, onTTSChunk, onTTSEnd, onError,
    };

    // 内部状态
    this._ws = null;
    this._state = ConnectionState.DISCONNECTED;
    this._retryCount = 0;
    this._retryTimer = null;
    this._pingTimer = null;
    this._sessionId = null;
    this._messageSeq = 0;

    // 是否主动断开（区分主动断开和异常断开）
    this._intentionalClose = false;
  }

  // ─── 公开 API ────────────────────────────────────────────────────────────

  /**
   * 连接到服务端
   */
  connect() {
    if (this._state === ConnectionState.CONNECTED ||
        this._state === ConnectionState.CONNECTING) {
      return;
    }
    this._intentionalClose = false;
    this._retryCount = 0;
    this._connect();
  }

  /**
   * 主动断开连接
   */
  disconnect() {
    this._intentionalClose = true;
    this._clearTimers();

    if (this._ws) {
      this._ws.close(1000, 'client disconnect');
      this._ws = null;
    }
    this._setState(ConnectionState.DISCONNECTED);
  }

  /**
   * 通知服务端：用户开始说话
   */
  sendVADStart() {
    this._sendText(makeTextMessage(MessageType.VAD_START, {
      timestamp: Date.now(),
    }));
  }

  /**
   * 通知服务端：用户说话结束
   * @param {number} durationMs  说话时长（ms）
   */
  sendVADEnd(durationMs) {
    this._sendText(makeTextMessage(MessageType.VAD_END, {
      timestamp: Date.now(),
      duration_ms: durationMs,
    }));
  }

  /**
   * 发送音频帧（每 20ms 一帧）
   * @param {Int16Array} frame
   */
  sendAudioFrame(frame) {
    if (this._state !== ConnectionState.CONNECTED) return;

    const buffer = packAudioFrame(frame);
    try {
      this._ws.send(buffer);
    } catch (err) {
      console.error('发送音频帧失败：', err);
    }
  }

  /**
   * 通知服务端：TTS 音频已播放完毕
   * @param {string} utteranceId
   */
  sendTTSPlayed(utteranceId) {
    this._sendText(makeTextMessage(MessageType.TTS_PLAYED, { utteranceId }));
  }

  /**
   * 获取当前连接状态
   */
  get state() {
    return this._state;
  }

  /**
   * 是否已连接
   */
  get isConnected() {
    return this._state === ConnectionState.CONNECTED;
  }

  // ─── 内部方法 ────────────────────────────────────────────────────────────

  _connect() {
    this._setState(ConnectionState.CONNECTING);

    try {
      this._ws = new WebSocket(this.url);
      this._ws.binaryType = 'arraybuffer'; // 接收二进制数据时使用 ArrayBuffer

      this._ws.onopen    = () => this._onOpen();
      this._ws.onmessage = (evt) => this._onMessage(evt);
      this._ws.onclose   = (evt) => this._onClose(evt);
      this._ws.onerror   = (evt) => this._onWSError(evt);
    } catch (err) {
      console.error('创建 WebSocket 失败：', err);
      this._scheduleReconnect();
    }
  }

  _onOpen() {
    console.log('[WS] 连接成功');
    this._setState(ConnectionState.CONNECTED);
    this._retryCount = 0;

    // 发送会话初始化消息
    this._sendText(makeTextMessage(MessageType.SESSION_START, {
      sample_rate: this.sampleRate,
      language: this.language,
    }));

    // 启动心跳
    this._startPing();

    this.handlers.onConnect();
  }

  _onMessage(evt) {
    if (typeof evt.data === 'string') {
      this._handleTextMessage(evt.data);
    } else if (evt.data instanceof ArrayBuffer) {
      this._handleBinaryMessage(evt.data);
    }
  }

  _handleTextMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.error('[WS] 无效的 JSON 消息：', raw);
      return;
    }

    switch (msg.type) {
      case MessageType.SESSION_INFO:
        this._sessionId = msg.session_id;
        console.log(`[WS] 会话建立，ID: ${this._sessionId}`);
        break;

      case MessageType.ASR_RESULT:
        this.handlers.onASRResult({
          text: msg.text,
          isFinal: msg.is_final,
        });
        break;

      case MessageType.LLM_TEXT:
        this.handlers.onLLMText({
          text: msg.text,
          isFinal: msg.is_final,
        });
        break;

      case MessageType.TTS_START:
        this.handlers.onTTSStart({ utteranceId: msg.utterance_id });
        break;

      case MessageType.TTS_END:
        this.handlers.onTTSEnd({
          utteranceId: msg.utterance_id,
          durationMs: msg.duration_ms,
        });
        break;

      case MessageType.PONG:
        // 心跳响应，什么都不需要做
        break;

      case MessageType.ERROR:
        console.error(`[WS] 服务端错误 [${msg.code}]：${msg.message}`);
        this.handlers.onError({ code: msg.code, message: msg.message });
        break;

      default:
        console.warn('[WS] 未知消息类型：', msg.type);
    }
  }

  _handleBinaryMessage(buffer) {
    try {
      const { type, data } = unpackBinaryMessage(buffer);

      if (type === BinaryType.TTS_CHUNK) {
        this.handlers.onTTSChunk(data);
      } else {
        console.warn('[WS] 未知二进制消息类型：', type);
      }
    } catch (err) {
      console.error('[WS] 解析二进制消息失败：', err);
    }
  }

  _onClose(evt) {
    console.log(`[WS] 连接关闭，code=${evt.code}, reason=${evt.reason}`);
    this._clearTimers();
    this._ws = null;

    const wasConnected = this._state === ConnectionState.CONNECTED;

    if (this._intentionalClose) {
      this._setState(ConnectionState.DISCONNECTED);
      this.handlers.onDisconnect({ code: evt.code, reason: evt.reason });
      return;
    }

    // 非主动关闭：尝试重连
    this.handlers.onDisconnect({ code: evt.code, reason: evt.reason });
    this._scheduleReconnect();
  }

  _onWSError(evt) {
    // WebSocket 的 error 事件通常紧跟着 close 事件
    // 实际错误处理逻辑在 _onClose 里
    console.error('[WS] WebSocket error 事件');
  }

  _scheduleReconnect() {
    if (this._retryCount >= this.maxRetries) {
      console.error(`[WS] 已重试 ${this.maxRetries} 次，放弃连接`);
      this._setState(ConnectionState.FAILED);
      this.handlers.onFailed();
      return;
    }

    // 指数退避：delay = baseDelay * 2^retryCount，上限 maxDelay
    const delay = Math.min(
      this.baseDelay * Math.pow(2, this._retryCount),
      this.maxDelay
    );
    this._retryCount++;

    console.log(`[WS] ${delay}ms 后进行第 ${this._retryCount} 次重连...`);
    this._setState(ConnectionState.RECONNECTING);
    this.handlers.onReconnecting({ attempt: this._retryCount, delay });

    this._retryTimer = setTimeout(() => {
      this._connect();
    }, delay);
  }

  _sendText(message) {
    if (this._state !== ConnectionState.CONNECTED || !this._ws) {
      console.warn('[WS] 未连接，无法发送文本消息');
      return false;
    }
    try {
      this._ws.send(message);
      return true;
    } catch (err) {
      console.error('[WS] 发送文本消息失败：', err);
      return false;
    }
  }

  _setState(newState) {
    if (this._state !== newState) {
      console.log(`[WS] 状态变更：${this._state} → ${newState}`);
      this._state = newState;
    }
  }

  _startPing() {
    this._pingTimer = setInterval(() => {
      this._sendText(makeTextMessage(MessageType.PING, {
        timestamp: Date.now(),
      }));
    }, this.pingInterval);
  }

  _clearTimers() {
    if (this._retryTimer) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
    if (this._pingTimer) {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }
  }
}
```

---

## 6.6 发送端：接入 VAD 输出

把上一章的 `MicCaptureWithVAD` 和这章的 `VoiceBotClient` 连接起来：

```javascript
// audio-sender.js
// 负责将 VAD 检测到的语音帧通过 WebSocket 发送给服务端

import { MicCaptureWithVAD } from './mic-capture-with-vad.js';
import { VoiceBotClient, ConnectionState } from './voicebot-client.js';

export class AudioSender {
  constructor({ wsUrl, onStateChange = () => {}, onASRResult = () => {} }) {
    // 初始化 WebSocket 客户端
    this.client = new VoiceBotClient({
      url: wsUrl,

      onConnect: () => {
        onStateChange('connected');
        console.log('WebSocket 已连接，可以开始说话');
      },

      onDisconnect: ({ code, reason }) => {
        onStateChange('disconnected');
      },

      onReconnecting: ({ attempt, delay }) => {
        onStateChange(`reconnecting (${attempt})`);
      },

      onASRResult: ({ text, isFinal }) => {
        onASRResult({ text, isFinal });
      },

      // TTS 相关回调由第 7 章的播放器处理
      onTTSChunk:  () => {},
      onTTSStart:  () => {},
      onTTSEnd:    () => {},
    });

    // 初始化麦克风采集（含 VAD）
    this.mic = new MicCaptureWithVAD({
      energyThreshold: -35,
      speechStartFrames: 3,
      speechEndFrames: 20,

      onSpeechStart: () => {
        // 通知服务端：用户开始说话
        this.client.sendVADStart();
      },

      onAudioFrame: (frame) => {
        // 流式发送音频帧
        this.client.sendAudioFrame(frame);
      },

      onSpeechEnd: (durationMs) => {
        // 通知服务端：用户说话结束
        this.client.sendVADEnd(durationMs);
      },
    });
  }

  async start() {
    // 先连接 WebSocket
    this.client.connect();

    // 再开启麦克风（会弹权限框，在用户点击后调用）
    await this.mic.start();
  }

  async stop() {
    await this.mic.stop();
    this.client.disconnect();
  }
}
```

---

## 6.7 接收端：处理服务端消息

服务端会推送两类关键数据：
1. **JSON 消息**：ASR 识别结果、TTS 控制事件
2. **二进制消息**：TTS PCM 音频块

这些已经在 `VoiceBotClient` 里通过回调分发了。主页面代码这样使用：

```javascript
// main.js（完整版）

import { AudioSender } from './audio-sender.js';

const WS_URL = `ws://${location.host}/ws/voice`;

// UI 元素
const startBtn  = document.getElementById('start-btn');
const stopBtn   = document.getElementById('stop-btn');
const statusEl  = document.getElementById('status');
const transcript = document.getElementById('transcript');

let sender = null;

async function start() {
  startBtn.disabled = true;
  statusEl.textContent = '正在连接...';

  sender = new AudioSender({
    wsUrl: WS_URL,

    onStateChange(state) {
      statusEl.textContent = {
        'connected':    '已连接，可以说话',
        'disconnected': '连接断开',
        'reconnecting (1)': '重连中 (1/5)...',
        'reconnecting (2)': '重连中 (2/5)...',
      }[state] || state;
    },

    onASRResult({ text, isFinal }) {
      if (isFinal) {
        // 最终识别结果，追加到对话记录
        const p = document.createElement('p');
        p.textContent = `用户：${text}`;
        transcript.appendChild(p);
      } else {
        // 中间结果，实时显示
        statusEl.textContent = `识别中：${text}`;
      }
    },
  });

  try {
    await sender.start();
    stopBtn.disabled = false;
  } catch (err) {
    alert(err.message);
    startBtn.disabled = false;
    sender = null;
  }
}

async function stop() {
  if (!sender) return;
  await sender.stop();
  sender = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  statusEl.textContent = '已停止';
}

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
```

---

## 6.8 消息时序图

用户说一句话，完整的消息交互如下：

```
浏览器                                      服务端
  │                                            │
  │────── WS 连接建立 ─────────────────────────→│
  │────── session_start ───────────────────────→│
  │←───── session_info ─────────────────────────│
  │                                            │
  │  [用户开始说话]                             │
  │────── vad_start ───────────────────────────→│  开始 ASR 流
  │────── Binary:audio_frame(0~20ms) ──────────→│
  │────── Binary:audio_frame(20~40ms) ─────────→│
  │────── Binary:audio_frame(40~60ms) ─────────→│
  │         ...                               │
  │←───── asr_result(text="今天", is_final=false)│  中间识别
  │         ...                               │
  │────── Binary:audio_frame(1480~1500ms) ─────→│
  │────── vad_end(duration_ms=1500) ───────────→│  ASR 提交最终识别
  │                                            │
  │←───── asr_result(text="今天天气怎么样", is_final=true)
  │                                            │  LLM 开始生成
  │←───── llm_text(text="今天", is_final=false) │
  │←───── llm_text(text="今天天气", is_final=false)
  │         ...                               │
  │←───── llm_text(text="今天天气不错……", is_final=true)
  │                                            │  TTS 开始合成
  │←───── tts_start(utterance_id="utt-001") ───│
  │←───── Binary:tts_chunk(前 20ms PCM) ────────│
  │←───── Binary:tts_chunk(20~40ms PCM) ────────│
  │         ...                               │
  │←───── tts_end(utterance_id="utt-001") ─────│
  │  [播放完毕]                                │
  │────── tts_played(utterance_id="utt-001") ──→│
  │                                            │
```

---

## 6.9 常见问题

### 问题：WebSocket 连接被服务端拒绝（403）

通常是跨域问题。服务端需要配置允许前端页面的来源：

```python
# FastAPI + WebSocket 跨域配置
# 第 8 章会详细讲服务端，这里先给一个参考
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
)
```

### 问题：发送大量音频帧时 WebSocket 缓冲区积压

如果网络慢，发送速度赶不上采集速度，WebSocket 发送缓冲区会积压。

```javascript
// 检查缓冲区状态，避免积压
sendAudioFrame(frame) {
  if (this._state !== ConnectionState.CONNECTED) return;

  // bufferedAmount 是待发送字节数
  // 如果超过 1MB，说明网络很慢，暂时跳过这帧
  if (this._ws.bufferedAmount > 1024 * 1024) {
    console.warn('[WS] 发送缓冲区积压，跳过此帧');
    return;
  }

  this._ws.send(packAudioFrame(frame));
}
```

### 问题：重连后 session 状态丢失

重连后需要重新发送 `session_start` 消息。这在我们的实现里已经在 `_onOpen` 里处理了。

如果服务端维护了会话状态（比如对话历史），需要在 `session_start` 里带上上次的 `session_id`：

```javascript
// 重连时带上旧的 session_id
this._sendText(makeTextMessage(MessageType.SESSION_START, {
  sample_rate: this.sampleRate,
  language: this.language,
  resume_session_id: this._sessionId || null,  // 如果有，尝试恢复
}));
```

---

## 本章小结

本章设计并实现了 VoiceBot 的音频流传输协议：

- **协议分层**：JSON 文本通道用于控制信令，二进制通道用于音频数据
- **消息设计**：定义了 10 余种消息类型，覆盖 VAD 事件、ASR 结果、TTS 流等
- **分帧策略**：流式传输（每 20ms 一帧）比整句传输延迟低约 0.5 秒
- **断线重连**：指数退避策略，最多重试 N 次，失败后通知用户
- **心跳检测**：定期发送 PING，防止连接被中间代理超时断开
- **完整封装**：`VoiceBotClient` 把所有细节封装起来，主逻辑只关心回调

这一章打通了浏览器和服务端的通信管道。音频能发出去，TTS 能收回来。

**下一章**，我们来处理最后一块拼图：把从服务端流回来的 TTS 音频 chunk，在浏览器里边接收边播放，实现流畅的语音输出。
