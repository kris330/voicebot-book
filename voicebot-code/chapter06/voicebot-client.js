
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
