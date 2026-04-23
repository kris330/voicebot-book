
import { audioContextManager } from './audio-context-manager.js';

/**
 * 监听页面可见性，在后台时暂停音频处理，切回前台时恢复
 * 这能显著减少后台功耗，避免在用户不使用时持续占用资源
 */
class VisibilityManager {
  constructor() {
    this._recorder = null;
    this._wasRecording = false;
    this._onPause = null;
    this._onResume = null;
  }

  init({ onPause, onResume } = {}) {
    this._onPause = onPause;
    this._onResume = onResume;

    document.addEventListener('visibilitychange', this._handleVisibilityChange.bind(this));

    // iOS Safari 的页面隐藏事件（visibilitychange 有时不可靠）
    window.addEventListener('pagehide', this._handlePageHide.bind(this));
    window.addEventListener('pageshow', this._handlePageShow.bind(this));
  }

  _handleVisibilityChange() {
    if (document.visibilityState === 'hidden') {
      this._handleBackground();
    } else if (document.visibilityState === 'visible') {
      this._handleForeground();
    }
  }

  _handlePageHide(event) {
    // event.persisted = true 表示页面进入 BFCache（返回时不会重新加载）
    this._handleBackground();
  }

  _handlePageShow(event) {
    if (event.persisted) {
      // 从 BFCache 恢复，AudioContext 可能已经被挂起
      this._handleForeground();
    }
  }

  _handleBackground() {
    const ctx = audioContextManager.getContext();
    if (ctx?.state === 'running') {
      // 挂起 AudioContext，释放音频硬件资源
      ctx.suspend().then(() => {
        console.log('[可见性] 页面进入后台，音频已暂停');
      });
    }

    if (this._onPause) {
      this._onPause();
    }
  }

  _handleForeground() {
    const ctx = audioContextManager.getContext();
    if (ctx?.state === 'suspended') {
      // 注意：resume() 在某些情况下需要用户手势，这里可能失败
      ctx.resume().then(() => {
        console.log('[可见性] 页面回到前台，音频已恢复');
      }).catch((err) => {
        console.warn('[可见性] 恢复音频失败，需要用户点击:', err);
        // 显示一个提示让用户点击
        showTapToResumeHint();
      });
    }

    if (this._onResume) {
      this._onResume();
    }
  }
}

function showTapToResumeHint() {
  const hint = document.getElementById('tap-to-resume');
  if (hint) {
    hint.style.display = 'block';
    hint.addEventListener('click', async () => {
      await audioContextManager.ensureResumed();
      hint.style.display = 'none';
    }, { once: true });
  }
}

export const visibilityManager = new VisibilityManager();
