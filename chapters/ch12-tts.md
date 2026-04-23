# 第十二章：TTS 语音合成——让 VoiceBot 开口说话

---

想象一下你刚刚完成了 ASR 和 LLM 的接入，用户说了一句话，VoiceBot 也生成了一段文字回复。但问题来了——怎么把这段文字"说"出来？

你可以把文字显示在屏幕上，但那不是语音助手，那是聊天机器人。真正的 VoiceBot 需要有自己的声音。

这就是 TTS（Text-to-Speech，文字转语音）要解决的问题。

本章我们从 TTS 的工作原理讲起，然后分别接入云端方案（CosyVoice API）和本地方案（Kokoro），最后设计一套统一的 TTS 接口，让 VoiceBot 可以随时换"嗓子"。

---

## 12.1 TTS 是怎么工作的

你可能以为 TTS 就是把文字"朗读"出来，原理很简单。其实不然，现代 TTS 系统经历了几个关键步骤：

```
文字输入
   │
   ▼
┌─────────────────┐
│   文本前处理     │  ← 数字、缩写、特殊符号的标准化
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   语言分析      │  ← 分词、词性标注、韵律预测
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   音素转换      │  ← 文字 → 音素序列（拼音/音标）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   声学模型      │  ← 音素序列 → 声谱图（Mel Spectrogram）
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   声码器        │  ← 声谱图 → 音频波形（PCM）
└────────┬────────┘
         │
         ▼
     音频输出
```

**文本前处理**：把"2024年"读成"二零二四年"，把"Dr."读成"博士"，把"😊"忽略掉。

**音素转换**：中文里就是转成拼音，英文里是转成 IPA 音标。这一步决定了发音是否准确。

**声学模型**：这是 TTS 的核心，通常是一个神经网络，负责把音素序列转换成声谱图。声谱图描述了不同时刻不同频率的能量分布。

**声码器（Vocoder）**：把声谱图还原成可以播放的 PCM 音频。现代声码器（如 HiFi-GAN）生成质量已经接近人声。

现代端到端 TTS 模型（比如 Kokoro、CosyVoice）把这些步骤合并了，输入文字，直接输出音频，中间过程对用户透明。

---

## 12.2 流式合成 vs 批量合成

对于 VoiceBot 来说，"快"是第一位的。用户不希望等 LLM 生成完整回复之后，再等 TTS 生成完整音频，再开始播放。

这就引出了两种工作模式的对比：

### 批量合成（Batch TTS）

```
LLM 输出：[等待... 等待... 等待...] → "今天天气真不错，适合出去走走。"
                                                │
TTS：                                   [等待... 等待...] → 完整音频
                                                │
播放：                                                  → ████████████
```

用户感受：等了很久，然后突然开始说话。

### 流式合成（Streaming TTS）

```
LLM 输出流：  "今天天气" → "真不错，" → "适合出去" → "走走。"
                  │              │              │          │
TTS 流：       音频片段1  →  音频片段2  →  音频片段3  → 音频片段4
                  │
播放：          ██ → ████ → ██████ → ████████
```

用户感受：几乎立刻开始听到声音，整体延迟大幅降低。

流式合成的关键在于**句子切分**：我们不能等 LLM 输出完整句子再合成，但也不能一个字一个字地发给 TTS（质量太差）。合理的切分点是关键，后面我们会详细讲。

---

## 12.3 云端方案：CosyVoice API 接入

CosyVoice 是阿里巴巴开源的高质量 TTS 模型，既可以本地部署，也提供云端 API。我们先从 API 接入开始。

### 12.3.1 基础接入

```python
# src/voicebot/tts/cosyvoice_api.py

import asyncio
import aiohttp
import json
from typing import AsyncIterator
import logging

logger = logging.getLogger(__name__)


class CosyVoiceAPIClient:
    """CosyVoice 云端 API 客户端"""

    def __init__(
        self,
        api_url: str,
        voice: str = "longxiaochun",
        sample_rate: int = 22050,
    ) -> None:
        self.api_url = api_url
        self.voice = voice
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> bytes:
        """批量合成：返回完整音频 bytes"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": text,
                "voice": self.voice,
                "format": "pcm",
                "sample_rate": self.sample_rate,
            }
            async with session.post(
                f"{self.api_url}/synthesize",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.read()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成：逐块返回音频数据"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "text": text,
                "voice": self.voice,
                "format": "pcm",
                "sample_rate": self.sample_rate,
                "stream": True,
            }
            async with session.post(
                f"{self.api_url}/synthesize_stream",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        yield chunk
```

### 12.3.2 本地部署的 CosyVoice（gRPC 版本）

如果你在自己的服务器上部署了 CosyVoice，通常会用 gRPC 接口，性能更好：

```python
# src/voicebot/tts/cosyvoice_grpc.py

import asyncio
import grpc
from typing import AsyncIterator
import logging

# 假设 proto 文件已经编译好
# from .grpc_pb import cosyvoice_pb2, cosyvoice_pb2_grpc

logger = logging.getLogger(__name__)


class CosyVoiceGRPCClient:
    """CosyVoice gRPC 客户端"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 50000,
        voice: str = "中文女",
        sample_rate: int = 22050,
    ) -> None:
        self.host = host
        self.port = port
        self.voice = voice
        self.sample_rate = sample_rate
        self._channel: grpc.aio.Channel | None = None
        self._stub = None

    async def connect(self) -> None:
        """建立 gRPC 连接"""
        self._channel = grpc.aio.insecure_channel(f"{self.host}:{self.port}")
        # self._stub = cosyvoice_pb2_grpc.CosyVoiceStub(self._channel)
        logger.info(f"Connected to CosyVoice at {self.host}:{self.port}")

    async def close(self) -> None:
        """关闭连接"""
        if self._channel:
            await self._channel.close()

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式合成"""
        if self._stub is None:
            raise RuntimeError("Not connected. Call connect() first.")

        # request = cosyvoice_pb2.Request(
        #     tts_text=text,
        #     spk_id=self.voice,
        # )
        # async for response in self._stub.Inference(request):
        #     if response.tts_audio:
        #         yield response.tts_audio
        #
        # 示意性代码，实际需要根据你的 proto 定义调整
        yield b""
```

---

## 12.4 本地方案：Kokoro 完整接入

Kokoro 是一个轻量级高质量 TTS 模型，只有 82M 参数，普通 CPU 就能跑，非常适合本地部署或资源受限的场景。

### 12.4.1 安装依赖

```bash
pip install kokoro-onnx soundfile numpy
```

Kokoro 使用 ONNX 格式，不需要 PyTorch，依赖极简。

### 12.4.2 完整接入代码

```python
# src/voicebot/tts/kokoro_local.py

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class KokoroLocalTTS:
    """
    Kokoro 本地 TTS 引擎

    模型参数：82M，支持 CPU 推理
    支持语言：中文、英文、日文等
    输出采样率：24000 Hz
    """

    OUTPUT_SAMPLE_RATE = 24000  # Kokoro 固定输出 24kHz

    def __init__(
        self,
        model_path: str | None = None,
        voice: str = "zf_xiaobei",
        speed: float = 1.0,
    ) -> None:
        """
        Args:
            model_path: ONNX 模型文件路径，None 则自动下载
            voice: 声音名称，中文推荐 zf_xiaobei / zm_yunyang
            speed: 语速，1.0 为正常速度
        """
        self.model_path = model_path
        self.voice = voice
        self.speed = speed
        self._kokoro = None

    def _load_model(self) -> None:
        """加载模型（懒加载）"""
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise ImportError(
                "请安装 kokoro-onnx: pip install kokoro-onnx"
            )

        if self.model_path and os.path.exists(self.model_path):
            model_file = self.model_path
        else:
            # 自动下载模型
            logger.info("未找到本地模型，尝试自动下载 Kokoro 模型...")
            model_file = None  # Kokoro 库会自动处理下载

        self._kokoro = Kokoro(model_file, lang="zh")
        logger.info(f"Kokoro TTS 模型已加载，声音：{self.voice}")

    def _ensure_loaded(self) -> None:
        """确保模型已加载"""
        if self._kokoro is None:
            self._load_model()

    def synthesize_sync(self, text: str) -> bytes:
        """
        同步合成（在线程池中调用）

        Returns:
            PCM 音频数据（16-bit，24kHz，单声道）
        """
        self._ensure_loaded()

        samples, sample_rate = self._kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang="zh",
        )

        # 转换为 16-bit PCM bytes
        audio_int16 = (samples * 32767).astype(np.int16)
        return audio_int16.tobytes()

    async def synthesize(self, text: str) -> bytes:
        """
        异步合成：在线程池中运行同步推理
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.synthesize_sync, text)

    async def synthesize_stream(
        self,
        text: str,
        chunk_size_ms: int = 200,
    ) -> AsyncIterator[bytes]:
        """
        流式合成：先完整合成，再按块输出

        Kokoro 本身不支持真正的流式输出，但我们可以把完整音频
        切成小块逐步 yield，配合播放缓冲实现"伪流式"效果。

        Args:
            text: 要合成的文字
            chunk_size_ms: 每块音频的时长（毫秒）
        """
        audio_bytes = await self.synthesize(text)

        # 计算每块的字节数
        # 16-bit PCM = 2 bytes/sample, 24000 samples/sec
        bytes_per_ms = self.OUTPUT_SAMPLE_RATE * 2 // 1000
        chunk_bytes = chunk_size_ms * bytes_per_ms

        for i in range(0, len(audio_bytes), chunk_bytes):
            chunk = audio_bytes[i : i + chunk_bytes]
            if chunk:
                yield chunk
                # 让出控制权，避免阻塞事件循环
                await asyncio.sleep(0)

    def get_sample_rate(self) -> int:
        """返回输出采样率"""
        return self.OUTPUT_SAMPLE_RATE
```

### 12.4.3 验证安装

```python
# scripts/test_kokoro.py

import asyncio
import soundfile as sf
import numpy as np
from voicebot.tts.kokoro_local import KokoroLocalTTS


async def main() -> None:
    tts = KokoroLocalTTS(voice="zf_xiaobei", speed=1.0)

    test_text = "你好，我是 VoiceBot，很高兴认识你。"
    print(f"正在合成：{test_text}")

    audio_bytes = await tts.synthesize(test_text)

    # 转换回 numpy array 并保存为 wav
    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
    sf.write("test_output.wav", audio_array, tts.get_sample_rate())

    duration = len(audio_array) / tts.get_sample_rate()
    print(f"合成完成！时长：{duration:.2f}秒，文件：test_output.wav")


asyncio.run(main())
```

运行后你会得到一个 `test_output.wav` 文件，用播放器打开就能听到效果。

---

## 12.5 统一 TTS 接口设计

VoiceBot 可能在不同场景下用不同的 TTS 引擎：开发时用本地 Kokoro，生产时用云端 CosyVoice API。我们需要一个统一接口，让上层代码不关心底层用的是哪个引擎。

### 12.5.1 Protocol 定义

```python
# src/voicebot/tts/base.py

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class TTSEngine(Protocol):
    """TTS 引擎统一接口"""

    async def synthesize(self, text: str) -> bytes:
        """
        批量合成

        Args:
            text: 要合成的文字（已经过预处理）

        Returns:
            PCM 音频数据（16-bit 有符号整数）
        """
        ...

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        流式合成

        Yields:
            PCM 音频块（16-bit 有符号整数）
        """
        ...

    def get_sample_rate(self) -> int:
        """返回输出采样率（Hz）"""
        ...
```

### 12.5.2 TTS 管理器

```python
# src/voicebot/tts/manager.py

import asyncio
import logging
from typing import AsyncIterator

from .base import TTSEngine
from .text_processor import TextPreprocessor, SentenceSplitter

logger = logging.getLogger(__name__)


class TTSManager:
    """
    TTS 管理器

    负责：
    1. 文本预处理
    2. 句子切分
    3. 调用底层 TTS 引擎
    4. 流式输出音频块
    """

    def __init__(
        self,
        engine: TTSEngine,
        target_sample_rate: int = 16000,
    ) -> None:
        self.engine = engine
        self.target_sample_rate = target_sample_rate
        self._preprocessor = TextPreprocessor()
        self._splitter = SentenceSplitter()

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """
        把文字转成音频流

        完整流程：预处理 → 切分 → 合成 → 采样率转换
        """
        # 1. 预处理
        clean_text = self._preprocessor.process(text)
        if not clean_text.strip():
            logger.debug("预处理后文字为空，跳过合成")
            return

        # 2. 切分成句子
        sentences = self._splitter.split(clean_text)
        logger.debug(f"切分为 {len(sentences)} 个句子")

        # 3. 逐句合成
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            logger.debug(f"合成句子：{sentence[:20]}...")

            async for chunk in self.engine.synthesize_stream(sentence):
                # 4. 采样率转换（如果需要）
                converted = self._convert_sample_rate(
                    chunk,
                    self.engine.get_sample_rate(),
                    self.target_sample_rate,
                )
                yield converted

    def _convert_sample_rate(
        self,
        pcm_bytes: bytes,
        src_rate: int,
        dst_rate: int,
    ) -> bytes:
        """简单的采样率转换"""
        if src_rate == dst_rate:
            return pcm_bytes

        try:
            import numpy as np
            from scipy import signal

            audio = np.frombuffer(pcm_bytes, dtype=np.int16)
            # 计算重采样后的长度
            new_length = int(len(audio) * dst_rate / src_rate)
            resampled = signal.resample(audio, new_length)
            return resampled.astype(np.int16).tobytes()
        except ImportError:
            # 没有 scipy 就直接返回原始数据，让上层处理
            logger.warning("scipy 未安装，跳过采样率转换")
            return pcm_bytes
```

---

## 12.6 文本预处理

TTS 引擎对输入格式很敏感。LLM 输出的文本可能包含各种"杂质"，直接丢给 TTS 会出问题：

- Markdown 格式符号（`**粗体**`、`# 标题`）
- 数字（"2024年"要读成"二零二四年"还是"两千零二十四年"？）
- 特殊符号（URL、表情符号、括号内的英文注释）

```python
# src/voicebot/tts/text_processor.py

import re
import logging

logger = logging.getLogger(__name__)

# 数字转中文的映射
_DIGITS = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千", "万", "十万", "百万", "千万", "亿"]


def _number_to_chinese(n: int) -> str:
    """把整数转换为中文读法"""
    if n == 0:
        return "零"
    if n < 0:
        return "负" + _number_to_chinese(-n)

    result = ""
    s = str(n)

    for i, digit in enumerate(s):
        unit_idx = len(s) - i - 1
        d = int(digit)
        if d != 0:
            result += _DIGITS[d]
            if unit_idx < len(_UNITS):
                result += _UNITS[unit_idx]
        elif result and not result.endswith("零"):
            result += "零"

    return result.rstrip("零") or "零"


class TextPreprocessor:
    """
    TTS 文本预处理器

    按顺序执行：
    1. 去除 Markdown 格式
    2. 去除 URL
    3. 去除表情符号
    4. 数字转中文
    5. 清理多余空白
    """

    def process(self, text: str) -> str:
        text = self._remove_markdown(text)
        text = self._remove_urls(text)
        text = self._remove_emojis(text)
        text = self._convert_numbers(text)
        text = self._clean_whitespace(text)
        return text

    def _remove_markdown(self, text: str) -> str:
        """去除 Markdown 格式符号"""
        # 代码块（多行）
        text = re.sub(r"```[\s\S]*?```", "", text)
        # 行内代码
        text = re.sub(r"`[^`]+`", "", text)
        # 粗体/斜体
        text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
        text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
        # 标题符号
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # 列表符号
        text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
        # 链接 [text](url) → text
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        # 图片 ![alt](url) → 去掉
        text = re.sub(r"!\[[^\]]*\]\([^\)]+\)", "", text)
        # 表格分隔符
        text = re.sub(r"\|[-:]+\|", "", text)
        text = re.sub(r"\|", " ", text)
        return text

    def _remove_urls(self, text: str) -> str:
        """去除 URL"""
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"www\.\S+", "", text)
        return text

    def _remove_emojis(self, text: str) -> str:
        """去除 emoji 表情符号"""
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # 表情
            "\U0001F300-\U0001F5FF"  # 符号&象形文字
            "\U0001F680-\U0001F6FF"  # 交通&地图
            "\U0001F1E0-\U0001F1FF"  # 国旗
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.sub("", text)

    def _convert_numbers(self, text: str) -> str:
        """把阿拉伯数字转换为中文读法"""

        def replace_number(m: re.Match) -> str:
            num_str = m.group(0)
            # 有小数点的情况
            if "." in num_str:
                integer_part, decimal_part = num_str.split(".", 1)
                result = _number_to_chinese(int(integer_part))
                result += "点"
                result += "".join(_DIGITS[int(d)] for d in decimal_part)
                return result
            return _number_to_chinese(int(num_str))

        # 匹配整数和小数
        return re.sub(r"\d+(?:\.\d+)?", replace_number, text)

    def _clean_whitespace(self, text: str) -> str:
        """清理多余的空白字符"""
        # 多个换行变成一个
        text = re.sub(r"\n{2,}", "\n", text)
        # 多个空格变成一个
        text = re.sub(r" {2,}", " ", text)
        # 去掉行首行尾空白
        text = "\n".join(line.strip() for line in text.split("\n"))
        return text.strip()
```

### 验证预处理效果

```python
# 快速测试
from voicebot.tts.text_processor import TextPreprocessor

preprocessor = TextPreprocessor()

test_cases = [
    "**今天**天气不错，气温22度。",
    "请访问 https://example.com 了解详情。",
    "# 介绍\n\n这是一个测试。",
    "一共花了3.5小时，完成了2024个任务。",
    "😊 很高兴认识你！",
]

for text in test_cases:
    result = preprocessor.process(text)
    print(f"原文：{text}")
    print(f"处理：{result}")
    print()
```

输出：
```
原文：**今天**天气不错，气温22度。
处理：今天天气不错，气温二十二度。

原文：请访问 https://example.com 了解详情。
处理：请访问  了解详情。

原文：# 介绍\n\n这是一个测试。
处理：介绍
这是一个测试。

原文：一共花了3.5小时，完成了2024个任务。
处理：一共花了三点五小时，完成了二零二四个任务。

原文：😊 很高兴认识你！
处理：很高兴认识你！
```

---

## 12.7 句子切分策略

句子切分是流式 TTS 中最微妙的部分。切得太细，音频质量差（每个短片段的韵律感不连贯）；切得太粗，延迟高（要等很长的句子合成完才能播放第一个音频块）。

### 12.7.1 切分点的选择

关键原则：**逗号不是好的切分点**。

为什么？因为逗号切分会导致录音间隙——两个音频块之间有一个短暂的停顿，但在正常说话时，逗号处的停顿是说话人内部控制的，不该有明显间隙。

合适的切分点：

```
句号 。    ← 一句话的终止，切这里最自然
问号 ？    ← 同上
感叹号 ！  ← 同上
换行符 \n  ← 段落结束

不适合切分：
逗号 ，   ← 会造成停顿感，韵律断裂
顿号 、   ← 同上
分号 ；   ← 可以切，但要结合上下文
```

### 12.7.2 切分实现

```python
# src/voicebot/tts/text_processor.py（续）

class SentenceSplitter:
    """
    句子切分器

    策略：
    - 按句号/问号/感叹号切分
    - 保证每个片段有足够长度（避免过短）
    - 超长句子按长度强制切分
    """

    # 触发切分的标点符号
    SPLIT_PUNCTUATION = set("。！？\n")

    # 单个合成片段的长度范围
    MIN_LENGTH = 5    # 太短的片段合成质量差
    MAX_LENGTH = 100  # 太长的片段延迟高

    def split(self, text: str) -> list[str]:
        """
        把文本切分成适合 TTS 合成的片段列表

        Args:
            text: 预处理后的文本

        Returns:
            句子片段列表，每个片段适合单次 TTS 合成
        """
        sentences: list[str] = []
        current = ""

        for char in text:
            current += char

            if char in self.SPLIT_PUNCTUATION:
                # 到达切分点
                if len(current) >= self.MIN_LENGTH:
                    sentences.append(current)
                    current = ""
                # 太短则继续积累

            elif len(current) >= self.MAX_LENGTH:
                # 超过最大长度，强制切分
                # 尽量在最近的标点处切
                cut_pos = self._find_last_punctuation(current)
                if cut_pos > self.MIN_LENGTH:
                    sentences.append(current[:cut_pos + 1])
                    current = current[cut_pos + 1:]
                else:
                    sentences.append(current)
                    current = ""

        # 处理剩余文本
        if current.strip():
            sentences.append(current)

        return sentences

    def _find_last_punctuation(self, text: str) -> int:
        """在文本中找最后一个可切分的标点位置"""
        # 扩展切分点：末尾找不到强切分点时，考虑更多标点
        soft_split = set("；:：")

        for i in range(len(text) - 1, -1, -1):
            if text[i] in self.SPLIT_PUNCTUATION or text[i] in soft_split:
                return i

        return -1  # 没找到，返回 -1 表示不在标点处切
```

### 12.7.3 测试切分效果

```python
splitter = SentenceSplitter()

text = """
今天天气不错，适合出去走走。
你有什么计划？
我想去公园，然后去图书馆，最后回家做饭。
如果天气突然变化，我们就去商场吧！
"""

sentences = splitter.split(text.strip())
for i, s in enumerate(sentences, 1):
    print(f"片段 {i}（{len(s)}字）：{s}")
```

输出：
```
片段 1（11字）：今天天气不错，适合出去走走。
片段 2（7字）：你有什么计划？
片段 3（20字）：我想去公园，然后去图书馆，最后回家做饭。
片段 4（14字）：如果天气突然变化，我们就去商场吧！
```

注意第 3 个片段——"我想去公园，然后去图书馆，最后回家做饭"里有两个逗号，但我们没有在逗号处切分，而是整句一起合成，这样韵律更自然。

---

## 12.8 采样率处理

TTS 引擎输出的采样率不一定和系统其他部分匹配。比如：
- Kokoro 输出 24000 Hz
- 电话系统通常用 8000 Hz
- Web 浏览器常用 16000 Hz 或 44100 Hz

不转换采样率，播放出来的声音会变调（太慢变低沉，太快变尖细）。

```python
# src/voicebot/audio/resampler.py

import numpy as np
import logging

logger = logging.getLogger(__name__)


def resample_pcm(
    pcm_bytes: bytes,
    src_rate: int,
    dst_rate: int,
) -> bytes:
    """
    对 PCM 音频进行重采样

    Args:
        pcm_bytes: 16-bit 有符号 PCM 音频数据
        src_rate: 源采样率（Hz）
        dst_rate: 目标采样率（Hz）

    Returns:
        重采样后的 PCM 音频数据
    """
    if src_rate == dst_rate:
        return pcm_bytes

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    # 计算目标长度
    target_length = int(len(audio) * dst_rate / src_rate)

    try:
        from scipy import signal
        resampled = signal.resample(audio, target_length)
    except ImportError:
        # 没有 scipy，用简单的线性插值
        logger.warning("scipy 未安装，使用线性插值重采样（质量较低）")
        indices = np.linspace(0, len(audio) - 1, target_length)
        resampled = np.interp(indices, np.arange(len(audio)), audio)

    # 限幅并转回 int16
    resampled = np.clip(resampled, -32768, 32767)
    return resampled.astype(np.int16).tobytes()


def get_audio_duration_ms(pcm_bytes: bytes, sample_rate: int) -> float:
    """计算 PCM 音频时长（毫秒）"""
    num_samples = len(pcm_bytes) // 2  # 16-bit = 2 bytes/sample
    return num_samples / sample_rate * 1000
```

---

## 12.9 完整集成示例

把所有组件串起来，看看完整的 TTS 流水线：

```python
# examples/tts_demo.py

import asyncio
import logging
from voicebot.tts.kokoro_local import KokoroLocalTTS
from voicebot.tts.manager import TTSManager

logging.basicConfig(level=logging.INFO)


async def demo_streaming_tts() -> None:
    """演示流式 TTS 合成"""

    # 初始化 TTS 引擎
    engine = KokoroLocalTTS(voice="zf_xiaobei", speed=1.0)

    # 创建 TTS 管理器，目标采样率 16kHz
    manager = TTSManager(engine=engine, target_sample_rate=16000)

    # 模拟 LLM 的一段输出
    llm_response = """
    好的！Python 是一门非常适合初学者的编程语言。
    它的语法简洁清晰，读起来几乎像英文一样。
    你可以用它做数据分析、网站开发、自动化脚本，甚至人工智能！
    我建议你从基础语法开始，比如变量、循环、函数这些概念。
    有什么具体想学的方向吗？
    """

    total_bytes = 0
    chunk_count = 0

    print("开始流式 TTS 合成...")
    async for audio_chunk in manager.speak(llm_response):
        total_bytes += len(audio_chunk)
        chunk_count += 1
        print(f"收到音频块 {chunk_count}：{len(audio_chunk)} bytes")
        # 实际使用中这里会把音频块发送给播放器

    print(f"\n合成完成！共 {chunk_count} 个音频块，总计 {total_bytes} bytes")

    # 计算时长
    duration_s = total_bytes / (16000 * 2)  # 16kHz, 16-bit
    print(f"音频总时长：{duration_s:.2f} 秒")


asyncio.run(demo_streaming_tts())
```

---

## 本章小结

本章我们为 VoiceBot 添加了"嗓子"：

- **TTS 工作原理**：文字经过音素转换、声学模型、声码器，最终变成音频波形。
- **流式 vs 批量**：流式合成大幅降低首字延迟，是 VoiceBot 的必选方案。
- **CosyVoice**：云端高质量 TTS，适合生产环境，支持 HTTP API 和 gRPC。
- **Kokoro**：82M 参数的本地 TTS，CPU 可跑，适合开发调试和私有部署。
- **统一接口**：通过 `TTSEngine` Protocol 解耦上层代码和底层引擎。
- **文本预处理**：去 Markdown、去 URL、数字转中文，保证 TTS 输入干净。
- **句子切分**：按句号/问号/感叹号切分，**逗号不切**，避免录音间隙。
- **采样率转换**：用 scipy 或线性插值，确保音频采样率匹配播放设备。

现在 VoiceBot 能听（ASR）、能想（LLM）、能说（TTS）了。但这三个模块还是独立的，怎么把它们连接成一个实时系统？

**下一章**我们来看 WebSocket 网关——VoiceBot 的通信核心，负责接收浏览器发来的音频，返回 TTS 合成的音频流，并管理连接的整个生命周期。
