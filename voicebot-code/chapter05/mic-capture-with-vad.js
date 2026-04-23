
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
