
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
