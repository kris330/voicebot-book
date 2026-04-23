
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "INFO"


@dataclass(frozen=True)
class SessionConfig:
    timeout_seconds: int = 1800
    max_history_turns: int = 20
    system_prompt: str = ""


@dataclass(frozen=True)
class ASRConfig:
    provider: str = "openai"
    model: str = "whisper-1"
    language: str = "zh"
    api_key: str = ""


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 500
    api_key: str = ""


@dataclass(frozen=True)
class TTSConfig:
    provider: str = "openai"
    model: str = "tts-1"
    voice: str = "alloy"
    speed: float = 1.0
    api_key: str = ""


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    session: SessionConfig
    asr: ASRConfig
    llm: LLMConfig
    tts: TTSConfig


def _resolve_env_vars(value: Any) -> Any:
    """
    递归解析配置中的环境变量占位符。
    ${VAR_NAME} 会被替换为对应的环境变量值。
    """
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'
        def replace(match):
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                logger.warning(f"环境变量 {var_name} 未设置")
                return ""
            return env_value
        return re.sub(pattern, replace, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: str = "config.json") -> Config:
    """
    从 JSON 文件加载配置，并解析环境变量占位符。

    Args:
        config_path: 配置文件路径

    Returns:
        Config 对象

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置文件格式错误
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)

    # 解析环境变量
    resolved = _resolve_env_vars(raw)

    # 构建配置对象
    server_data = resolved.get("server", {})
    session_data = resolved.get("session", {})
    asr_data = resolved.get("asr", {})
    llm_data = resolved.get("llm", {})
    tts_data = resolved.get("tts", {})

    config = Config(
        server=ServerConfig(**server_data),
        session=SessionConfig(**session_data),
        asr=ASRConfig(**asr_data),
        llm=LLMConfig(**llm_data),
        tts=TTSConfig(**tts_data),
    )

    logger.info(
        f"配置已加载: ASR={config.asr.provider}/{config.asr.model}, "
        f"LLM={config.llm.provider}/{config.llm.model}, "
        f"TTS={config.tts.provider}/{config.tts.model}"
    )
    return config
