
export const VADState = {
  SILENCE:  'silence',   // 静音
  SPEAKING: 'speaking',  // 说话中
  TRAILING: 'trailing',  // 尾音等待
};

export class EnergyVAD {
  /**
   * @param {object} options
   * @param {number} options.energyThreshold   能量阈值，dBFS，默认 -35
   * @param {number} options.speechStartFrames 连续多少帧有声才算开始，默认 3（约 60ms）
   * @param {number} options.speechEndFrames   连续多少帧静音才算结束，默认 20（约 400ms）
   * @param {function} options.onSpeechStart   说话开始回调
   * @param {function} options.onSpeechEnd     说话结束回调，参数是说话时长（ms）
   * @param {function} options.onFrame         每帧回调，参数是 {state, energyDB, isSpeech}
   */
  constructor({
    energyThreshold = -35,
    speechStartFrames = 3,
    speechEndFrames = 20,
    onSpeechStart = () => {},
    onSpeechEnd = () => {},
    onFrame = () => {},
  } = {}) {
    this.energyThreshold = energyThreshold;
    this.speechStartFrames = speechStartFrames;
    this.speechEndFrames = speechEndFrames;
    this.onSpeechStart = onSpeechStart;
    this.onSpeechEnd = onSpeechEnd;
    this.onFrame = onFrame;

    // 状态机
    this.state = VADState.SILENCE;

    // 连续有声帧计数（用于判断说话开始）
    this.consecutiveSpeechFrames = 0;

    // 连续静音帧计数（用于判断说话结束）
    this.consecutiveSilenceFrames = 0;

    // 说话开始的时间戳
    this.speechStartTime = null;

    // 在 TRAILING 阶段缓存的帧（以防最终判定是继续说话）
    this.trailingFrames = [];
  }

  /**
   * 处理一帧音频数据
   *
   * @param {Float32Array | Int16Array} frame  一帧音频采样
   * @param {number} frameDurationMs          这帧对应的时长，默认 20ms
   * @returns {object} { state, energyDB, isSpeech }
   */
  processFrame(frame, frameDurationMs = 20) {
    const energyDB = this._calculateEnergyDB(frame);
    const isSpeech = energyDB > this.energyThreshold;

    const prevState = this.state;
    this._updateState(isSpeech, frameDurationMs);

    const result = {
      state: this.state,
      energyDB,
      isSpeech,
      stateChanged: this.state !== prevState,
    };

    this.onFrame(result);
    return result;
  }

  /**
   * 重置状态机到初始状态
   */
  reset() {
    this.state = VADState.SILENCE;
    this.consecutiveSpeechFrames = 0;
    this.consecutiveSilenceFrames = 0;
    this.speechStartTime = null;
    this.trailingFrames = [];
  }

  // ─── 私有方法 ────────────────────────────────────────────────────────────

  /**
   * 计算音频帧的能量（dBFS）
   * 支持 Float32Array 和 Int16Array
   */
  _calculateEnergyDB(frame) {
    let sumOfSquares = 0;
    const n = frame.length;

    if (frame instanceof Float32Array) {
      for (let i = 0; i < n; i++) {
        sumOfSquares += frame[i] * frame[i];
      }
    } else if (frame instanceof Int16Array) {
      // Int16 范围是 [-32768, 32767]，归一化到 [-1, 1]
      const scale = 1 / 32768;
      for (let i = 0; i < n; i++) {
        const normalized = frame[i] * scale;
        sumOfSquares += normalized * normalized;
      }
    } else {
      throw new Error(`不支持的音频格式：${frame.constructor.name}`);
    }

    const rms = Math.sqrt(sumOfSquares / n);
    if (rms === 0) return -100; // 避免 log(0)

    return 20 * Math.log10(rms);
  }

  /**
   * 状态机转换逻辑
   */
  _updateState(isSpeech, frameDurationMs) {
    switch (this.state) {
      case VADState.SILENCE:
        if (isSpeech) {
          this.consecutiveSpeechFrames++;
          if (this.consecutiveSpeechFrames >= this.speechStartFrames) {
            // 连续 N 帧有声 → 转为 SPEAKING
            this.state = VADState.SPEAKING;
            this.consecutiveSpeechFrames = 0;
            this.consecutiveSilenceFrames = 0;
            this.speechStartTime = Date.now();
            this.onSpeechStart();
          }
        } else {
          // 静音时重置有声帧计数
          this.consecutiveSpeechFrames = 0;
        }
        break;

      case VADState.SPEAKING:
        if (!isSpeech) {
          // 检测到静音，进入 TRAILING 阶段
          this.state = VADState.TRAILING;
          this.consecutiveSilenceFrames = 1;
          this.trailingFrames = [];
        }
        // 有声时保持 SPEAKING，不做状态变化
        break;

      case VADState.TRAILING:
        if (isSpeech) {
          // 又检测到声音，说明用户只是短暂停顿，回到 SPEAKING
          this.state = VADState.SPEAKING;
          this.consecutiveSilenceFrames = 0;
          this.trailingFrames = [];
        } else {
          this.consecutiveSilenceFrames++;
          if (this.consecutiveSilenceFrames >= this.speechEndFrames) {
            // 连续 M 帧静音 → 说话结束
            const speechDurationMs = Date.now() - this.speechStartTime;
            this.state = VADState.SILENCE;
            this.consecutiveSilenceFrames = 0;
            this.speechStartTime = null;
            this.trailingFrames = [];
            this.onSpeechEnd(speechDurationMs);
          }
        }
        break;
    }
  }
}
