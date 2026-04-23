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
