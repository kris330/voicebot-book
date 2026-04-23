# 第 20 章：情感与音色控制

## 你有没有遇到过这种尴尬

你打电话给客服，对方用一成不变的语气说："您好，请问有什么可以帮助您？" 不管你是来投诉、咨询还是闲聊，语气永远是那个机械的腔调。

然后你意识到——对面根本不是人。

现在 VoiceBot 能听懂你说的话，也能用合理的语气回答。但如果 AI 永远用同一种语调说话，那种"机器感"还是挥之不去。当用户在抱怨时，AI 应该用安慰的语气；当用户解决了问题，AI 应该带点高兴的语气；当用户说错了什么，AI 应该温和地纠正，而不是用播报新闻的腔调。

**情感表达，是让 VoiceBot 从"能用"变成"好用"的关键一步。**

本章我们来做三件事：

1. 让 LLM 在回复时输出情感标记
2. 流式解析这个标记，不影响首句延迟
3. 把情感传递给 TTS，控制音色和语速

---

## 20.1 为什么要控制情感

先思考一个问题：TTS 本身不是已经有"自然语调"了吗？为什么还要额外控制情感？

答案是：TTS 的"自然"是基于文本内容的——它会根据标点符号、句子长短来调整语调。但它**不知道上下文**。同样一句"好的，我明白了"，在用户投诉崩溃之后说，和在轻松对话中说，应该有完全不同的情感色彩。TTS 无法自己判断这个。

我们的方案是：**让 LLM 决定情感，让 TTS 执行情感**。

```
用户说：我的问题三天都没解决，太让人失望了

LLM 思考：用户在抱怨，情绪负面，需要安慰的语气
LLM 输出：[EMOTION:1] 非常抱歉给您带来了不好的体验，
          让我来帮您彻底解决这个问题。

情感解析：emotion = 1 (安慰)
TTS 合成：慢速 + 柔和音色 + 语调下沉
```

---

## 20.2 情感标记设计

### 情感值定义

我们设计一套简单的情感编码，0-9 的整数：

```python
# voicebot/emotion.py

from enum import IntEnum
from dataclasses import dataclass

class Emotion(IntEnum):
    ANGRY      = 0   # 愤怒（用户很生气，AI 需要平息）
    COMFORT    = 1   # 安慰（用户沮丧，AI 给予支持）
    HAPPY      = 2   # 高兴（轻松愉快的对话）
    NEUTRAL    = 3   # 中性（默认，信息类回复）
    SERIOUS    = 4   # 严肃（重要事项，需要认真对待）
    EXCITED    = 5   # 激动（用户分享好消息）
    APOLOGETIC = 6   # 道歉（AI 出错需要致歉）
    ENCOURAGING= 7   # 鼓励（用户在学习或尝试）
    CURIOUS    = 8   # 好奇（探讨性话题）
    WARM       = 9   # 温暖（日常问候、关怀）

@dataclass
class EmotionConfig:
    """每种情感对应的 TTS 参数"""
    emotion: Emotion
    speed: float        # 语速倍率，1.0 = 正常
    voice_style: str    # TTS 音色风格名称（取决于具体 TTS）
    pitch_shift: float  # 音调偏移，0.0 = 不变

# 情感 → TTS 参数映射表
EMOTION_CONFIGS: dict[Emotion, EmotionConfig] = {
    Emotion.ANGRY:       EmotionConfig(Emotion.ANGRY,       0.9,  "calm",        -0.1),
    Emotion.COMFORT:     EmotionConfig(Emotion.COMFORT,     0.85, "gentle",      -0.05),
    Emotion.HAPPY:       EmotionConfig(Emotion.HAPPY,       1.1,  "cheerful",     0.05),
    Emotion.NEUTRAL:     EmotionConfig(Emotion.NEUTRAL,     1.0,  "default",      0.0),
    Emotion.SERIOUS:     EmotionConfig(Emotion.SERIOUS,     0.95, "serious",     -0.05),
    Emotion.EXCITED:     EmotionConfig(Emotion.EXCITED,     1.15, "excited",      0.1),
    Emotion.APOLOGETIC:  EmotionConfig(Emotion.APOLOGETIC,  0.9,  "gentle",      -0.05),
    Emotion.ENCOURAGING: EmotionConfig(Emotion.ENCOURAGING, 1.05, "cheerful",     0.05),
    Emotion.CURIOUS:     EmotionConfig(Emotion.CURIOUS,     1.0,  "default",      0.0),
    Emotion.WARM:        EmotionConfig(Emotion.WARM,        0.95, "gentle",       0.02),
}

def get_emotion_config(emotion: Emotion) -> EmotionConfig:
    return EMOTION_CONFIGS.get(emotion, EMOTION_CONFIGS[Emotion.NEUTRAL])
```

为什么用整数而不是字符串？因为 LLM 输出整数更稳定，不会因为大小写、拼写错误导致解析失败。

### System Prompt 设计

要让 LLM 输出情感标记，需要在 System Prompt 里明确要求：

```python
SYSTEM_PROMPT_WITH_EMOTION = """
你是一个语音助手。在每次回复的**最开头**，你必须输出一个情感标记，格式为：

[EMOTION:X]

其中 X 是 0-9 的整数，代表你这次回复的情感基调：
- 0: 愤怒平息（用户情绪激动，你需要冷静应对）
- 1: 安慰（用户沮丧或失望，你给予支持）
- 2: 高兴（轻松愉快的话题）
- 3: 中性（普通信息类回复，默认）
- 4: 严肃（重要事项或正式场合）
- 5: 激动（用户分享好消息，你表示兴奋）
- 6: 道歉（你需要致歉）
- 7: 鼓励（用户在学习或尝试新事物）
- 8: 好奇（探讨性、开放性话题）
- 9: 温暖（问候、关怀）

**规则：**
1. 情感标记必须在回复的第一个字符，不能有任何前置空格或文字
2. 标记之后直接跟回复内容，不要换行
3. 一次回复只有一个情感标记
4. 标记本身不会被用户听到，系统会自动过滤

示例：
用户："我等了两个小时还没解决"
你的回复："[EMOTION:1] 非常抱歉让您久等了，我现在立刻为您处理。"

用户："我通过考试了！"
你的回复："[EMOTION:5] 太棒了，恭喜你！这一定是你努力的结果。"
"""
```

---

## 20.3 流式解析情感标记

这是本章最关键的技术点。

LLM 是流式输出的——token 一个一个地来。情感标记 `[EMOTION:3]` 会被分成多个 token：`[`, `EMOTION`, `:`, `3`, `]`，然后才是正文。

**我们不能等到全部输出完再解析**，因为那样会影响 TTS 的启动时间（TTFS）。

**也不能直接把 `[EMOTION:3]` 发给 TTS**，因为 TTS 会把方括号念出来。

解决方案：**缓冲前 N 个 token，检测并提取标记，之后的 token 直接透传给 TTS**。

```
LLM 流式输出：
  token1: "["
  token2: "EMOTION"
  token3: ":"
  token4: "3"
  token5: "]"      ← 标记结束，提取 emotion=3
  token6: " 好"
  token7: "的"
  token8: "，"     ← 从 token6 开始透传给 TTS
  ...
```

代码实现：

```python
# voicebot/emotion_parser.py

import re
import asyncio
import logging
from typing import AsyncIterator
from .emotion import Emotion, get_emotion_config, EmotionConfig

logger = logging.getLogger(__name__)

# 情感标记的正则表达式
EMOTION_TAG_PATTERN = re.compile(r'^\[EMOTION:(\d+)\]\s*')

# 缓冲多少个字符再尝试解析（标记最长约 12 个字符）
BUFFER_SIZE = 20


class EmotionStreamParser:
    """
    从 LLM 流式输出中提取情感标记。

    工作流程：
    1. 先缓冲前 BUFFER_SIZE 个字符
    2. 尝试匹配情感标记
    3. 如果匹配到，提取情感值，剩余文本继续输出
    4. 如果没有匹配到，按默认情感处理，缓冲区内容直接输出
    5. 之后的所有 token 直接透传（不再缓冲）
    """

    def __init__(self, default_emotion: Emotion = Emotion.NEUTRAL) -> None:
        self._default_emotion = default_emotion
        self._detected_emotion: Emotion | None = None
        self._buffer: str = ""
        self._tag_parsed: bool = False

    @property
    def emotion(self) -> Emotion:
        """返回检测到的情感，未检测到则返回默认值"""
        return self._detected_emotion if self._detected_emotion is not None else self._default_emotion

    @property
    def emotion_config(self) -> EmotionConfig:
        return get_emotion_config(self.emotion)

    async def process_stream(
        self, llm_stream: AsyncIterator[str]
    ) -> AsyncIterator[str]:
        """
        处理 LLM 流，返回去掉情感标记后的文本流。
        情感值通过 self.emotion 属性访问。
        """
        async for chunk in llm_stream:
            if self._tag_parsed:
                # 标记已解析完毕，直接透传
                if chunk:
                    yield chunk
                continue

            # 还在缓冲阶段
            self._buffer += chunk

            if len(self._buffer) >= BUFFER_SIZE:
                # 缓冲区足够大，尝试解析
                yield from self._flush_buffer()
                self._tag_parsed = True

        # 流结束，如果缓冲区还有内容
        if not self._tag_parsed:
            yield from self._flush_buffer()
            self._tag_parsed = True

    def _flush_buffer(self) -> list[str]:
        """
        尝试从缓冲区解析情感标记，返回应该输出的文本列表。
        """
        match = EMOTION_TAG_PATTERN.match(self._buffer)

        if match:
            emotion_value = int(match.group(1))
            try:
                self._detected_emotion = Emotion(emotion_value)
                logger.debug(f"Detected emotion: {self._detected_emotion.name}")
            except ValueError:
                logger.warning(
                    f"Unknown emotion value: {emotion_value}, using default"
                )
                self._detected_emotion = self._default_emotion

            # 返回标记之后的内容
            remaining = self._buffer[match.end():]
            return [remaining] if remaining else []
        else:
            # 没有情感标记，用默认情感
            logger.debug("No emotion tag found, using default emotion")
            self._detected_emotion = self._default_emotion
            result = self._buffer
            self._buffer = ""
            return [result] if result else []
```

### 使用方式

```python
# 在 LLM 流处理中使用 EmotionStreamParser

async def handle_llm_response(
    llm_stream: AsyncIterator[str],
    tts_client: TTSClient,
) -> None:
    parser = EmotionStreamParser(default_emotion=Emotion.NEUTRAL)

    # 收集过滤后的文本，并行送给 TTS
    text_buffer = ""
    async for clean_text in parser.process_stream(llm_stream):
        text_buffer += clean_text
        # 积累到句子边界再送 TTS（按标点切分）
        sentences = split_sentences(text_buffer)
        for sentence in sentences[:-1]:   # 除最后一个（可能不完整）
            # 第一个句子时，情感已经解析完毕
            config = parser.emotion_config
            await tts_client.synthesize(
                text=sentence,
                speed=config.speed,
                voice_style=config.voice_style,
            )
        text_buffer = sentences[-1] if sentences else ""

    # 处理最后剩余的文本
    if text_buffer.strip():
        config = parser.emotion_config
        await tts_client.synthesize(
            text=text_buffer,
            speed=config.speed,
            voice_style=config.voice_style,
        )
```

---

## 20.4 把情感传递给 TTS

不同的 TTS 引擎接受情感参数的方式不同。我们来看几种常见情况。

### 20.4.1 OpenAI TTS（voice 参数）

OpenAI 的 TTS 通过选择不同的 voice 来改变风格：

```python
# voicebot/tts/openai_tts.py

import asyncio
from openai import AsyncOpenAI
from ..emotion import Emotion, EmotionConfig

# OpenAI voice 到情感的映射
# alloy: 中性  echo: 低沉  fable: 故事感  onyx: 权威  nova: 温暖  shimmer: 活泼
OPENAI_VOICE_MAP: dict[str, str] = {
    "calm":       "echo",
    "gentle":     "nova",
    "cheerful":   "shimmer",
    "default":    "alloy",
    "serious":    "onyx",
    "excited":    "shimmer",
}

class OpenAITTSWithEmotion:
    def __init__(self, api_key: str, model: str = "tts-1") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    def _get_voice(self, config: EmotionConfig) -> str:
        return OPENAI_VOICE_MAP.get(config.voice_style, "alloy")

    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """流式合成，边合成边返回音频块"""
        voice = self._get_voice(emotion_config)
        # OpenAI TTS 不直接支持语速，通过 speed 参数（1.0 = 正常）
        speed = emotion_config.speed

        async with self._client.audio.speech.with_streaming_response.create(
            model=self._model,
            voice=voice,
            input=text,
            speed=speed,
            response_format="pcm",
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                yield chunk
```

### 20.4.2 CosyVoice（支持情感标签）

CosyVoice 等开源模型支持在文本中嵌入情感控制标签（类似 SSML 但更简单）：

```python
# voicebot/tts/cosyvoice_emotion.py

from ..emotion import Emotion, EmotionConfig

# CosyVoice 支持的情感指令
COSYVOICE_STYLE_MAP: dict[str, str] = {
    "calm":       "<|CALM|>",
    "gentle":     "<|GENTLE|>",
    "cheerful":   "<|CHEERFUL|>",
    "default":    "",
    "serious":    "<|SERIOUS|>",
    "excited":    "<|EXCITED|>",
}

def wrap_text_with_emotion(text: str, config: EmotionConfig) -> str:
    """
    用 CosyVoice 的情感标签包装文本。
    不同 TTS 引擎格式不同，这是 CosyVoice 的格式。
    """
    style_tag = COSYVOICE_STYLE_MAP.get(config.voice_style, "")
    if style_tag:
        return f"{style_tag}{text}"
    return text


class CosyVoiceTTSWithEmotion:
    def __init__(self, grpc_endpoint: str) -> None:
        self._endpoint = grpc_endpoint
        # 初始化 gRPC channel（省略具体实现）

    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """带情感控制的流式合成"""
        wrapped_text = wrap_text_with_emotion(text, emotion_config)
        speed = emotion_config.speed

        # 调用 gRPC 接口（省略具体实现，参考第 7 章）
        async for audio_chunk in self._grpc_synthesize(
            text=wrapped_text,
            speed=speed,
        ):
            yield audio_chunk
```

### 20.4.3 通用接口抽象

为了让上层代码不关心具体 TTS 实现，我们定义一个统一接口：

```python
# voicebot/tts/base.py

from abc import ABC, abstractmethod
from typing import AsyncIterator
from ..emotion import EmotionConfig


class BaseTTSEngine(ABC):
    """TTS 引擎的抽象基类"""

    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """
        流式合成文本，返回音频字节块。

        Args:
            text: 要合成的文本（不包含情感标记）
            emotion_config: 情感配置（速度、音色等）

        Yields:
            PCM 音频块
        """
        ...

    async def synthesize_all(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> bytes:
        """合成完整音频（非流式，用于短文本）"""
        chunks = []
        async for chunk in self.synthesize_stream(text, emotion_config):
            chunks.append(chunk)
        return b"".join(chunks)
```

---

## 20.5 语速控制的细节

语速不只是"快一点、慢一点"，它影响的是整体节奏感：

```
正常语速 (1.0x):  "今 天 天 气 不 错"  ← 每个字间隔均匀
慢语速  (0.85x):  "今 天  天 气  不 错"  ← 字间隔拉长，感觉从容
快语速  (1.15x):  "今天天气不错"        ← 字间隔缩短，感觉活泼
```

实际项目中，语速参数需要根据用户反馈调整。有些 TTS 引擎的 speed=0.85 效果很好，有些会产生机器音。建议：

```python
# 不要直接用情感配置的语速，而是综合用户偏好
def compute_final_speed(
    emotion_speed: float,
    user_preference: float = 1.0,  # 用户在设置里调的倍率
) -> float:
    """
    合并情感语速和用户偏好。
    用户偏好 1.0 = 不调整，0.9 = 用户希望慢 10%
    """
    final = emotion_speed * user_preference
    # 限制在合理范围
    return max(0.7, min(1.5, final))
```

---

## 20.6 完整集成代码

现在把所有部分串起来，实现一个带情感控制的完整回复流程：

```python
# voicebot/emotion_pipeline.py

import asyncio
import logging
from typing import AsyncIterator, Callable

from .emotion import Emotion, EmotionConfig, get_emotion_config
from .emotion_parser import EmotionStreamParser
from .tts.base import BaseTTSEngine

logger = logging.getLogger(__name__)


def split_sentences(text: str) -> list[str]:
    """
    按中文标点分割句子。
    返回的最后一个元素可能是不完整的句子。
    """
    import re
    # 在句末标点后分割，保留标点
    parts = re.split(r'(?<=[。！？，；])', text)
    return [p for p in parts if p]


class EmotionPipeline:
    """
    带情感控制的 LLM→TTS 流水线。

    数据流：
    LLM 流 → 情感解析 → 句子分割 → TTS 合成 → 音频流
    """

    def __init__(
        self,
        tts_engine: BaseTTSEngine,
        default_emotion: Emotion = Emotion.NEUTRAL,
        min_sentence_length: int = 5,  # 最短句子长度，太短不送 TTS
    ) -> None:
        self._tts = tts_engine
        self._default_emotion = default_emotion
        self._min_sentence_length = min_sentence_length

    async def process(
        self,
        llm_stream: AsyncIterator[str],
        on_emotion_detected: Callable[[Emotion], None] | None = None,
    ) -> AsyncIterator[bytes]:
        """
        处理 LLM 流，返回音频字节流。

        Args:
            llm_stream: LLM 的流式文本输出
            on_emotion_detected: 情感被检测到时的回调（可用于更新 UI）
        """
        parser = EmotionStreamParser(self._default_emotion)
        text_buffer = ""
        emotion_notified = False

        async def generate_audio() -> AsyncIterator[bytes]:
            nonlocal text_buffer, emotion_notified

            async for chunk in parser.process_stream(llm_stream):
                # 情感解析完后立即通知
                if not emotion_notified and parser._tag_parsed:
                    emotion_notified = True
                    if on_emotion_detected:
                        on_emotion_detected(parser.emotion)
                    logger.info(f"Emotion detected: {parser.emotion.name}")

                text_buffer += chunk
                sentences = split_sentences(text_buffer)

                # 保留最后一个（可能未完整）
                complete_sentences = sentences[:-1]
                text_buffer = sentences[-1] if sentences else ""

                for sentence in complete_sentences:
                    if len(sentence.strip()) >= self._min_sentence_length:
                        config = parser.emotion_config
                        logger.debug(
                            f"Synthesizing: '{sentence[:20]}...' "
                            f"emotion={config.emotion.name} "
                            f"speed={config.speed}"
                        )
                        async for audio_chunk in self._tts.synthesize_stream(
                            sentence, config
                        ):
                            yield audio_chunk

            # 处理最后剩余的文本
            if text_buffer.strip():
                if not emotion_notified and on_emotion_detected:
                    on_emotion_detected(parser.emotion)
                config = parser.emotion_config
                async for audio_chunk in self._tts.synthesize_stream(
                    text_buffer, config
                ):
                    yield audio_chunk

        async for audio in generate_audio():
            yield audio
```

### 集成到 WebSocket 服务器

```python
# voicebot/server.py（相关片段）

import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket
from .emotion import Emotion
from .emotion_pipeline import EmotionPipeline
from .tts.openai_tts import OpenAITTSWithEmotion
from .llm_client import LLMClient

logger = logging.getLogger(__name__)
app = FastAPI()


async def handle_voice_session(websocket: WebSocket) -> None:
    await websocket.accept()

    tts_engine = OpenAITTSWithEmotion(api_key="YOUR_KEY")
    pipeline = EmotionPipeline(tts_engine=tts_engine)
    llm_client = LLMClient()

    try:
        while True:
            # 接收用户语音（已经过 ASR 转成文字）
            data = await websocket.receive_json()
            user_text = data.get("text", "")

            if not user_text:
                continue

            logger.info(f"User said: {user_text}")

            # 定义情感检测回调，通知前端更新 UI
            def on_emotion(emotion: Emotion) -> None:
                # 这里用 asyncio.create_task 避免阻塞
                asyncio.create_task(
                    websocket.send_json({
                        "type": "emotion",
                        "value": emotion.value,
                        "name": emotion.name,
                    })
                )

            # 获取 LLM 流式输出
            llm_stream = llm_client.stream_chat(user_text)

            # 通过情感流水线生成音频
            audio_chunks = []
            async for audio_chunk in pipeline.process(
                llm_stream, on_emotion_detected=on_emotion
            ):
                # 实时发送音频块给前端
                await websocket.send_bytes(audio_chunk)

            # 发送结束信号
            await websocket.send_json({"type": "audio_end"})

    except Exception as e:
        logger.error(f"Session error: {e}", exc_info=True)
    finally:
        await websocket.close()
```

---

## 20.7 调试：当情感不对时怎么排查

情感控制出问题时，通常有这几种情况：

**1. LLM 没有输出情感标记**

检查 System Prompt 是否被正确传递。加一个日志：

```python
# 在 LLM 客户端里，记录原始输出的前 50 个字符
first_chunk = True
async for chunk in llm_stream:
    if first_chunk:
        logger.debug(f"LLM first chunk: {repr(chunk)}")
        first_chunk = False
    yield chunk
```

**2. 情感标记被 TTS 读出来**

说明 `EmotionStreamParser` 没有正确过滤。检查 `EMOTION_TAG_PATTERN` 是否匹配实际输出格式。用单元测试验证：

```python
# tests/test_emotion_parser.py

import asyncio
import pytest
from voicebot.emotion_parser import EmotionStreamParser
from voicebot.emotion import Emotion


async def stream_from_text(text: str):
    """把字符串模拟成流式输出（每次一个字符）"""
    for char in text:
        yield char


@pytest.mark.asyncio
async def test_emotion_tag_extracted():
    parser = EmotionStreamParser()
    llm_output = "[EMOTION:2] 太棒了，你做得很好！"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    assert parser.emotion == Emotion.HAPPY
    assert "[EMOTION" not in result
    assert "太棒了" in result


@pytest.mark.asyncio
async def test_no_emotion_tag_uses_default():
    parser = EmotionStreamParser(default_emotion=Emotion.NEUTRAL)
    llm_output = "好的，我来帮您处理这个问题。"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    assert parser.emotion == Emotion.NEUTRAL
    assert "好的" in result


@pytest.mark.asyncio
async def test_unknown_emotion_value():
    parser = EmotionStreamParser(default_emotion=Emotion.NEUTRAL)
    llm_output = "[EMOTION:99] 这是一个不存在的情感值。"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    # 应该回退到默认情感，而不是崩溃
    assert parser.emotion == Emotion.NEUTRAL
```

**3. 语速参数没有生效**

每个 TTS 引擎对 speed 参数的支持不一样。有些引擎忽略 speed=1.0 以外的值。用单独的测试脚本验证：

```python
# scripts/test_emotion_tts.py

import asyncio
from voicebot.emotion import Emotion, get_emotion_config
from voicebot.tts.openai_tts import OpenAITTSWithEmotion

async def main():
    tts = OpenAITTSWithEmotion(api_key="YOUR_KEY")

    test_cases = [
        (Emotion.COMFORT, "非常抱歉，请允许我来帮您解决这个问题。"),
        (Emotion.HAPPY, "太好了！您的操作完全正确！"),
        (Emotion.SERIOUS, "请注意，这是一个重要的安全提示。"),
    ]

    for emotion, text in test_cases:
        config = get_emotion_config(emotion)
        print(f"\n情感: {emotion.name}, 语速: {config.speed}, 音色: {config.voice_style}")
        print(f"文本: {text}")

        audio_data = await tts.synthesize_all(text, config)

        filename = f"test_emotion_{emotion.name.lower()}.pcm"
        with open(filename, "wb") as f:
            f.write(audio_data)
        print(f"已保存: {filename}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 本章小结

本章我们为 VoiceBot 添加了情感感知能力：

1. **情感标记设计**：用 `[EMOTION:X]` 格式，0-9 的整数代表不同情感，整数比字符串更稳定可靠。

2. **System Prompt 设计**：明确告诉 LLM 在回复开头输出情感标记，提供了完整的格式规范和示例。

3. **流式解析**：`EmotionStreamParser` 缓冲前 20 个字符，解析情感标记后立即透传剩余内容，不影响 TTS 启动时间。

4. **TTS 情感适配**：把情感值转换为 TTS 的具体参数（voice 名称、speed 倍率），不同 TTS 引擎有不同的适配方式。

5. **完整流水线**：`EmotionPipeline` 把 LLM 流 → 情感解析 → 句子分割 → TTS 合成 串成一个完整的异步流水线。

**关键设计原则**：情感控制加在 LLM 输出和 TTS 之间，不修改 LLM 的回复内容，不影响用户听到的文字，只影响声音的风格。这样即使情感解析失败，系统也能正常降级为中性语气。

下一章，我们来解决一个工程问题：如何让整个系统可配置，让运维人员不改代码就能切换模型和参数。
