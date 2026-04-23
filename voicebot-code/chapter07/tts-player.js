
/**
 * TTS 音频播放器
 *
 * 功能：
 * - 边收 chunk 边播放，无缝拼接
 * - 播放队列管理，支持打断
 * - 播放完毕回调（通知服务端）
 * - 回声消除配合（播放时暂停麦克风输入）
 */
export class TTSPlayer {
  /**
   * @param {object} options
   * @param {number}   [options.sampleRate=16000]  TTS 音频采样率
   * @param {number}   [options.bufferAheadSec=0.05] 预缓冲时间（秒），防止卡顿
   * @param {function} [options.onPlaybackStart]   开始播放某条 TTS 时的回调
   * @param {function} [options.onPlaybackEnd]     一条 TTS 全部播放完的回调，参数: utteranceId
   * @param {function} [options.onStateChange]     播放状态变化回调，参数: 'playing' | 'idle'
   */
  constructor({
    sampleRate = 16000,
    bufferAheadSec = 0.05,
    onPlaybackStart = () => {},
    onPlaybackEnd   = () => {},
    onStateChange   = () => {},
  } = {}) {
    this.sampleRate = sampleRate;
    this.bufferAheadSec = bufferAheadSec;
    this.onPlaybackStart = onPlaybackStart;
    this.onPlaybackEnd   = onPlaybackEnd;
    this.onStateChange   = onStateChange;

    // AudioContext（懒加载，在第一次播放时创建）
    this._audioContext = null;

    // 正在播放的 utterance id
    this._currentUtteranceId = null;

    // 下一个 chunk 的计划播放时间（AudioContext 时间轴上的秒数）
    this._nextPlayTime = 0;

    // 正在运行的 AudioBufferSourceNode 列表（用于打断播放）
    this._activeSources = [];

    // 是否正在播放
    this._isPlaying = false;

    // 当前 utterance 的总 chunk 数和已播放完的 chunk 数
    // 用于判断什么时候整条 TTS 播完了
    this._chunkCount = 0;
    this._endedChunkCount = 0;
    this._ttsEnded = false;   // 是否已收到 tts_end 消息
  }

  // ─── 公开 API ─────────────────────────────────────────────────────────────

  /**
   * 开始接收一条新的 TTS（收到 tts_start 消息时调用）
   * @param {string} utteranceId
   */
  beginUtterance(utteranceId) {
    // 如果上一条 TTS 还没播完，直接打断它
    if (this._isPlaying) {
      this._stopCurrent();
    }

    this._currentUtteranceId = utteranceId;
    this._chunkCount = 0;
    this._endedChunkCount = 0;
    this._ttsEnded = false;

    // 重置播放时间游标
    this._ensureAudioContext();
    this._nextPlayTime = this._audioContext.currentTime + this.bufferAheadSec;

    console.log(`[Player] 开始接收 TTS：${utteranceId}`);
    this.onPlaybackStart({ utteranceId });
  }

  /**
   * 接收一个 TTS 音频块并加入播放队列（收到 Binary:tts_chunk 时调用）
   * @param {Int16Array} chunk  Int16 PCM 数据，采样率为 this.sampleRate
   */
  pushChunk(chunk) {
    if (!this._currentUtteranceId) {
      console.warn('[Player] 收到 TTS chunk，但还没有 beginUtterance，忽略');
      return;
    }

    this._ensureAudioContext();
    this._chunkCount++;

    const chunkIndex = this._chunkCount;  // 用于回调顺序追踪

    try {
      // 1. Int16 → Float32
      const float32Data = this._int16ToFloat32(chunk);

      // 2. 创建 AudioBuffer
      const audioBuffer = this._audioContext.createBuffer(
        1,                   // 单声道
        float32Data.length,  // 采样数
        this.sampleRate      // 采样率
      );
      audioBuffer.getChannelData(0).set(float32Data);

      // 3. 计算这个 chunk 的播放时间
      const now = this._audioContext.currentTime;
      if (this._nextPlayTime < now) {
        // 如果游标落后了（比如 chunk 到达太慢），重新对齐
        const gap = now - this._nextPlayTime;
        if (gap > 0.1) {
          // 落后超过 100ms，重置游标
          console.warn(`[Player] 播放游标落后 ${(gap * 1000).toFixed(0)}ms，重置`);
          this._nextPlayTime = now + this.bufferAheadSec;
        }
      }

      const startTime = this._nextPlayTime;
      this._nextPlayTime += audioBuffer.duration;

      // 4. 创建并启动播放节点
      const source = this._audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this._audioContext.destination);

      source.onended = () => {
        // 从活跃节点列表中移除
        const idx = this._activeSources.indexOf(source);
        if (idx !== -1) this._activeSources.splice(idx, 1);

        this._endedChunkCount++;
        this._checkPlaybackComplete();
      };

      this._activeSources.push(source);
      source.start(startTime);

      if (!this._isPlaying) {
        this._isPlaying = true;
        this.onStateChange('playing');
      }

    } catch (err) {
      console.error('[Player] 播放 chunk 失败：', err);
    }
  }

  /**
   * 标记当前 TTS 的所有 chunk 已经发完（收到 tts_end 时调用）
   */
  endUtterance() {
    this._ttsEnded = true;
    this._checkPlaybackComplete();
  }

  /**
   * 立刻停止播放，清空队列
   */
  stop() {
    this._stopCurrent();
    this._setState('idle');
  }

  /**
   * 是否正在播放
   */
  get isPlaying() {
    return this._isPlaying;
  }

  /**
   * 获取当前播放的 utteranceId
   */
  get currentUtteranceId() {
    return this._currentUtteranceId;
  }

  // ─── 私有方法 ─────────────────────────────────────────────────────────────

  /**
   * 确保 AudioContext 已创建并处于运行状态
   * 注意：AudioContext 必须在用户交互后创建
   */
  _ensureAudioContext() {
    if (!this._audioContext) {
      this._audioContext = new AudioContext({
        latencyHint: 'interactive',
        sampleRate: this.sampleRate,
      });
    }

    // 如果 AudioContext 被挂起（浏览器自动播放策略），恢复它
    if (this._audioContext.state === 'suspended') {
      this._audioContext.resume().catch(console.error);
    }
  }

  /**
   * 停止当前播放的所有节点
   */
  _stopCurrent() {
    // 停止所有活跃的播放节点
    for (const source of this._activeSources) {
      try {
        source.stop();
        source.disconnect();
      } catch (e) {
        // 节点可能已经停止，忽略错误
      }
    }
    this._activeSources = [];
    this._isPlaying = false;
    this._currentUtteranceId = null;
    this._nextPlayTime = 0;
  }

  /**
   * 检查当前 utterance 是否已全部播完
   * 条件：已收到 tts_end + 所有 chunk 都播完了
   */
  _checkPlaybackComplete() {
    if (this._ttsEnded && this._endedChunkCount >= this._chunkCount) {
      const utteranceId = this._currentUtteranceId;
      this._isPlaying = false;
      this._currentUtteranceId = null;
      this.onStateChange('idle');
      this.onPlaybackEnd({ utteranceId });
      console.log(`[Player] TTS 播放完毕：${utteranceId}`);
    }
  }

  /**
   * Int16Array → Float32Array 格式转换
   */
  _int16ToFloat32(int16Data) {
    const float32Data = new Float32Array(int16Data.length);
    for (let i = 0; i < int16Data.length; i++) {
      float32Data[i] = int16Data[i] / 32768;
    }
    return float32Data;
  }

  _setState(state) {
    if (state === 'idle') {
      this._isPlaying = false;
    }
  }
}
