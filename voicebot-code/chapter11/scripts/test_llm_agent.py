
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
