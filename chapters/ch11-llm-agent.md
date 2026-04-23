# 第十一章：LLM Agent 对话引擎

## 开篇场景

VoiceBot 的语音管道已经打通：用户说话 → VAD 切割 → ASR 识别成文字。现在我们需要把这段文字送给 LLM，得到 AI 的回复，再交给 TTS 朗读出来。

听起来很简单——不就是调一下 OpenAI API 吗？

但实际跑起来之后你会发现很多问题：

> 用户问："今天天气怎么样？"
> AI 回复："## 今天的天气情况\n\n根据您的问题，我需要告诉您：**今天的天气**可能因地区而异。\n\n- 如果您在北方..."

TTS 朗读出来会是：井井号号 今天的天气情况 换行换行 根据您的问题...

这就是 VoiceAI 场景的特殊性：**LLM 必须输出适合朗读的文字，而不是适合显示的 Markdown**。

此外，流式输出、对话历史管理、工具调用……这些在普通聊天场景里也有，但 VoiceAI 对延迟要求更高，每个环节都需要精心设计。

---

## 11.1 VoiceAI 中 LLM 的特殊要求

### 输出格式

```
普通聊天 LLM 输出（适合显示）：
  ## 明天行程安排
  根据您的日历，明天有以下安排：
  1. **上午 9:00** - 项目周会
  2. **下午 2:00** - 客户拜访
  > 提醒：记得准备 PPT

VoiceAI LLM 输出（适合朗读）：
  明天您有两个安排。上午九点有项目周会，下午两点要去拜访客户。
  另外提醒您，记得准备幻灯片。
```

核心原则：
- **无 Markdown**：`#`、`**`、`-`、`>`、` ``` ` 等符号会被 TTS 读出来
- **短句优先**：一句话不超过 30 个字，便于 TTS 在自然停顿处开始播放
- **口语化表达**：不说"如下所示"，而说"我来告诉你"；不说"请参阅"，而说"你可以去看看"
- **数字写法**：写"三百元"而不是"300元"（视 TTS 能力而定，有的 TTS 能自动处理）

### 流式输出的重要性

```
非流式：
  用户说完 → 等 LLM 生成完整回复（1-5秒）→ TTS 开始播放

流式：
  用户说完 → LLM 第一个 token 就开始 → 积累到第一个句子 → TTS 立刻播放
           → LLM 继续生成 → TTS 继续播放
           ↑
           感知延迟 ≈ 首 token 时间（通常 200-500ms）
```

流式输出让 VoiceBot 的响应延迟从"几秒"降到"不到一秒"，是 VoiceAI 体验的关键。

---

## 11.2 接入 OpenAI 兼容接口

现在大多数主流 LLM 都提供 OpenAI 兼容的 API（Qwen、DeepSeek、GLM 等）。只需要修改 `base_url` 和 `api_key`，就可以无缝切换。

```python
# src/voicebot/llm/client.py

import os
from openai import AsyncOpenAI


def create_llm_client(provider: str = "auto") -> AsyncOpenAI:
    """
    创建 LLM 客户端

    provider 可选值：
      openai   - OpenAI 官方 API
      qwen     - 阿里云通义千问
      deepseek - DeepSeek
      ollama   - 本地 Ollama（免费，需要本地部署）
      auto     - 根据环境变量自动选择
    """
    if provider == "auto":
        provider = os.environ.get("LLM_PROVIDER", "openai")

    configs = {
        "openai": {
            "api_key": os.environ.get("OPENAI_API_KEY"),
            "base_url": None,  # 使用默认值
            "default_model": "gpt-4o-mini",
        },
        "qwen": {
            "api_key": os.environ.get("QWEN_API_KEY"),
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen-turbo",
        },
        "deepseek": {
            "api_key": os.environ.get("DEEPSEEK_API_KEY"),
            "base_url": "https://api.deepseek.com",
            "default_model": "deepseek-chat",
        },
        "ollama": {
            "api_key": "ollama",  # Ollama 不需要真实的 API key
            "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "default_model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        },
    }

    if provider not in configs:
        raise ValueError(f"不支持的 provider: {provider}，可选: {list(configs.keys())}")

    config = configs[provider]
    client = AsyncOpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    client._default_model = config["default_model"]  # 附加默认模型名
    return client
```

---

## 11.3 流式输出：async generator 实现

```python
# src/voicebot/llm/streaming.py

import asyncio
import logging
import re
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

logger = logging.getLogger(__name__)

# 句子边界的正则表达式
# 匹配中文句末标点和英文句末标点
SENTENCE_BOUNDARY = re.compile(r'[。！？!?\.…]+')


async def stream_llm_response(
    client: AsyncOpenAI,
    messages: list[dict],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 500,
) -> AsyncGenerator[str, None]:
    """
    流式调用 LLM，按句子边界拆分输出

    不是逐 token 输出，而是等到句子结束再 yield，
    这样 TTS 可以拿到完整的句子来合成，语调更自然。

    Yields:
        str: 每次 yield 一个完整的句子（或半句，如果句子很长）
    """
    model = model or getattr(client, "_default_model", "gpt-4o-mini")
    buffer = ""

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in stream:
            delta = _extract_delta(chunk)
            if delta is None:
                continue

            buffer += delta

            # 检查 buffer 里是否有完整的句子
            while True:
                match = SENTENCE_BOUNDARY.search(buffer)
                if not match:
                    break

                # 找到句子边界，提取这个句子
                end_pos = match.end()
                sentence = buffer[:end_pos].strip()
                buffer = buffer[end_pos:]

                if sentence:
                    logger.debug(f"[LLM] 输出句子: {sentence!r}")
                    yield sentence

        # 处理 buffer 里剩余的内容（最后一段可能没有标点）
        if buffer.strip():
            yield buffer.strip()

    except asyncio.CancelledError:
        logger.info("[LLM] 流式输出被取消")
        raise
    except Exception as e:
        logger.error(f"[LLM] 流式调用失败: {e}", exc_info=True)
        raise


def _extract_delta(chunk: ChatCompletionChunk) -> Optional[str]:
    """从 chunk 中提取文本增量"""
    if not chunk.choices:
        return None
    delta = chunk.choices[0].delta
    if delta.content is None:
        return None
    return delta.content
```

### 测试流式输出

```python
# scripts/test_llm_stream.py

import asyncio
import time

from voicebot.llm.client import create_llm_client
from voicebot.llm.streaming import stream_llm_response


async def main():
    client = create_llm_client("qwen")  # 或 "openai", "ollama"

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个语音助手。回答要简短，每句话不超过20个字。"
                "不要使用任何 Markdown 格式。用口语化的中文回答。"
            ),
        },
        {"role": "user", "content": "介绍一下北京有哪些著名景点"},
    ]

    print("LLM 输出（按句子分块）：")
    print("-" * 40)

    start = time.monotonic()
    first_token_time = None

    async for sentence in stream_llm_response(client, messages):
        if first_token_time is None:
            first_token_time = time.monotonic()
            print(f"[首包延迟: {(first_token_time - start)*1000:.0f}ms]")
        print(f"  → {sentence!r}")

    total_time = time.monotonic() - start
    print(f"\n总耗时: {total_time*1000:.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 11.4 对话历史管理

多轮对话需要把历史消息一起发给 LLM，但历史越来越长会撑爆上下文窗口，也会增加每次调用的费用和延迟。

### 截断策略

```
策略选择：

简单截断（滑动窗口）：
  保留最近 N 轮对话
  优点：实现简单
  缺点：可能丢失重要的早期上下文

Token 限制截断：
  保留不超过 K 个 token 的历史
  优点：精确控制成本
  缺点：需要 tokenizer

摘要压缩（高级）：
  当历史过长时，用 LLM 对早期历史做摘要
  优点：不丢失重要信息
  缺点：需要额外 LLM 调用

对于 VoiceBot，推荐使用"保留最近 10-20 轮 + system prompt"的滑动窗口策略。
语音对话通常比文字对话要短，每次说话不超过 50 个 token，
10-20 轮历史也只有 500-1000 个 token。
```

```python
# src/voicebot/llm/history.py

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str
    # 工具调用相关（第 11.6 节会用到）
    tool_call_id: str | None = None
    tool_calls: list | None = None


class ConversationHistory:
    """
    对话历史管理

    维护一个滑动窗口，自动截断过长的历史
    """

    def __init__(
        self,
        system_prompt: str,
        max_turns: int = 20,
    ):
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._messages: list[Message] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))
        self._trim()

    def add_assistant_message(self, content: str) -> None:
        self._messages.append(Message(role="assistant", content=content))

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._messages.append(Message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
        ))

    def _trim(self) -> None:
        """截断到 max_turns 轮"""
        # 一轮 = 一个 user 消息 + 一个 assistant 消息
        # 加上可能的 tool 消息，每轮最多约 3 条消息
        max_messages = self._max_turns * 3

        if len(self._messages) > max_messages:
            removed = len(self._messages) - max_messages
            self._messages = self._messages[-max_messages:]
            logger.debug(f"[历史] 截断了 {removed} 条旧消息")

    def to_api_format(self) -> list[dict]:
        """转换为 OpenAI API 格式"""
        result = [{"role": "system", "content": self._system_prompt}]

        for msg in self._messages:
            item = {"role": msg.role, "content": msg.content}
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                item["tool_calls"] = msg.tool_calls
            result.append(item)

        return result

    def clear(self) -> None:
        self._messages.clear()

    @property
    def turn_count(self) -> int:
        return sum(1 for m in self._messages if m.role == "user")

    def __len__(self) -> int:
        return len(self._messages)
```

---

## 11.5 System Prompt 设计

System Prompt 是控制 LLM 输出风格最有效的工具。对于 VoiceAI，需要在 prompt 里明确指出输出格式要求。

```python
# src/voicebot/llm/prompts.py

VOICE_ASSISTANT_SYSTEM_PROMPT = """你是一个语音助手，名字叫小智。

【重要输出格式要求】
- 回答必须简洁，每次回答控制在 3 句话以内
- 每句话不超过 30 个字
- 禁止使用任何 Markdown 格式（不用 #、**、-、>、```等符号）
- 不用列表，改用"第一...第二...第三..."的口语表达
- 数字直接用汉字读法，如"三百元"而不是"300元"
- 用自然的口语，不用书面语，不用"您"改用"你"

【回答风格】
- 直接回答，不用说"好的，我来帮你..."
- 不确定的事情直接说不知道，不要编造信息
- 需要澄清时，用简短的问句

【示例】
❌ 错误："## 北京景点推荐\n根据您的问题，以下是一些著名景点：\n1. **故宫** - 明清皇宫..."
✓ 正确："北京最值得去的是故宫和天坛。故宫是明清两朝的皇宫，非常壮观。天坛则是古代皇帝祭天的地方。"
"""


def build_system_prompt(
    assistant_name: str = "小智",
    persona: str = "",
    tools: list[str] = None,
) -> str:
    """
    动态构建 system prompt

    Args:
        assistant_name: 助手名字
        persona: 角色设定（如"你是一个专业的旅游顾问"）
        tools: 可用工具列表（用于提示 LLM 何时使用工具）
    """
    base = f"""你是一个语音助手，名字叫{assistant_name}。"""

    if persona:
        base += f"\n\n{persona}"

    base += """

【输出格式要求（非常重要）】
回答将被转换为语音播放，因此必须遵守以下规则：
1. 禁止使用任何 Markdown 标记：不用井号标题、不用星号加粗、不用连字符列表
2. 简短回答：每次不超过 3 句话，每句不超过 25 个字
3. 口语表达：用"第一、第二"而不是列表；用"也就是说"而不是"即"
4. 直接回答：不要说"好的"、"当然可以"等无意义的开场白
"""

    if tools:
        tools_desc = "、".join(tools)
        base += f"\n\n【可用能力】你有以下工具可以使用：{tools_desc}。需要时主动调用，不需要告诉用户你在调用什么工具。"

    return base
```

---

## 11.6 Tool Use（工具调用）基础

工具调用让 LLM 可以执行真实操作：查天气、查日历、控制智能家居、查数据库……这是 VoiceBot 从"聊天机器人"变成"AI 助手"的关键。

### 工具调用的完整流程

```
用户: "北京今天天气怎么样？"
         │
         ▼
    LLM 分析需要调用工具
         │
         ▼
    LLM 输出 tool_call:
    {
      "name": "get_weather",
      "arguments": {"city": "北京", "date": "today"}
    }
         │
         ▼
    我们的代码执行 get_weather("北京", "today")
         │
         ▼
    返回结果: {"temp": 18, "condition": "多云", "wind": "北风3级"}
         │
         ▼
    把结果加入对话历史，再次调用 LLM
         │
         ▼
    LLM 输出最终回复:
    "北京今天多云，气温十八度，北风三级，挺适合出门的。"
```

### 定义工具

```python
# src/voicebot/llm/tools.py

from typing import Any, Callable, Awaitable
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具定义，符合 OpenAI 函数调用格式"""
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[str]]

    def to_openai_format(self) -> dict:
        """转换为 OpenAI tools 参数格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ==================== 工具实现 ====================

async def get_weather(city: str, date: str = "today") -> str:
    """
    查询天气（示例：实际项目中调用真实天气 API）
    """
    # 真实项目中这里调用天气 API，如：
    # response = await http_client.get(f"https://api.weather.com/v1/{city}")
    # return format_weather(response.json())

    # 示例数据
    weather_data = {
        "北京": {"temp": 18, "condition": "多云", "wind": "北风三级"},
        "上海": {"temp": 22, "condition": "晴", "wind": "东南风二级"},
        "广州": {"temp": 28, "condition": "阵雨", "wind": "南风四级"},
    }

    data = weather_data.get(city, {"temp": 20, "condition": "未知", "wind": "微风"})
    return f"{city}{date}天气：{data['condition']}，气温{data['temp']}度，{data['wind']}"


async def query_calendar(date: str) -> str:
    """查询日历（示例）"""
    # 真实项目中查询 Google Calendar 或企业日历
    if date == "today" or date == "今天":
        return "今天下午三点有一个项目评审会议，预计一小时。"
    return f"{date}暂无日程安排。"


async def control_device(device: str, action: str) -> str:
    """控制智能家居设备（示例）"""
    # 真实项目中调用 Home Assistant API 或涂鸦 API
    return f"已将{device}{action}"


# ==================== 工具注册表 ====================

AVAILABLE_TOOLS = [
    ToolDefinition(
        name="get_weather",
        description="查询指定城市的天气情况",
        parameters={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称，如北京、上海",
                },
                "date": {
                    "type": "string",
                    "description": "日期，如 today（今天）、tomorrow（明天）",
                    "default": "today",
                },
            },
            "required": ["city"],
        },
        handler=get_weather,
    ),
    ToolDefinition(
        name="query_calendar",
        description="查询日历上的日程安排",
        parameters={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期，如 today、tomorrow 或具体日期",
                }
            },
            "required": ["date"],
        },
        handler=query_calendar,
    ),
    ToolDefinition(
        name="control_device",
        description="控制智能家居设备，如打开灯、调节空调温度",
        parameters={
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "设备名称，如客厅灯、空调",
                },
                "action": {
                    "type": "string",
                    "description": "操作指令，如打开、关闭、调到26度",
                },
            },
            "required": ["device", "action"],
        },
        handler=control_device,
    ),
]

# 方便快速查找
TOOLS_BY_NAME = {tool.name: tool for tool in AVAILABLE_TOOLS}
```

---

## 11.7 完整的 LLMAgent 类

把上面所有模块组合在一起：

```python
# src/voicebot/llm/agent.py

import asyncio
import json
import logging
from typing import AsyncGenerator, Callable, Optional, Awaitable

from openai import AsyncOpenAI

from voicebot.llm.client import create_llm_client
from voicebot.llm.history import ConversationHistory
from voicebot.llm.prompts import build_system_prompt
from voicebot.llm.streaming import stream_llm_response
from voicebot.llm.tools import AVAILABLE_TOOLS, TOOLS_BY_NAME, ToolDefinition

logger = logging.getLogger(__name__)


class LLMAgent:
    """
    VoiceBot 的 LLM 对话引擎

    功能：
    - 多轮对话历史管理
    - 流式输出（按句子分块，供 TTS 逐句播放）
    - 工具调用（天气、日历、设备控制等）
    - 自动降级（工具执行失败时告知用户）
    """

    def __init__(
        self,
        client: Optional[AsyncOpenAI] = None,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_turns: int = 20,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
    ):
        self._client = client or create_llm_client()
        self._model = model or getattr(self._client, "_default_model", "gpt-4o-mini")
        self._temperature = temperature
        self._tools = tools if tools is not None else AVAILABLE_TOOLS

        system_prompt = system_prompt or build_system_prompt(
            tools=[t.name for t in self._tools] if self._tools else []
        )
        self._history = ConversationHistory(
            system_prompt=system_prompt,
            max_turns=max_turns,
        )

    async def chat_stream(
        self,
        user_input: str,
        on_sentence: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """
        处理用户输入，流式生成回复

        Args:
            user_input: 用户说的话（ASR 识别结果）
            on_sentence: 每生成一个完整句子时的回调（用于触发 TTS）

        Returns:
            完整的回复文本
        """
        self._history.add_user_message(user_input)
        logger.info(f"[LLMAgent] 用户输入: {user_input!r}")

        full_response = await self._generate_with_tools(on_sentence)

        self._history.add_assistant_message(full_response)
        logger.info(f"[LLMAgent] 完整回复: {full_response!r}")

        return full_response

    async def _generate_with_tools(
        self,
        on_sentence: Optional[Callable[[str], Awaitable[None]]],
    ) -> str:
        """
        生成回复，支持工具调用

        工具调用时不使用流式（等待工具执行结果），
        最终回复时使用流式。
        """
        messages = self._history.to_api_format()
        tools_format = [t.to_openai_format() for t in self._tools] if self._tools else None

        # 最多执行 5 次工具调用循环（防止无限循环）
        for _ in range(5):
            # 先非流式调用，检查是否需要工具调用
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools_format,
                tool_choice="auto" if tools_format else None,
                temperature=self._temperature,
                max_tokens=500,
                stream=False,  # 有工具时先用非流式
            )

            message = response.choices[0].message

            # 检查是否有工具调用
            if not message.tool_calls:
                break  # 没有工具调用，直接进入流式生成

            # 执行工具调用
            logger.info(f"[LLMAgent] LLM 要求调用工具: {[tc.function.name for tc in message.tool_calls]}")

            # 把 LLM 的工具调用请求加入历史
            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            })

            # 并发执行所有工具调用
            tool_results = await asyncio.gather(
                *[self._execute_tool(tc) for tc in message.tool_calls],
                return_exceptions=True,
            )

            # 把工具结果加入历史
            for tc, result in zip(message.tool_calls, tool_results):
                if isinstance(result, Exception):
                    result_str = f"工具执行失败: {result}"
                    logger.error(f"[LLMAgent] 工具 {tc.function.name} 执行失败: {result}")
                else:
                    result_str = str(result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                logger.info(f"[LLMAgent] 工具结果 [{tc.function.name}]: {result_str!r}")

        # 现在用流式生成最终回复
        full_response = ""
        async for sentence in stream_llm_response(
            self._client,
            messages,
            model=self._model,
            temperature=self._temperature,
        ):
            full_response += sentence
            if on_sentence:
                try:
                    await on_sentence(sentence)
                except Exception as e:
                    logger.error(f"[LLMAgent] on_sentence 回调出错: {e}")

        return full_response

    async def _execute_tool(self, tool_call) -> str:
        """执行单个工具调用"""
        name = tool_call.function.name
        tool = TOOLS_BY_NAME.get(name)

        if not tool:
            return f"未知工具: {name}"

        try:
            arguments = json.loads(tool_call.function.arguments)
            result = await tool.handler(**arguments)
            return result
        except json.JSONDecodeError as e:
            return f"参数解析失败: {e}"
        except TypeError as e:
            return f"参数不匹配: {e}"
        except Exception as e:
            return f"执行失败: {e}"

    def clear_history(self) -> None:
        """清除对话历史（开始新对话时调用）"""
        self._history.clear()
        logger.info("[LLMAgent] 对话历史已清除")

    @property
    def turn_count(self) -> int:
        return self._history.turn_count
```

---

## 11.8 整合到语音管道

把 LLMAgent 与 VAD、ASR 整合，形成完整的语音处理链路：

```python
# src/voicebot/pipeline/full_pipeline.py

import asyncio
import logging
from typing import Optional

import numpy as np

from voicebot.vad.vad_manager import VADManager, VADConfig
from voicebot.asr.asr_manager import ASRManager
from voicebot.llm.agent import LLMAgent

logger = logging.getLogger(__name__)


class FullVoicePipeline:
    """
    完整的语音处理管道：
    音频输入 → VAD → ASR → LLMAgent → TTS（下一章）
    """

    def __init__(self):
        self._vad = VADManager(VADConfig())
        self._asr = ASRManager.from_env()
        self._llm = LLMAgent()

        # 回调函数
        self._on_user_speech: Optional[callable] = None  # VAD 检测到语音
        self._on_transcript: Optional[callable] = None   # ASR 识别完成
        self._on_llm_sentence: Optional[callable] = None # LLM 生成一个句子
        self._on_response_done: Optional[callable] = None # LLM 生成完成

        # 防止并发处理（用户说话时打断 AI 回复）
        self._current_response_task: Optional[asyncio.Task] = None

    async def init(self):
        await asyncio.gather(
            self._vad.init(),
            self._asr.init(),
        )
        logger.info("FullVoicePipeline 初始化完成")

    def on(self, event: str, callback: callable) -> "FullVoicePipeline":
        """注册事件回调（链式调用）"""
        setattr(self, f"_on_{event}", callback)
        return self

    async def process_audio(self, audio_chunk: bytes):
        """处理一帧音频"""
        chunk = np.frombuffer(audio_chunk, dtype=np.int16)
        segment = await self._vad.process_chunk(chunk)

        if segment is not None:
            # 用户说完了，取消正在进行的 AI 回复（打断功能）
            if self._current_response_task and not self._current_response_task.done():
                logger.info("[Pipeline] 检测到用户说话，打断 AI 回复")
                self._current_response_task.cancel()

            # 启动 ASR + LLM 处理（异步，不阻塞继续录音）
            self._current_response_task = asyncio.create_task(
                self._handle_speech(segment.audio)
            )

    async def _handle_speech(self, audio: np.ndarray):
        """处理一段语音：ASR → LLM → 触发 TTS"""
        try:
            # ASR 识别
            asr_result = await self._asr.transcribe(audio)

            if not asr_result.text.strip():
                logger.debug("[Pipeline] ASR 结果为空，忽略")
                return

            logger.info(f"[Pipeline] ASR: {asr_result.text!r}")

            if self._on_transcript:
                await self._on_transcript(asr_result.text)

            # LLM 生成回复
            async def on_sentence(sentence: str):
                """每生成一个句子，触发 TTS"""
                if self._on_llm_sentence:
                    await self._on_llm_sentence(sentence)

            full_response = await self._llm.chat_stream(
                user_input=asr_result.text,
                on_sentence=on_sentence,
            )

            if self._on_response_done:
                await self._on_response_done(full_response)

        except asyncio.CancelledError:
            logger.info("[Pipeline] 处理被取消（用户打断）")
        except Exception as e:
            logger.error(f"[Pipeline] 处理失败: {e}", exc_info=True)

    def new_session(self):
        """开始新会话，清除历史"""
        self._llm.clear_history()
        self._vad.reset()
        if self._current_response_task:
            self._current_response_task.cancel()
```

### WebSocket 服务器整合

```python
# src/voicebot/server/ws_handler.py（更新版）

import json
import logging

from aiohttp import web, WSMsgType

from voicebot.pipeline.full_pipeline import FullVoicePipeline

logger = logging.getLogger(__name__)


async def ws_voice_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=64 * 1024)
    await ws.prepare(request)

    session_id = f"sess_{id(ws)}"
    pipeline = FullVoicePipeline()

    # 注册回调：把各阶段结果发回给客户端
    async def send(data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    pipeline.on("transcript", lambda text: send({"type": "transcript", "text": text}))
    pipeline.on("llm_sentence", lambda text: send({"type": "llm_sentence", "text": text}))
    pipeline.on("response_done", lambda text: send({"type": "response_done", "text": text}))

    await pipeline.init()
    await ws.send_json({"type": "ready"})
    logger.info(f"[{session_id}] 连接就绪")

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await pipeline.process_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "new_session":
                    pipeline.new_session()
                    await ws.send_json({"type": "session_cleared"})
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    except Exception as e:
        logger.error(f"[{session_id}] 连接错误: {e}")

    logger.info(f"[{session_id}] 连接关闭")
    return ws
```

---

## 11.9 快速测试：命令行对话

在接入完整语音链路之前，先用命令行测试 LLM Agent：

```python
# scripts/test_llm_agent.py

import asyncio
import os

from voicebot.llm.agent import LLMAgent
from voicebot.llm.client import create_llm_client
from voicebot.llm.prompts import VOICE_ASSISTANT_SYSTEM_PROMPT


async def interactive_test():
    """
    交互式命令行测试，验证 LLMAgent 的各项功能：
    - 多轮对话
    - System prompt 效果（是否有 Markdown）
    - 工具调用（输入"北京天气"测试）
    """
    # 设置环境变量（或者放在 .env 文件里）
    # os.environ["LLM_PROVIDER"] = "qwen"
    # os.environ["QWEN_API_KEY"] = "..."

    client = create_llm_client()
    agent = LLMAgent(
        client=client,
        system_prompt=VOICE_ASSISTANT_SYSTEM_PROMPT,
    )

    print("VoiceBot LLM Agent 测试（输入 quit 退出，输入 clear 清除历史）")
    print("-" * 50)

    while True:
        user_input = input("\n你: ").strip()

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "clear":
            agent.clear_history()
            print("[历史已清除]")
            continue

        print("AI: ", end="", flush=True)

        async def on_sentence(sentence: str):
            print(sentence, end="", flush=True)

        await agent.chat_stream(user_input, on_sentence=on_sentence)
        print(f"\n[第 {agent.turn_count} 轮对话]")


if __name__ == "__main__":
    asyncio.run(interactive_test())
```

运行测试：

```bash
python scripts/test_llm_agent.py

# 测试用例建议：
# 1. "你好"              → 检查是否有 Markdown
# 2. "介绍一下长城"      → 检查回复长度是否合理
# 3. "北京今天天气怎样"  → 检查工具调用是否触发
# 4. "明天有什么日程"    → 检查工具调用
# 5. "打开客厅的灯"      → 检查设备控制工具
# 6. "刚才说的是什么"    → 检查多轮对话上下文
```

---

## 本章小结

本章构建了 VoiceBot 的 LLM 对话引擎：

- **VoiceAI 的特殊要求**：输出不能有 Markdown，句子要短，表达要口语化——这些必须通过 System Prompt 强制约束
- **OpenAI 兼容接口**：修改 `base_url` 和 `api_key`，可以无缝切换 Qwen、DeepSeek、Ollama 等任意兼容 API
- **流式输出**：使用 async generator，按句子边界拆分 token 流，每产生一个完整句子就触发 TTS，感知延迟接近首 token 时间（200-500ms）
- **对话历史管理**：滑动窗口截断（保留最近 20 轮），`to_api_format()` 统一转换为 API 格式
- **System Prompt 设计**：明确禁止 Markdown，限制每句话长度，指定口语化表达风格
- **工具调用**：定义工具 → LLM 决策调用哪个工具 → 我们执行工具 → 结果返回给 LLM → 生成最终回复
- **打断功能**：用户说话时取消当前 `asyncio.Task`，实现"AI 回答到一半被打断"的自然交互

至此，VoiceBot 的核心管道已经全部打通：

```
麦克风 → 客户端 VAD → WebSocket → 服务端 VAD → ASR → LLMAgent → TTS → 扬声器
```

下一章，我们完成最后一块拼图——TTS（文字转语音）。LLMAgent 流式生成的每个句子，需要快速合成为自然的语音并播放出来，实现真正"边想边说"的流式 TTS 体验。
