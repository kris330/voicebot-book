
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
