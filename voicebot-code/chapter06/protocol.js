// 协议常量定义

// 文本消息类型
export const MessageType = Object.freeze({
  // 会话控制
  SESSION_START: 'session_start',
  SESSION_INFO:  'session_info',

  // VAD 事件
  VAD_START: 'vad_start',
  VAD_END:   'vad_end',

  // ASR 结果
  ASR_RESULT: 'asr_result',

  // LLM 输出
  LLM_TEXT: 'llm_text',

  // TTS 控制
  TTS_START:  'tts_start',
  TTS_END:    'tts_end',
  TTS_PLAYED: 'tts_played',

  // 心跳
  PING: 'ping',
  PONG: 'pong',

  // 错误
  ERROR: 'error',
});

// 二进制帧类型（第一个字节）
export const BinaryType = Object.freeze({
  AUDIO_FRAME: 0x01,   // 客→服：麦克风 PCM 数据
  TTS_CHUNK:   0x02,   // 服→客：TTS PCM 数据
});

/**
 * 构造一个 JSON 控制消息
 * @param {string} type     MessageType 中的一个
 * @param {object} data     消息体
 * @param {number} [seq]    序列号（可选）
 */
export function makeTextMessage(type, data = {}, seq = undefined) {
  const msg = { type, ...data };
  if (seq !== undefined) msg.seq = seq;
  return JSON.stringify(msg);
}

/**
 * 把 Int16Array 音频帧打包成二进制消息
 * 格式：[0x01][Int16 采样数据...]
 *
 * @param {Int16Array} frame
 * @returns {ArrayBuffer}
 */
export function packAudioFrame(frame) {
  // 总长度 = 1字节类型 + N字节音频数据
  const buffer = new ArrayBuffer(1 + frame.byteLength);
  const view = new DataView(buffer);

  // 第一个字节：类型标识
  view.setUint8(0, BinaryType.AUDIO_FRAME);

  // 后续字节：音频数据（直接内存拷贝）
  new Int16Array(buffer, 1).set(frame);

  return buffer;
}

/**
 * 解析服务端发来的二进制消息
 * @param {ArrayBuffer} buffer
 * @returns {{ type: number, data: Int16Array }}
 */
export function unpackBinaryMessage(buffer) {
  const view = new DataView(buffer);
  const type = view.getUint8(0);

  // 音频数据从第 1 个字节开始，以 Int16 方式解释
  // 注意：offset 必须是 2 的倍数（Int16Array 的对齐要求）
  // 如果原始 buffer 的偏移不对齐，需要先 slice
  const audioBuffer = buffer.slice(1);
  const data = new Int16Array(audioBuffer);

  return { type, data };
}
