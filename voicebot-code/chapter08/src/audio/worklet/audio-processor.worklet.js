// 这个文件运行在独立的 AudioWorklet 线程里

class AudioProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = [];
    this._bufferSize = 0;
    // 每积累 4096 个采样点（约 256ms@16kHz）才发送一次，减少消息传递频率
    this._targetSize = 4096;
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const samples = input[0]; // Float32Array，单声道

    // 累积到目标大小再发送，避免过于频繁的消息传递
    this._buffer.push(...samples);
    this._bufferSize += samples.length;

    if (this._bufferSize >= this._targetSize) {
      // 把 Float32Array 转成 Int16Array（减少数据量一半）
      const int16 = this._float32ToInt16(new Float32Array(this._buffer));
      this.port.postMessage(int16, [int16.buffer]); // Transferable，零拷贝
      this._buffer = [];
      this._bufferSize = 0;
    }

    return true; // 返回 true 表示节点保持活跃
  }

  _float32ToInt16(float32Array) {
    const int16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      // 限幅到 [-1, 1]，然后乘以 32767
      const clamped = Math.max(-1, Math.min(1, float32Array[i]));
      int16[i] = clamped * 32767;
    }
    return int16;
  }
}

registerProcessor('audio-processor', AudioProcessor);
