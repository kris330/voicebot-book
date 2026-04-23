
export class AudioRecorder {
  constructor(audioContext) {
    this._ctx = audioContext;
    this._workletNode = null;
    this._stream = null;
    this._onAudioData = null;
  }

  /**
   * 初始化 AudioWorklet（需要提前加载 worklet 模块）
   */
  async init() {
    await this._ctx.audioWorklet.addModule('/src/audio/worklet/audio-processor.worklet.js');
    console.log('[AudioRecorder] Worklet 加载完成');
  }

  /**
   * 开始录音
   * @param {MediaStream} stream - 麦克风流
   * @param {Function} onData - 收到音频数据的回调，参数为 Int16Array
   */
  async start(stream, onData) {
    this._stream = stream;
    this._onAudioData = onData;

    // 创建麦克风输入节点
    const source = this._ctx.createMediaStreamSource(stream);

    // 创建 Worklet 处理节点
    this._workletNode = new AudioWorkletNode(this._ctx, 'audio-processor');

    // 监听 worklet 发来的数据
    this._workletNode.port.onmessage = (event) => {
      if (this._onAudioData) {
        this._onAudioData(event.data); // Int16Array
      }
    };

    // 连接音频图
    source.connect(this._workletNode);
    // 注意：worklet 节点不需要连接到 destination，它只是处理数据

    console.log('[AudioRecorder] 开始录音');
  }

  stop() {
    if (this._stream) {
      this._stream.getTracks().forEach((track) => track.stop());
      this._stream = null;
    }
    if (this._workletNode) {
      this._workletNode.disconnect();
      this._workletNode = null;
    }
    console.log('[AudioRecorder] 停止录音');
  }
}
