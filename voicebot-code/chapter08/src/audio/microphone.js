
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
