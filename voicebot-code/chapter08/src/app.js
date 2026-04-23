
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
