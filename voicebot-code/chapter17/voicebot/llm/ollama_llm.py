
import httpx
import json
from collections.abc import AsyncGenerator


class OllamaLLM:
    def __init__(self, model: str = "qwen2:7b", base_url: str = "http://localhost:11434") -> None:
        self._model = model
        self._base_url = base_url

    async def generate_stream(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": True},
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        data = json.loads(line)
                        if content := data.get("message", {}).get("content"):
                            yield content
                        if data.get("done"):
                            break
