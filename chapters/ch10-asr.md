# 第十章：ASR 语音识别

## 开篇场景

上一章我们有了服务端 VAD，它能精准地切出每一段语音。现在问题来了：切出来的音频片段，怎么变成文字？

这就是 ASR（Automatic Speech Recognition，自动语音识别）要做的事情。

你可能会想：直接调一个 API 不就好了？对，入门是这样的。但随着 VoiceBot 的用户越来越多，你开始遇到一些新问题：

- 阿里云按调用次数收费，每天 10000 次通话，账单数字挺吓人的
- 某天阿里云 API 抖动，识别延迟突然飙到 3 秒，用户体验一落千丈
- 客户要求数据不出内网，云端 API 不满足合规要求

所以一个成熟的 VoiceBot 应该同时支持**云端 ASR**和**本地 ASR**，通过统一的抽象接口来使用，随时可以切换。

---

## 10.1 流式 ASR 原理

在接入 API 之前，先花几分钟理解流式 ASR 的工作原理，这能帮你更好地理解那些参数和结果格式。

### 为什么需要流式？

非流式 ASR 的流程：

```
录音 → 等待说完 → 把整段音频发给服务 → 等识别结果 → 显示
                                          ↑
                                     这里可能等 1-3 秒
```

流式 ASR 的流程：

```
录音 → 边说边发 → 实时返回中间结果 → 说完后返回最终结果
         ↓              ↓
      正在说...     "今天天气..."（中间结果，不断更新）
                        ↓
                   "今天天气怎么样"（最终结果）
```

流式 ASR 大幅降低了感知延迟，用户说完之后几乎立刻就能看到识别结果。

### CTC 解码：流式的基础

现代 ASR 模型通常基于 CTC（Connectionist Temporal Classification）解码：

```
音频帧序列：[帧1, 帧2, 帧3, 帧4, 帧5, 帧6, 帧7, ...]
                ↓ 声学模型（通常是 Conformer 或 Transformer）
概率矩阵：每帧对应每个字符的概率
                ↓ CTC 解码
中间结果：[今, 今天, 今天天, 今天天气, ...]（随帧数增加不断更新）
                ↓ 语言模型重打分（可选）
最终结果：今天天气怎么样
```

CTC 的特点是可以在不知道完整句子的情况下，逐帧给出当前的最优猜测。这就是流式 ASR 中间结果的来源。

### 中间结果 vs 最终结果

```
时间轴：
  0ms      500ms    1000ms   1500ms   2000ms（说完）
   │         │         │         │         │
   │         ▼         ▼         ▼         ▼
   │    "今天"     "今天天气"  "今天天气怎"  "今天天气怎么样"
   │   (中间结果)  (中间结果)  (中间结果)   (最终结果，固定)

中间结果特点：
  - 可能不准（尤其是句子末尾）
  - 会被后续结果覆盖
  - 用于实时显示，让用户感知到"AI 在听"

最终结果特点：
  - 更准确（利用了完整上下文）
  - 说完后才出现
  - 用于送给 LLM 处理
```

---

## 10.2 统一的 ASR 抽象接口

在接入具体实现之前，先定义接口。这是"接口优先"设计的核心价值——业务代码只依赖抽象，不依赖具体实现。

```python
# src/voicebot/asr/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Optional


@dataclass
class ASRResult:
    """ASR 识别结果"""
    text: str                          # 识别文本
    is_final: bool                     # 是否为最终结果
    confidence: float = 1.0            # 置信度 [0, 1]
    words: list[dict] = None           # 词级时间戳（可选）
    language: str = "zh"               # 识别语言


class BaseASR(ABC):
    """ASR 抽象基类

    所有 ASR 实现都必须继承这个类，实现以下方法。
    """

    @abstractmethod
    async def init(self) -> None:
        """初始化（加载模型、建立连接等）"""
        ...

    @abstractmethod
    async def transcribe(self, audio: bytes | "np.ndarray") -> ASRResult:
        """
        对一段完整音频进行识别（非流式）

        Args:
            audio: int16 音频数据，16kHz 单声道

        Returns:
            ASRResult，is_final=True
        """
        ...

    @abstractmethod
    async def transcribe_stream(
        self, audio_generator: AsyncGenerator
    ) -> AsyncGenerator[ASRResult, None]:
        """
        流式识别（可选实现，不支持的子类可以抛出 NotImplementedError）

        Args:
            audio_generator: 异步生成器，持续产生音频帧

        Yields:
            ASRResult，中间结果 is_final=False，最终结果 is_final=True
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放资源"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """ASR 实现的名称（用于日志）"""
        ...
```

---

## 10.3 云端方案：阿里云实时语音识别

### 申请和准备

1. 登录[阿里云控制台](https://nls-portal.console.aliyun.com/)
2. 创建项目，获得 `AppKey`
3. 在 RAM 控制台创建 AccessKey（建议只授权语音服务权限）
4. 免费额度：每月 3 小时流式识别（新用户更多）

```bash
# 安装阿里云语音 SDK
pip install aliyun-python-sdk-core
pip install nls-python-sdk

# 或者使用较新的 SDK
pip install alibabacloud-nls20180628
```

### 完整实现

```python
# src/voicebot/asr/aliyun_asr.py

import asyncio
import logging
import os
from typing import AsyncGenerator, Optional

import numpy as np

# 阿里云 NLS SDK
import nls

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)


class AliyunASR(BaseASR):
    """
    阿里云实时语音识别（NLS）

    文档：https://help.aliyun.com/document_detail/84435.html
    """

    # 阿里云 NLS 服务地址
    ENDPOINT = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"

    def __init__(
        self,
        app_key: Optional[str] = None,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
    ):
        self._app_key = app_key or os.environ["ALIYUN_NLS_APP_KEY"]
        self._access_key_id = access_key_id or os.environ["ALIYUN_ACCESS_KEY_ID"]
        self._access_key_secret = (
            access_key_secret or os.environ["ALIYUN_ACCESS_KEY_SECRET"]
        )
        self._token = None
        self._token_expiry = 0

    @property
    def name(self) -> str:
        return "AliyunASR"

    async def init(self) -> None:
        """获取访问 Token"""
        await self._refresh_token()
        logger.info(f"[{self.name}] 初始化完成")

    async def _refresh_token(self):
        """获取或刷新 NLS Token（有效期 24 小时）"""
        import time
        from aliyunsdkcore.client import AcsClient
        from aliyunsdknls_cloud_meta.request.v20180518 import CreateTokenRequest

        if self._token and time.time() < self._token_expiry - 300:
            return  # Token 还有 5 分钟以上，不需要刷新

        loop = asyncio.get_event_loop()

        def _get_token():
            client = AcsClient(
                self._access_key_id,
                self._access_key_secret,
                "cn-shanghai"
            )
            request = CreateTokenRequest.CreateTokenRequest()
            response = client.do_action_with_exception(request)
            import json
            data = json.loads(response)
            return data["Token"]["Id"], data["Token"]["ExpireTime"]

        self._token, self._token_expiry = await loop.run_in_executor(None, _get_token)
        logger.debug(f"[{self.name}] Token 刷新成功，有效期至 {self._token_expiry}")

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """
        对一段完整音频进行识别

        内部使用流式 API，但等待最终结果后才返回
        """
        if isinstance(audio, np.ndarray):
            audio_bytes = audio.astype(np.int16).tobytes()
        else:
            audio_bytes = audio

        await self._refresh_token()

        # 使用 asyncio.Event 等待识别完成
        result_event = asyncio.Event()
        final_text = ""
        error_msg = None

        def on_sentence_end(message, *args):
            nonlocal final_text
            import json
            data = json.loads(message)
            if data.get("result"):
                final_text = data["result"]

        def on_recognition_complete(message, *args):
            result_event.set()

        def on_error(message, *args):
            nonlocal error_msg
            error_msg = str(message)
            result_event.set()

        # 创建识别器
        recognizer = nls.NlsSpeechRecognizer(
            url=self.ENDPOINT,
            token=self._token,
            appkey=self._app_key,
            on_sentence_end=on_sentence_end,
            on_recognition_complete=on_recognition_complete,
            on_error=on_error,
        )

        # 在线程池里运行同步的阿里云 SDK
        loop = asyncio.get_event_loop()

        def _run_recognition():
            recognizer.start(
                aformat="pcm",
                sample_rate=16000,
                enable_punctuation_prediction=True,
                enable_inverse_text_normalization=True,
            )

            # 分块发送音频（每次 3200 字节 = 100ms@16kHz）
            chunk_size = 3200
            for i in range(0, len(audio_bytes), chunk_size):
                chunk = audio_bytes[i:i + chunk_size]
                recognizer.send_audio(chunk)

            recognizer.stop()

        await loop.run_in_executor(None, _run_recognition)

        # 等待识别完成（最多 10 秒）
        try:
            await asyncio.wait_for(result_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] 识别超时")
            return ASRResult(text="", is_final=True, confidence=0.0)

        if error_msg:
            logger.error(f"[{self.name}] 识别错误: {error_msg}")
            return ASRResult(text="", is_final=True, confidence=0.0)

        return ASRResult(
            text=final_text.strip(),
            is_final=True,
            confidence=1.0,
        )

    async def transcribe_stream(self, audio_generator: AsyncGenerator):
        raise NotImplementedError("阿里云实时识别流式接口请使用 NlsStreamingRecognizer")

    async def close(self) -> None:
        pass
```

### 测试阿里云 ASR

```python
# scripts/test_aliyun_asr.py

import asyncio
import wave
import numpy as np
import os

from voicebot.asr.aliyun_asr import AliyunASR


async def main():
    # 设置环境变量（实际使用时放在 .env 文件里）
    os.environ["ALIYUN_NLS_APP_KEY"] = "你的 AppKey"
    os.environ["ALIYUN_ACCESS_KEY_ID"] = "你的 AccessKeyId"
    os.environ["ALIYUN_ACCESS_KEY_SECRET"] = "你的 AccessKeySecret"

    # 读取测试音频
    with wave.open("test_audio/sample.wav", "r") as f:
        assert f.getframerate() == 16000, "需要 16kHz 音频"
        assert f.getnchannels() == 1, "需要单声道音频"
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    asr = AliyunASR()
    await asr.init()

    print("开始识别...")
    result = await asr.transcribe(audio)
    print(f"识别结果: {result.text!r}")
    print(f"置信度: {result.confidence}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 10.4 本地方案：SenseVoice（FunASR）

SenseVoice 是阿里巴巴开源的多语言语音理解模型，支持中英日韩等多种语言，还能同时输出情感标签和音频事件（如笑声、掌声）。

更重要的是：**SenseVoice-Small 的推理速度极快**，在 CPU 上处理 10 秒音频只需 ~70ms。

```
SenseVoice 与其他本地 ASR 模型对比：
┌────────────────┬──────────────┬──────────────┬──────────────┐
│ 模型           │ 模型大小     │ CPU推理(10s) │ 中文准确率   │
├────────────────┼──────────────┼──────────────┼──────────────┤
│ Whisper-large  │ 1.5 GB       │ ~3000ms      │ 很好         │
│ Whisper-small  │ 244 MB       │ ~500ms       │ 较好         │
│ SenseVoice-S   │ 234 MB       │ ~70ms        │ 很好         │
│ SenseVoice-M   │ ~800 MB      │ ~200ms       │ 极好         │
└────────────────┴──────────────┴──────────────┴──────────────┘
```

### 安装

```bash
pip install funasr
pip install torch torchaudio  # CPU 版本
# 或者 GPU 版本：
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 完整实现

```python
# src/voicebot/asr/sensevoice_asr.py

import asyncio
import io
import logging
import time
import wave
from typing import AsyncGenerator, Optional

import numpy as np

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)

# SenseVoice 情感标签映射
EMOTION_MAP = {
    "NEUTRAL": "中性",
    "HAPPY": "开心",
    "SAD": "悲伤",
    "ANGRY": "愤怒",
    "FEARFUL": "恐惧",
    "DISGUSTED": "厌恶",
    "SURPRISED": "惊讶",
}


class SenseVoiceASR(BaseASR):
    """
    SenseVoice 本地语音识别

    基于 FunASR 框架，支持中英日韩等多语言
    模型下载地址：https://www.modelscope.cn/models/iic/SenseVoiceSmall
    """

    def __init__(
        self,
        model_name: str = "iic/SenseVoiceSmall",
        device: str = "cpu",
        batch_size: int = 1,
    ):
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model = None

    @property
    def name(self) -> str:
        return f"SenseVoice({self._model_name})"

    async def init(self) -> None:
        """在线程池中加载模型（避免阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info(f"[{self.name}] 模型加载完成，设备={self._device}")

    def _load_model(self):
        from funasr import AutoModel

        self._model = AutoModel(
            model=self._model_name,
            device=self._device,
            disable_update=True,   # 不自动更新模型（生产环境推荐）
            disable_log=True,
        )
        logger.info(f"[{self.name}] FunASR 模型加载完成")

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """把 numpy 数组转换为 WAV 格式字节流（FunASR 接受 WAV 输入）"""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)       # int16 = 2 bytes
            f.setframerate(16000)
            f.writeframes(audio.astype(np.int16).tobytes())
        return buffer.getvalue()

    def _run_inference(self, audio: np.ndarray) -> dict:
        """运行模型推理（同步，在线程池中调用）"""
        start_time = time.monotonic()

        # SenseVoice 接受 numpy float32 数组
        if audio.dtype == np.int16:
            audio_float = audio.astype(np.float32) / 32768.0
        else:
            audio_float = audio.astype(np.float32)

        result = self._model.generate(
            input=audio_float,
            cache={},
            language="auto",                      # 自动检测语言
            use_itn=True,                          # 逆文本归一化（数字写法）
            batch_size_s=60,                       # 批处理时长（秒）
        )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.debug(f"[{self.name}] 推理耗时: {elapsed_ms:.1f}ms")

        return result[0] if result else {}

    def _parse_result(self, raw: dict) -> ASRResult:
        """解析 FunASR 的原始输出"""
        text = raw.get("text", "").strip()

        # SenseVoice 输出格式示例：
        # "<|zh|><|NEUTRAL|><|Speech|><|woitn|>今天天气怎么样"
        # 需要去掉特殊标签

        import re
        # 提取情感标签
        emotion_match = re.search(r"<\|([A-Z]+)\|>", text)
        emotion = emotion_match.group(1) if emotion_match else "NEUTRAL"

        # 去掉所有 <|...|> 标签
        clean_text = re.sub(r"<\|[^|]+\|>", "", text).strip()

        return ASRResult(
            text=clean_text,
            is_final=True,
            confidence=1.0,
        )

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """识别一段完整音频"""
        if isinstance(audio, bytes):
            audio_array = np.frombuffer(audio, dtype=np.int16)
        else:
            audio_array = audio

        if len(audio_array) == 0:
            return ASRResult(text="", is_final=True)

        loop = asyncio.get_event_loop()
        raw_result = await loop.run_in_executor(None, self._run_inference, audio_array)

        return self._parse_result(raw_result)

    async def transcribe_stream(self, audio_generator: AsyncGenerator):
        """
        SenseVoice 不原生支持流式，这里用非流式模拟：
        等待 VAD 切出完整语音段后，统一送入识别
        """
        raise NotImplementedError(
            "SenseVoice 不支持流式识别，请使用 transcribe() 处理 VAD 切出的语音段"
        )

    async def close(self) -> None:
        self._model = None
        logger.info(f"[{self.name}] 已释放资源")
```

### 测试本地 ASR

```python
# scripts/test_sensevoice.py

import asyncio
import wave
import numpy as np
import time

from voicebot.asr.sensevoice_asr import SenseVoiceASR


async def benchmark():
    """测试 SenseVoice 的识别准确率和速度"""

    asr = SenseVoiceASR(model_name="iic/SenseVoiceSmall", device="cpu")

    print("加载模型中...")
    load_start = time.monotonic()
    await asr.init()
    print(f"模型加载耗时: {(time.monotonic() - load_start) * 1000:.0f}ms")

    # 测试音频列表
    test_cases = [
        ("test_audio/short_sentence.wav", "今天天气怎么样"),
        ("test_audio/long_sentence.wav", "帮我查一下明天北京到上海的高铁票"),
        ("test_audio/numbers.wav", "我需要预订3间房间，价格在500元以内"),
    ]

    for audio_file, expected in test_cases:
        with wave.open(audio_file, "r") as f:
            audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
            duration_ms = len(audio) / 16000 * 1000

        infer_start = time.monotonic()
        result = await asr.transcribe(audio)
        infer_ms = (time.monotonic() - infer_start) * 1000

        rtf = infer_ms / duration_ms  # 实时率（< 1 才能用于实时场景）

        print(f"\n音频时长: {duration_ms:.0f}ms")
        print(f"推理耗时: {infer_ms:.0f}ms (RTF={rtf:.3f})")
        print(f"预期: {expected!r}")
        print(f"识别: {result.text!r}")

    await asr.close()


if __name__ == "__main__":
    asyncio.run(benchmark())
```

---

## 10.5 ASRManager：统一管理多个 ASR 实例

```python
# src/voicebot/asr/asr_manager.py

import asyncio
import logging
import os
from typing import Optional

import numpy as np

from voicebot.asr.base import BaseASR, ASRResult

logger = logging.getLogger(__name__)


class ASRManager:
    """
    ASR 管理器：
    - 支持主/备双 ASR（主用云端，备用本地；或主用本地，备用云端）
    - 自动降级：主 ASR 失败时切换到备用 ASR
    - 统计识别成功率和延迟
    """

    def __init__(
        self,
        primary: Optional[BaseASR] = None,
        fallback: Optional[BaseASR] = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._stats = {
            "primary_success": 0,
            "primary_failure": 0,
            "fallback_used": 0,
        }

    @classmethod
    def from_env(cls) -> "ASRManager":
        """
        根据环境变量自动选择 ASR 配置

        环境变量：
          ASR_MODE=local | cloud | hybrid（默认 hybrid）
          ALIYUN_NLS_APP_KEY=...（cloud 模式需要）
        """
        mode = os.environ.get("ASR_MODE", "hybrid")

        if mode == "local":
            from voicebot.asr.sensevoice_asr import SenseVoiceASR
            return cls(primary=SenseVoiceASR())

        elif mode == "cloud":
            from voicebot.asr.aliyun_asr import AliyunASR
            return cls(primary=AliyunASR())

        else:  # hybrid：云端优先，本地兜底
            from voicebot.asr.aliyun_asr import AliyunASR
            from voicebot.asr.sensevoice_asr import SenseVoiceASR
            return cls(
                primary=AliyunASR(),
                fallback=SenseVoiceASR(),
            )

    async def init(self) -> None:
        """并发初始化所有 ASR 实例"""
        tasks = []
        if self._primary:
            tasks.append(self._primary.init())
        if self._fallback:
            tasks.append(self._fallback.init())

        await asyncio.gather(*tasks)
        logger.info(
            f"ASRManager 初始化完成，"
            f"主={self._primary.name if self._primary else 'None'}，"
            f"备={self._fallback.name if self._fallback else 'None'}"
        )

    async def transcribe(self, audio: bytes | np.ndarray) -> ASRResult:
        """
        识别音频，主 ASR 失败时自动降级到备用 ASR
        """
        if self._primary:
            try:
                result = await asyncio.wait_for(
                    self._primary.transcribe(audio),
                    timeout=8.0,  # 云端 ASR 超时时间
                )
                self._stats["primary_success"] += 1
                return result
            except asyncio.TimeoutError:
                logger.warning(f"主 ASR ({self._primary.name}) 超时，切换到备用")
                self._stats["primary_failure"] += 1
            except Exception as e:
                logger.error(f"主 ASR ({self._primary.name}) 出错: {e}")
                self._stats["primary_failure"] += 1

        if self._fallback:
            logger.info(f"使用备用 ASR: {self._fallback.name}")
            self._stats["fallback_used"] += 1
            return await self._fallback.transcribe(audio)

        return ASRResult(text="", is_final=True)

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._stats["primary_success"] + self._stats["primary_failure"]
        success_rate = self._stats["primary_success"] / total if total > 0 else 0
        return {
            **self._stats,
            "primary_success_rate": f"{success_rate:.1%}",
        }

    async def close(self) -> None:
        tasks = []
        if self._primary:
            tasks.append(self._primary.close())
        if self._fallback:
            tasks.append(self._fallback.close())
        await asyncio.gather(*tasks)
```

---

## 10.6 标点恢复

ASR 模型（尤其是端到端模型）输出的文本通常没有标点：

```
输入语音: "今天天气怎么样 明天我想去爬山 你觉得合适吗"
ASR 输出: "今天天气怎么样明天我想去爬山你觉得合适吗"
期望输出: "今天天气怎么样？明天我想去爬山，你觉得合适吗？"
```

没有标点的文字直接送给 TTS，朗读出来语调会很奇怪——停顿位置不对，句子边界不清晰。

### 方案一：开启 ASR 自带的标点恢复

阿里云 NLS 和 FunASR 都支持在推理时开启标点恢复（ITN + 标点）：

```python
# FunASR SenseVoice 开启标点
result = model.generate(
    input=audio,
    use_itn=True,           # 逆文本归一化（"san shi yi" → "31"）
    # 标点由模型自动添加
)

# 阿里云 NLS
recognizer.start(
    enable_punctuation_prediction=True,    # 开启标点预测
    enable_inverse_text_normalization=True, # 开启 ITN
)
```

### 方案二：用 FunASR CT-Transformer 标点模型

如果 ASR 自带的标点质量不够好，可以用专门的标点恢复模型：

```python
# src/voicebot/asr/punctuation.py

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PunctuationRestorer:
    """
    使用 FunASR CT-Transformer 模型添加标点

    模型：ct-transformer-zh-cn-punct
    """

    def __init__(self):
        self._model = None

    async def init(self):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info("标点恢复模型加载完成")

    def _load_model(self):
        from funasr import AutoModel
        self._model = AutoModel(
            model="ct-transformer-zh-cn-punct",
            disable_log=True,
        )

    async def restore(self, text: str) -> str:
        """为无标点文本添加标点"""
        if not text.strip():
            return text

        if self._model is None:
            return text  # 未初始化时直接返回原文

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model.generate(input=text)
        )

        if result and result[0].get("text"):
            return result[0]["text"]
        return text


# 简单的规则后处理（不依赖模型，速度快）
def simple_punctuation_fix(text: str) -> str:
    """
    基于规则的简单标点修复
    适合对延迟极其敏感的场景（模型方式需要额外 50-200ms）
    """
    if not text:
        return text

    text = text.strip()

    # 句末没有标点时，根据疑问词判断加什么标点
    question_words = ["吗", "呢", "啊", "嘛", "吧", "么", "什么", "怎么", "哪里", "谁"]
    ends_with_question = any(text.endswith(w) for w in question_words)

    if not text[-1] in "。？！，、；：.?!":
        if ends_with_question:
            text += "？"
        else:
            text += "。"

    return text
```

---

## 10.7 常见问题排查

### 问题 1：识别率差，经常识别错误

```
排查步骤：

1. 检查音频质量
   - 采样率必须是 16kHz（不是 8kHz，不是 44.1kHz）
   - 声道必须是单声道（立体声转单声道取平均）
   - 检查音量：int16 最大值为 32767，正常通话音量峰值应在 5000-25000

2. 检查 VAD 切割质量
   - 是否切掉了句子开头（pre_roll_ms 太小）
   - 是否包含太多静音（silence_threshold_ms 太大）
   - 保存 VAD 切出的音频文件，用播放器听一下

3. 检查采集端
   - 是否开启了回声消除（echoCancellation）
   - 是否开启了噪声抑制（noiseSuppression）

代码示例：检查音频质量
```

```python
# scripts/check_audio_quality.py

import wave
import numpy as np
import matplotlib.pyplot as plt


def analyze_audio(wav_file: str):
    with wave.open(wav_file, "r") as f:
        sample_rate = f.getframerate()
        channels = f.getnchannels()
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    print(f"采样率: {sample_rate} Hz {'✓' if sample_rate == 16000 else '✗ (需要16000)'}")
    print(f"声道数: {channels} {'✓' if channels == 1 else '✗ (需要单声道)'}")
    print(f"时长: {len(audio) / sample_rate:.2f} 秒")
    print(f"峰值振幅: {np.max(np.abs(audio))} (正常范围: 5000-25000)")
    print(f"均方根: {np.sqrt(np.mean(audio.astype(np.float32)**2)):.0f}")

    # 检测是否有裁剪（限幅失真）
    clipped = np.sum(np.abs(audio) >= 32700)
    if clipped > 0:
        print(f"⚠ 检测到 {clipped} 个采样点可能过载（振幅接近32767）")
    else:
        print("✓ 无明显过载")


analyze_audio("test_audio/sample.wav")
```

### 问题 2：延迟高（从说完到收到识别结果超过 2 秒）

```
延迟构成分析：
┌────────────────┬──────────────┬──────────────────────────────┐
│ 阶段           │ 典型耗时     │ 优化方向                     │
├────────────────┼──────────────┼──────────────────────────────┤
│ VAD 尾部延迟   │ 600ms        │ 减小 silence_threshold_ms    │
│ 网络传输       │ 50-200ms     │ 选择更近的 CDN 节点          │
│ 云端 ASR 处理  │ 200-500ms    │ 切换本地 ASR                 │
│ 本地 ASR 处理  │ 50-200ms     │ 使用 GPU，换更快的模型       │
└────────────────┴──────────────┴──────────────────────────────┘

最大的延迟来源通常是 VAD 尾部延迟（等待静音确认）
```

### 问题 3：中英文混合识别效果差

SenseVoice 对中英文混合的支持是最好的，使用 `language="auto"` 参数：

```python
# SenseVoice 中英混合识别
result = model.generate(
    input=audio,
    language="auto",  # 自动检测，而不是指定 "zh" 或 "en"
    use_itn=True,
)
# 能正确识别："我要订一张 business class 的机票"
```

---

## 本章小结

本章构建了 VoiceBot 的完整 ASR 层：

- **流式 ASR 原理**：CTC 解码使得逐帧输出中间结果成为可能；中间结果用于实时显示，最终结果用于 LLM 处理
- **统一抽象接口**：`BaseASR` 定义了 `transcribe()` 和 `transcribe_stream()` 接口，业务代码不依赖具体实现
- **阿里云 NLS**：云端方案，接入简单，有免费额度，适合快速验证
- **SenseVoice**：本地方案，234MB 模型，CPU 上 RTF < 0.1，适合成本敏感或数据合规场景
- **ASRManager**：主/备双 ASR，自动降级，透明切换
- **标点恢复**：FunASR CT-Transformer 模型，或简单规则处理
- **质量排查**：通过采样率、音量、VAD 切割质量来系统排查识别率问题

下一章，我们把识别出的文字送给 LLM 处理，并把 LLM 的回复流式传递给 TTS 播放。在 VoiceAI 中，LLM 不能像普通聊天机器人那样回复——它需要更短的句子、更口语化的表达，还要避免 Markdown 格式符号被 TTS 读出来。
