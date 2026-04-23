
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
