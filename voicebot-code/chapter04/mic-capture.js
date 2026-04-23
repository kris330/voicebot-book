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
