
const CACHE_VERSION = 'voicebot-v1.2.0'; // 每次发版时更新这个版本号
const STATIC_CACHE = `${CACHE_VERSION}-static`;

// 预缓存列表：应用外壳（App Shell）
const APP_SHELL = [
  '/',
  '/index.html',
  '/src/app.js',
  '/src/audio/audio-context-manager.js',
  '/src/audio/microphone.js',
  '/src/audio/recorder.js',
  '/src/audio/worklet/audio-processor.worklet.js',
  '/src/styles/main.css',
  '/icons/icon-192x192.png',
  '/icons/icon-512x512.png',
  '/manifest.json',
];

// ==================== 安装阶段 ====================
// Service Worker 首次注册或版本更新时触发
self.addEventListener('install', (event) => {
  console.log('[SW] 安装中，版本:', CACHE_VERSION);

  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => {
        console.log('[SW] 预缓存 App Shell');
        return cache.addAll(APP_SHELL);
      })
      .then(() => {
        // 跳过等待，立即激活新版本
        return self.skipWaiting();
      })
  );
});

// ==================== 激活阶段 ====================
// 新版本 SW 激活时，清理旧缓存
self.addEventListener('activate', (event) => {
  console.log('[SW] 激活，清理旧缓存');

  event.waitUntil(
    caches
      .keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name.startsWith('voicebot-') && name !== STATIC_CACHE)
            .map((name) => {
              console.log('[SW] 删除旧缓存:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => {
        // 让新 SW 立即接管所有页面
        return self.clients.claim();
      })
  );
});

// ==================== 请求拦截 ====================
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // WebSocket 请求不拦截（SW 无法处理 WebSocket）
  if (url.protocol === 'ws:' || url.protocol === 'wss:') {
    return;
  }

  // API 请求不缓存，直接走网络
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // 静态资源：Cache First 策略
  // 先查缓存，命中直接返回；未命中再请求网络并更新缓存
  event.respondWith(cacheFirst(event.request));
});

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);

  if (cached) {
    // 后台异步更新缓存（Stale-While-Revalidate 变体）
    updateCacheInBackground(cache, request);
    return cached;
  }

  // 缓存未命中，从网络获取
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone()); // 存入缓存
    }
    return response;
  } catch (err) {
    // 网络也失败了，返回离线页面（如果有的话）
    console.warn('[SW] 网络请求失败:', request.url);
    return new Response('离线模式：请检查网络连接', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
}

function updateCacheInBackground(cache, request) {
  fetch(request)
    .then((response) => {
      if (response.ok) {
        cache.put(request, response);
      }
    })
    .catch(() => {
      // 后台更新失败，静默忽略
    });
}
