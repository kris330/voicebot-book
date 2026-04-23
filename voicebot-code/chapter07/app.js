
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
