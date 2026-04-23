
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
