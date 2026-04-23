
export async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) {
    console.warn('[SW] 此浏览器不支持 Service Worker');
    return;
  }

  try {
    const registration = await navigator.serviceWorker.register('/service-worker.js', {
      scope: '/',
    });

    console.log('[SW] 注册成功，scope:', registration.scope);

    // 监听 SW 更新
    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing;
      newWorker.addEventListener('statechange', () => {
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          // 有新版本可用，提示用户刷新
          showUpdatePrompt();
        }
      });
    });
  } catch (err) {
    console.error('[SW] 注册失败:', err);
  }
}

function showUpdatePrompt() {
  // 显示一个非侵入式的更新提示
  const banner = document.createElement('div');
  banner.style.cssText = `
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #6c63ff; color: white; padding: 12px 20px; border-radius: 8px;
    font-size: 14px; z-index: 9999; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    display: flex; align-items: center; gap: 12px;
  `;
  banner.innerHTML = `
    <span>VoiceBot 有新版本可用</span>
    <button onclick="location.reload()" style="
      background: white; color: #6c63ff; border: none;
      padding: 4px 12px; border-radius: 4px; cursor: pointer; font-weight: bold;
    ">立即更新</button>
  `;
  document.body.appendChild(banner);
}
