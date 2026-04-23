
import { AudioSender } from './audio-sender.js';

const WS_URL = `ws://${location.host}/ws/voice`;

// UI 元素
const startBtn  = document.getElementById('start-btn');
const stopBtn   = document.getElementById('stop-btn');
const statusEl  = document.getElementById('status');
const transcript = document.getElementById('transcript');

let sender = null;

async function start() {
  startBtn.disabled = true;
  statusEl.textContent = '正在连接...';

  sender = new AudioSender({
    wsUrl: WS_URL,

    onStateChange(state) {
      statusEl.textContent = {
        'connected':    '已连接，可以说话',
        'disconnected': '连接断开',
        'reconnecting (1)': '重连中 (1/5)...',
        'reconnecting (2)': '重连中 (2/5)...',
      }[state] || state;
    },

    onASRResult({ text, isFinal }) {
      if (isFinal) {
        // 最终识别结果，追加到对话记录
        const p = document.createElement('p');
        p.textContent = `用户：${text}`;
        transcript.appendChild(p);
      } else {
        // 中间结果，实时显示
        statusEl.textContent = `识别中：${text}`;
      }
    },
  });

  try {
    await sender.start();
    stopBtn.disabled = false;
  } catch (err) {
    alert(err.message);
    startBtn.disabled = false;
    sender = null;
  }
}

async function stop() {
  if (!sender) return;
  await sender.stop();
  sender = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  statusEl.textContent = '已停止';
}

startBtn.addEventListener('click', start);
stopBtn.addEventListener('click', stop);
