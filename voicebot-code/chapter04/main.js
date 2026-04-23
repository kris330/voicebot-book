
import { MicCapture } from './mic-capture.js';

// 状态变量
let micCapture = null;
let isRecording = false;

// 页面上的按钮
const startBtn = document.getElementById('start-btn');
const stopBtn = document.getElementById('stop-btn');
const statusEl = document.getElementById('status');
const errorEl = document.getElementById('error');

/**
 * 收到一帧 16kHz PCM 音频数据
 * @param {Int16Array} frame  320 个 Int16 采样，代表 20ms 的音频
 */
function handleAudioFrame(frame) {
  // 现在拿到的是 16kHz、16-bit、单声道的 PCM 数据
  // 后续章节会把这里改成：发给 VAD 做端点检测，再发给 WebSocket
  console.log(`收到音频帧：${frame.length} 个采样，${frame.byteLength} 字节`);
}

/**
 * 处理错误
 */
function handleError(err) {
  console.error('麦克风错误：', err);
  errorEl.textContent = err.message;
  errorEl.style.display = 'block';

  // 恢复按钮状态
  startBtn.disabled = false;
  stopBtn.disabled = true;
  isRecording = false;
}

/**
 * 开始录音
 */
async function startRecording() {
  if (isRecording) return;

  // 清除之前的错误
  errorEl.style.display = 'none';
  startBtn.disabled = true;
  statusEl.textContent = '正在打开麦克风...';

  // 创建麦克风采集器
  micCapture = new MicCapture({
    onAudioFrame: handleAudioFrame,
    onError: handleError,
  });

  try {
    await micCapture.start();

    isRecording = true;
    stopBtn.disabled = false;
    statusEl.textContent = '正在录音...';
  } catch (err) {
    // handleError 已经在 onError 回调里处理了
    // 这里只需要恢复按钮
    startBtn.disabled = false;
    statusEl.textContent = '录音失败';
  }
}

/**
 * 停止录音
 */
async function stopRecording() {
  if (!isRecording || !micCapture) return;

  await micCapture.stop();
  micCapture = null;
  isRecording = false;

  startBtn.disabled = false;
  stopBtn.disabled = true;
  statusEl.textContent = '已停止';
}

// 绑定按钮事件
startBtn.addEventListener('click', startRecording);
stopBtn.addEventListener('click', stopRecording);

// 初始状态
stopBtn.disabled = true;
