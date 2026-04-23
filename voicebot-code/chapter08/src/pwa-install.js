
class PWAInstallManager {
  constructor() {
    this._deferredPrompt = null;
    this._installBtn = null;
    this._sessionStartCount = 0;
  }

  init() {
    // 拦截浏览器的默认安装提示
    window.addEventListener('beforeinstallprompt', (event) => {
      event.preventDefault(); // 阻止立即显示
      this._deferredPrompt = event;
      console.log('[PWA] 安装提示已准备好');

      // 用户用了 3 次以上，才显示安装按钮
      this._sessionStartCount++;
      if (this._sessionStartCount >= 3) {
        this._showInstallButton();
      }
    });

    // 监听安装完成事件
    window.addEventListener('appinstalled', () => {
      console.log('[PWA] 用户已安装 VoiceBot');
      this._deferredPrompt = null;
      this._hideInstallButton();
      // 可以记录到分析平台
    });

    // 检查是否已经在 standalone 模式下运行（已安装）
    if (window.matchMedia('(display-mode: standalone)').matches) {
      console.log('[PWA] 正在以独立应用模式运行');
      document.body.classList.add('pwa-standalone');
    }
  }

  async triggerInstall() {
    if (!this._deferredPrompt) {
      alert('请使用浏览器菜单中的"添加到主屏幕"选项');
      return;
    }

    // 显示浏览器的原生安装对话框
    this._deferredPrompt.prompt();

    const { outcome } = await this._deferredPrompt.userChoice;
    console.log('[PWA] 用户选择:', outcome); // 'accepted' 或 'dismissed'

    this._deferredPrompt = null;
    this._hideInstallButton();
  }

  _showInstallButton() {
    if (!this._installBtn) {
      this._installBtn = document.createElement('button');
      this._installBtn.textContent = '📱 安装到手机';
      this._installBtn.className = 'install-btn';
      this._installBtn.addEventListener('click', () => this.triggerInstall());
      document.getElementById('toolbar').appendChild(this._installBtn);
    }
    this._installBtn.style.display = 'block';
  }

  _hideInstallButton() {
    if (this._installBtn) {
      this._installBtn.style.display = 'none';
    }
  }
}

export const pwaInstallManager = new PWAInstallManager();
