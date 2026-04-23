# 第八章：移动端适配与 PWA

## 开篇场景

你花了两周时间把 VoiceBot 做得在 Chrome 桌面端运行得很顺滑——语音输入、流式回复、实时播放，一气呵成。然后你把链接发给朋友，朋友用 iPhone 打开，发来一张截图：页面空白，控制台一堆红色报错。

你拿起自己的 Android 手机试了试，好像能用，但声音怪怪的，有时候说话没反应，有时候 AI 还没说完话就自动停了。

移动端是 VoiceAI 的重灾区。这一章我们逐一拆解这些坑，并且教你把 VoiceBot 打包成 PWA（渐进式网页应用），让用户可以安装到主屏幕，像原生 App 一样使用。

---

## 8.1 iOS Safari：麦克风的"用户手势"限制

### 为什么 iOS 这么特别？

苹果对移动端浏览器的限制非常严格，核心原则是：**任何需要用户权限的操作，必须在用户的直接手势（tap/click）事件处理函数内发起**。

这意味着你不能这样写：

```javascript
// 错误示例：页面加载后自动请求麦克风
window.addEventListener('load', async () => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  // iOS 上这会直接失败，或者弹出权限框后立即被拒绝
});
```

正确的做法是把麦克风请求放在按钮的点击回调里：

```javascript
// 正确示例
document.getElementById('start-btn').addEventListener('click', async () => {
  // 用户点击了按钮，这是一个合法的"用户手势"
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  startRecording(stream);
});
```

### iOS 上 getUserMedia 的兼容写法

iOS 12 以前根本没有 `getUserMedia`，iOS 13+ 才加入，但写法和标准有些差异。下面是一个健壮的兼容写法：

```javascript
// src/audio/microphone.js

/**
 * 跨平台获取麦克风输入流
 * 兼容 iOS Safari、Android Chrome、桌面浏览器
 */
async function getMicrophoneStream() {
  // 标准化 getUserMedia API
  const getUserMedia =
    navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices) ||
    navigator.getUserMedia?.bind(navigator) ||
    navigator.webkitGetUserMedia?.bind(navigator) ||
    navigator.mozGetUserMedia?.bind(navigator);

  if (!getUserMedia) {
    throw new Error('此浏览器不支持麦克风访问，请使用 Chrome 或 Safari 最新版');
  }

  const constraints = {
    audio: {
      // 回声消除（通话场景必须开启）
      echoCancellation: true,
      // 噪声抑制
      noiseSuppression: true,
      // 自动增益（手机离嘴远近不同时有用）
      autoGainControl: true,
      // iOS Safari 要求：不要指定采样率，让浏览器自动选择
      // sampleRate: 16000,  // 注释掉这行！iOS 会报错
    },
  };

  // 旧版 API 使用 callback 风格，包装成 Promise
  if (navigator.mediaDevices?.getUserMedia) {
    return navigator.mediaDevices.getUserMedia(constraints);
  } else {
    return new Promise((resolve, reject) => {
      getUserMedia(constraints, resolve, reject);
    });
  }
}

export { getMicrophoneStream };
```

### 为什么不能在 iOS 上指定采样率？

iOS Safari 对 `getUserMedia` 的 `sampleRate` 约束支持不完整。如果你指定了 `sampleRate: 16000`，iOS 不会报错，但实际采样率可能还是 44100Hz，更糟的情况是直接返回空流。

正确做法：让 iOS 用它默认的采样率采集，然后在 `AudioContext` 里做重采样。

---

## 8.2 AudioContext 的激活问题（iOS 特殊行为）

### 问题描述

即使你成功拿到了麦克风流，在 iOS 上还有另一个坑：`AudioContext` 创建后处于 `suspended` 状态，必须在用户手势里调用 `resume()` 才能真正工作。

```javascript
// 这段代码在 iOS 上不发声，也不报错！
const ctx = new AudioContext();
console.log(ctx.state); // 输出 "suspended" ← 问题在这里

const source = ctx.createBufferSource();
source.connect(ctx.destination);
source.start(); // 静默，没有任何声音
```

### 正确的激活流程

```javascript
// src/audio/audio-context-manager.js

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
```

把这个管理器整合到开始录音的按钮里：

```javascript
// src/app.js

import { audioContextManager } from './audio/audio-context-manager.js';
import { getMicrophoneStream } from './audio/microphone.js';

document.getElementById('start-btn').addEventListener('click', async () => {
  try {
    // 第一步：在用户手势里激活 AudioContext（iOS 关键步骤）
    const ctx = await audioContextManager.ensureResumed();

    // 第二步：请求麦克风权限（同样需要在用户手势里）
    const stream = await getMicrophoneStream();

    // 第三步：开始处理音频
    await startVoiceSession(ctx, stream);

    // 更新 UI
    document.getElementById('start-btn').textContent = '停止';
    document.getElementById('status').textContent = '正在聆听...';
  } catch (err) {
    handleMicrophoneError(err);
  }
});

function handleMicrophoneError(err) {
  let message = '无法访问麦克风';

  if (err.name === 'NotAllowedError') {
    message = '麦克风权限被拒绝，请在浏览器设置中允许访问';
  } else if (err.name === 'NotFoundError') {
    message = '未找到麦克风设备';
  } else if (err.name === 'NotSupportedError') {
    message = '此浏览器不支持麦克风访问';
  }

  document.getElementById('status').textContent = message;
  console.error('[麦克风错误]', err.name, err.message);
}
```

---

## 8.3 Android Chrome：性能才是真正的坑

Android Chrome 对 Web Audio API 的支持比 iOS Safari 好得多，基本没有 API 层面的坑。但低端 Android 机（1-2GB RAM，八核但频率很低）有明显的性能问题：

- **ScriptProcessor 回调卡顿**：低端机上 JavaScript 主线程负载过高，音频回调延迟超过 100ms，导致录音断帧
- **AudioWorklet 是解药**：把音频处理移到独立线程，即使主线程忙碌也不影响音频
- **WebSocket 压力**：频繁发送音频数据包（每 100ms 一次）可能把低端机压垮

### 用 AudioWorklet 代替 ScriptProcessorNode

```javascript
// src/audio/worklet/audio-processor.worklet.js
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
```

```javascript
// src/audio/recorder.js

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
```

---

## 8.4 把 VoiceBot 变成 PWA

PWA（Progressive Web App）可以让用户把网页"安装"到手机主屏幕，启动时没有浏览器地址栏，体验接近原生 App。对 VoiceBot 来说，PWA 还有一个重要好处：**Service Worker 可以缓存静态资源，让应用在网络较差时依然能快速加载**。

### PWA 三要素

```
浏览器检查 PWA 安装条件：
┌─────────────────────────────────────────┐
│  1. HTTPS（或 localhost 开发时）         │
│  2. manifest.json（应用元信息）          │
│  3. Service Worker（已注册且激活）       │
└─────────────────────────────────────────┘
        ↓ 三个条件都满足
   浏览器显示"添加到主屏幕"提示
```

### 8.4.1 manifest.json

在项目根目录创建 `public/manifest.json`：

```json
{
  "name": "VoiceBot - AI 语音助手",
  "short_name": "VoiceBot",
  "description": "随时随地与 AI 对话的语音助手",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a2e",
  "theme_color": "#6c63ff",
  "orientation": "portrait-primary",
  "lang": "zh-CN",
  "icons": [
    {
      "src": "/icons/icon-72x72.png",
      "sizes": "72x72",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/icons/icon-96x96.png",
      "sizes": "96x96",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/icons/icon-128x128.png",
      "sizes": "128x128",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/icons/icon-144x144.png",
      "sizes": "144x144",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/icons/icon-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/icons/icon-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable any"
    }
  ],
  "screenshots": [
    {
      "src": "/screenshots/home.png",
      "sizes": "1080x1920",
      "type": "image/png",
      "form_factor": "narrow",
      "label": "VoiceBot 主界面"
    }
  ],
  "categories": ["productivity", "utilities"],
  "shortcuts": [
    {
      "name": "开始对话",
      "short_name": "对话",
      "description": "立即开始语音对话",
      "url": "/?action=start",
      "icons": [{ "src": "/icons/shortcut-chat.png", "sizes": "96x96" }]
    }
  ]
}
```

在 HTML 里引用 manifest：

```html
<!-- public/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />

    <!-- PWA manifest -->
    <link rel="manifest" href="/manifest.json" />

    <!-- iOS 特殊标签（Safari 不完全支持 manifest，需要这些） -->
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="VoiceBot" />
    <link rel="apple-touch-icon" href="/icons/icon-192x192.png" />

    <!-- Android Chrome 主题色 -->
    <meta name="theme-color" content="#6c63ff" />

    <title>VoiceBot - AI 语音助手</title>
  </head>
  <body>
    <!-- 应用内容 -->
    <div id="app"></div>
    <script type="module" src="/src/app.js"></script>
  </body>
</html>
```

### 8.4.2 Service Worker 缓存策略

Service Worker 是一个运行在浏览器后台的脚本，可以拦截网络请求并决定是从缓存返回还是从网络获取。

VoiceBot 的资源可以分为两类：
- **静态资源**（HTML、CSS、JS、图标）：可以积极缓存，更新时用新版本替换
- **API 请求**（WebSocket 连接、后端接口）：不缓存，始终走网络

```javascript
// public/service-worker.js

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
```

在应用里注册 Service Worker：

```javascript
// src/sw-register.js

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
```

### 8.4.3 引导用户安装到主屏幕

浏览器会在合适的时机触发 `beforeinstallprompt` 事件，我们可以拦截它，保存起来，等用户深度使用后再展示安装提示。

```javascript
// src/pwa-install.js

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
```

---

## 8.5 移动端音频省电策略

手机在后台或息屏时，浏览器会限制 JavaScript 执行，AudioContext 也可能被暂停。我们需要监听页面可见性变化，主动管理音频资源。

```javascript
// src/audio/visibility-manager.js

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
```

在 HTML 里加上这个提示：

```html
<!-- 后台返回时的恢复提示 -->
<div id="tap-to-resume" style="display:none; position:fixed; inset:0;
  background:rgba(0,0,0,0.7); color:white; font-size:18px;
  display:none; align-items:center; justify-content:center; z-index:1000;">
  <div style="text-align:center; padding:20px;">
    <div style="font-size:48px; margin-bottom:16px;">🎙️</div>
    <div>点击任意位置继续对话</div>
  </div>
</div>
```

---

## 8.6 完整的移动端入口文件

把上面所有模块组合起来：

```javascript
// src/app.js - 移动端优化版

import { audioContextManager } from './audio/audio-context-manager.js';
import { getMicrophoneStream } from './audio/microphone.js';
import { AudioRecorder } from './audio/recorder.js';
import { visibilityManager } from './audio/visibility-manager.js';
import { pwaInstallManager } from './pwa-install.js';
import { registerServiceWorker } from './sw-register.js';
import { VoiceBotClient } from './voicebot-client.js';

// ==================== 初始化 ====================

let recorder = null;
let botClient = null;
let isSessionActive = false;

async function init() {
  // 注册 Service Worker
  await registerServiceWorker();

  // 初始化 PWA 安装管理
  pwaInstallManager.init();

  // 初始化可见性管理（后台省电）
  visibilityManager.init({
    onPause: () => {
      if (isSessionActive) {
        updateStatus('应用在后台，对话已暂停');
      }
    },
    onResume: () => {
      if (isSessionActive) {
        updateStatus('对话已恢复');
      }
    },
  });

  // 绑定按钮事件
  document.getElementById('start-btn').addEventListener('click', handleStartClick);
  document.getElementById('stop-btn').addEventListener('click', handleStopClick);

  updateStatus('点击下方按钮开始对话');
  console.log('[App] 初始化完成');
}

// ==================== 对话控制 ====================

async function handleStartClick() {
  try {
    updateStatus('正在请求麦克风权限...');

    // 关键：在用户手势里同时激活 AudioContext 和请求麦克风
    const [ctx, stream] = await Promise.all([
      audioContextManager.ensureResumed(),
      getMicrophoneStream(),
    ]);

    // 初始化录音器
    recorder = new AudioRecorder(ctx);
    await recorder.init(); // 加载 AudioWorklet

    // 初始化 WebSocket 客户端
    botClient = new VoiceBotClient({
      wsUrl: `wss://${location.host}/ws/voice`,
      onTranscript: (text) => updateTranscript('你', text),
      onReply: (text) => updateTranscript('AI', text),
      onError: (err) => updateStatus(`错误: ${err}`),
    });
    await botClient.connect();

    // 开始录音，把数据发给服务端
    await recorder.start(stream, (audioData) => {
      botClient.sendAudio(audioData);
    });

    isSessionActive = true;
    setUIState('recording');
    updateStatus('正在聆听...');
  } catch (err) {
    handleError(err);
  }
}

async function handleStopClick() {
  if (recorder) {
    recorder.stop();
    recorder = null;
  }
  if (botClient) {
    botClient.disconnect();
    botClient = null;
  }
  isSessionActive = false;
  setUIState('idle');
  updateStatus('对话已结束');
}

// ==================== UI 更新 ====================

function setUIState(state) {
  const startBtn = document.getElementById('start-btn');
  const stopBtn = document.getElementById('stop-btn');

  if (state === 'recording') {
    startBtn.style.display = 'none';
    stopBtn.style.display = 'block';
  } else {
    startBtn.style.display = 'block';
    stopBtn.style.display = 'none';
  }
}

function updateStatus(message) {
  document.getElementById('status').textContent = message;
}

function updateTranscript(speaker, text) {
  const container = document.getElementById('transcript');
  const entry = document.createElement('div');
  entry.className = `transcript-entry transcript-${speaker === '你' ? 'user' : 'ai'}`;
  entry.textContent = `${speaker}：${text}`;
  container.appendChild(entry);
  container.scrollTop = container.scrollHeight;
}

function handleError(err) {
  console.error('[App] 错误:', err);
  let message = '发生未知错误';

  if (err.name === 'NotAllowedError') {
    message = '麦克风被拒绝，请在浏览器设置中开启权限';
  } else if (err.name === 'NotFoundError') {
    message = '未找到麦克风设备';
  } else if (err.message?.includes('AudioWorklet')) {
    message = '音频处理初始化失败，请刷新重试';
  }

  updateStatus(message);
  setUIState('idle');
}

// 页面加载后初始化（不涉及音频，不需要用户手势）
document.addEventListener('DOMContentLoaded', init);
```

---

## 8.7 移动端测试清单

在发布前，对照这个清单逐项测试：

### iOS Safari 测试

```
iOS Safari 兼容性检查：

  基础功能
  ├── [ ] 页面加载正常，无 JS 错误
  ├── [ ] 点击按钮后弹出麦克风权限请求
  ├── [ ] 授权后可以正常录音
  ├── [ ] 拒绝权限后显示友好错误提示
  └── [ ] 音频播放正常（AI 回复可以听到）

  AudioContext 行为
  ├── [ ] 首次点击按钮后 AudioContext.state === 'running'
  ├── [ ] 页面进后台再回来，音频恢复正常
  └── [ ] 锁屏后解锁，提示用户点击恢复

  PWA
  ├── [ ] Safari 地址栏显示"分享"按钮可以"添加到主屏幕"
  ├── [ ] 从主屏幕启动，全屏显示（无地址栏）
  └── [ ] 应用图标显示正确
```

### Android Chrome 测试

```
Android Chrome 兼容性检查：

  基础功能
  ├── [ ] 高端机（骁龙 8xx）流畅运行
  ├── [ ] 低端机（骁龙 4xx 或联发科低端）可以运行
  ├── [ ] 录音无明显延迟或断帧
  └── [ ] 长时间对话（10分钟+）无内存泄漏

  PWA
  ├── [ ] 地址栏显示安装提示（首次访问后）
  ├── [ ] 安装后从主屏幕启动正常
  ├── [ ] Service Worker 缓存生效（断网后能加载页面）
  └── [ ] 更新后显示"有新版本"提示

  后台行为
  ├── [ ] 切换到其他 App 再回来，对话可以恢复
  ├── [ ] 后台不持续消耗电量（AudioContext 已暂停）
  └── [ ] 通话打断后能正常处理
```

### 用 Chrome DevTools 模拟移动端

```bash
# 在 Chrome 打开 DevTools → 切换设备模拟模式
# 重点检查：
# 1. Application → Service Workers：SW 是否注册成功
# 2. Application → Cache → Cache Storage：缓存内容是否正确
# 3. Application → Manifest：manifest.json 是否解析正确
# 4. Lighthouse → PWA 评分：应达到 90+ 分
```

---

## 本章小结

本章覆盖了 VoiceBot 移动端适配的核心难点：

- **iOS 麦克风限制**：`getUserMedia` 和 `AudioContext.resume()` 必须在用户手势回调里调用，不能在页面加载时自动触发
- **AudioContext 激活**：iOS 上新建的 AudioContext 默认是 `suspended` 状态，必须显式调用 `resume()`
- **跨平台 getUserMedia**：不要在 iOS 上指定 `sampleRate`，让浏览器自动选择
- **Android 性能**：用 AudioWorklet 替代 ScriptProcessorNode，把音频处理移到独立线程
- **PWA 三要素**：HTTPS + manifest.json + Service Worker，满足这三个条件即可安装
- **Service Worker 缓存**：静态资源用 Cache First 策略，API 请求不缓存
- **省电策略**：监听 `visibilitychange` 事件，后台时暂停 AudioContext

下一章，我们把视线从客户端移到服务端。客户端的 VAD 可能会漏检或误触发，服务端需要一套自己的 VAD 机制来兜底——这就是**服务端 VAD**的价值所在。
