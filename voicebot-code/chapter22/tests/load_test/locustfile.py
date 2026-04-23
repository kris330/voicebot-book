
import json
import time
import wave
import struct
import threading
import logging
from locust import User, task, between, events
import websocket

logger = logging.getLogger(__name__)


def generate_test_audio(duration_ms: int = 2000, sample_rate: int = 16000) -> bytes:
    """生成测试用的静音 PCM 音频（实际测试应使用真实语音）"""
    num_samples = int(sample_rate * duration_ms / 1000)
    # 生成低幅度噪声（完全静音可能被 VAD 过滤）
    import random
    samples = [int(random.gauss(0, 100)) for _ in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


class VoiceBotUser(User):
    """模拟一个 VoiceBot 用户的行为"""

    wait_time = between(3, 8)  # 每次对话间隔 3-8 秒

    def on_start(self) -> None:
        """用户开始时建立 WebSocket 连接"""
        self.ws = None
        self._connect()

    def on_stop(self) -> None:
        """用户停止时关闭连接"""
        if self.ws:
            self.ws.close()

    def _connect(self) -> None:
        target_url = self.host.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{target_url}/ws"

        self.ws = websocket.WebSocket()
        try:
            self.ws.connect(ws_url, timeout=10)
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self.ws = None

    @task
    def voice_conversation(self) -> None:
        """模拟一次完整的语音对话"""
        if not self.ws:
            self._connect()
            if not self.ws:
                return

        start_time = time.time()

        try:
            # 发送音频数据（模拟用户说话）
            audio_data = generate_test_audio(duration_ms=2000)
            chunk_size = 3200  # 100ms @ 16kHz 16bit

            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                self.ws.send_binary(chunk)
                time.sleep(0.1)  # 模拟实时发送

            # 发送说话结束信号
            self.ws.send(json.dumps({"type": "speech_end"}))

            # 等待第一个音频响应（TTFS）
            first_audio_time = None
            while True:
                try:
                    self.ws.settimeout(10)
                    msg = self.ws.recv()

                    if isinstance(msg, bytes):
                        # 收到音频数据
                        if first_audio_time is None:
                            first_audio_time = time.time()
                            ttfs_ms = (first_audio_time - start_time) * 1000
                            # 上报自定义指标
                            events.request.fire(
                                request_type="WS",
                                name="TTFS (Time to First Sound)",
                                response_time=ttfs_ms,
                                response_length=len(msg),
                                exception=None,
                                context={},
                            )
                    elif isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("type") == "audio_end":
                            break

                except websocket.WebSocketTimeoutException:
                    logger.warning("Timeout waiting for response")
                    break

        except Exception as e:
            events.request.fire(
                request_type="WS",
                name="voice_conversation",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
                exception=e,
                context={},
            )
            # 连接可能已断开，重置
            self.ws = None
