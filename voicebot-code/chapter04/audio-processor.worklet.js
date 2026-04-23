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
