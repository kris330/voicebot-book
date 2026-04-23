
import { MicCaptureWithVAD } from './mic-capture-with-vad.js';
import { EnergyMeter } from './debug-energy-meter.js';

let mic = null;
const energyMeter = new EnergyMeter('energy-meter');

// 累积当前这句话的音频帧
let currentSpeechFrames = [];

async function startListening() {
  mic = new MicCaptureWithVAD({
    // VAD 参数
    energyThreshold: -35,    // 高于 -35 dBFS 算有声
    speechStartFrames: 3,    // 连续 3 帧（60ms）有声才开始
    speechEndFrames: 20,     // 连续 20 帧（400ms）静音才结束

    // 说话开始：清空缓存，准备接收
    onSpeechStart() {
      currentSpeechFrames = [];
      document.getElementById('status').textContent = '正在听...';
    },

    // 收到音频帧：存起来
    onAudioFrame(frame) {
      // 深拷贝一份（原始 ArrayBuffer 可能被转移）
      currentSpeechFrames.push(new Int16Array(frame));
    },

    // 说话结束：可以发给服务端了
    onSpeechEnd(durationMs) {
      document.getElementById('status').textContent = `处理中... (${durationMs}ms)`;

      // 把所有帧合并成一个大的 Int16Array
      const totalSamples = currentSpeechFrames.reduce(
        (sum, f) => sum + f.length, 0
      );
      const combined = new Int16Array(totalSamples);
      let offset = 0;
      for (const frame of currentSpeechFrames) {
        combined.set(frame, offset);
        offset += frame.length;
      }

      console.log(
        `说话结束，共 ${combined.length} 个采样，` +
        `时长 ${(combined.length / 16000).toFixed(2)} 秒`
      );

      // TODO 第 6 章：通过 WebSocket 发送 combined 给服务端
      currentSpeechFrames = [];
    },

    // 每帧都触发，用于更新能量计
    onAllFrame({ vadResult }) {
      energyMeter.update(vadResult.energyDB);
    },

    onError(err) {
      alert(err.message);
    },
  });

  await mic.start();
}

document.getElementById('start-btn').addEventListener('click', startListening);
document.getElementById('stop-btn').addEventListener('click', () => mic?.stop());
