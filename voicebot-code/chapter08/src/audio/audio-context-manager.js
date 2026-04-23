
class AudioContextManager {
  constructor() {
    this._ctx = null;
    this._isResumed = false;
  }

  /**
   * 获取 AudioContext 实例
   * 必须在用户手势回调中调用 ensureResumed()
   */
  getContext() {
    if (!this._ctx) {
      // AudioContext 构造时就处于 suspended（iOS）或 running（其他）
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return this._ctx;
  }

  /**
   * 在用户手势里调用这个方法来激活 AudioContext
   * 只需要调用一次，之后 AudioContext 会保持 running 状态
   */
  async ensureResumed() {
    const ctx = this.getContext();
    if (ctx.state === 'suspended') {
      await ctx.resume();
      this._isResumed = true;
      console.log('[AudioContext] 已激活，state:', ctx.state);
    }
    return ctx;
  }

  get isRunning() {
    return this._ctx?.state === 'running';
  }
}

// 全局单例，整个应用共用一个 AudioContext
export const audioContextManager = new AudioContextManager();
