
import os
import logging
from .config import VoiceBotConfig, EngineConfig, LLMConfig
from .registry import asr_registry, llm_registry, tts_registry
from .engines.asr.base import BaseASREngine
from .engines.llm.base import BaseLLMEngine
from .engines.tts.base import BaseTTSEngine

logger = logging.getLogger(__name__)


def _resolve_env_vars(config_dict: dict) -> dict:
    """
    递归地把配置中的环境变量引用替换为实际值。

    支持格式: "${ENV_VAR_NAME}" 或 "${ENV_VAR_NAME:default_value}"

    示例:
      "api_key": "${OPENAI_API_KEY}"
      "endpoint": "${TTS_ENDPOINT:localhost:50051}"
    """
    import re
    ENV_VAR_PATTERN = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')

    result = {}
    for key, value in config_dict.items():
        if isinstance(value, str):
            def replace(match):
                var_name = match.group(1)
                default = match.group(2)
                env_value = os.environ.get(var_name, default)
                if env_value is None:
                    raise ValueError(
                        f"Required environment variable '{var_name}' is not set"
                    )
                return env_value
            result[key] = ENV_VAR_PATTERN.sub(replace, value)
        elif isinstance(value, dict):
            result[key] = _resolve_env_vars(value)
        else:
            result[key] = value
    return result


def create_asr_engine(asr_cfg: EngineConfig) -> BaseASREngine:
    """从配置创建 ASR 引擎实例"""
    engine_class = asr_registry.get(asr_cfg.engine)
    resolved_config = _resolve_env_vars(asr_cfg.config)
    logger.info(f"Creating ASR engine: {asr_cfg.engine}")
    return engine_class(**resolved_config)


def create_llm_engine(llm_cfg: LLMConfig) -> BaseLLMEngine:
    """从配置创建 LLM 引擎实例"""
    engine_class = llm_registry.get(llm_cfg.engine)
    resolved_config = _resolve_env_vars(llm_cfg.config)

    # 加载 system prompt 文件
    system_prompt = ""
    if llm_cfg.system_prompt_file:
        try:
            with open(llm_cfg.system_prompt_file, encoding="utf-8") as f:
                system_prompt = f.read()
        except FileNotFoundError:
            logger.warning(
                f"System prompt file not found: {llm_cfg.system_prompt_file}"
            )

    logger.info(f"Creating LLM engine: {llm_cfg.engine}")
    return engine_class(system_prompt=system_prompt, **resolved_config)


def create_tts_engine(tts_cfg: EngineConfig) -> BaseTTSEngine:
    """从配置创建 TTS 引擎实例"""
    engine_class = tts_registry.get(tts_cfg.engine)
    resolved_config = _resolve_env_vars(tts_cfg.config)
    logger.info(f"Creating TTS engine: {tts_cfg.engine}")
    return engine_class(**resolved_config)


def create_voicebot(config: VoiceBotConfig):
    """
    从完整配置创建 VoiceBot 所有引擎。

    返回一个包含所有已初始化引擎的 namedtuple。
    """
    from collections import namedtuple

    # 注册所有引擎（确保注册表已填满）
    import voicebot.engines  # noqa: F401

    VoiceBotEngines = namedtuple("VoiceBotEngines", ["asr", "llm", "tts"])

    return VoiceBotEngines(
        asr=create_asr_engine(config.asr),
        llm=create_llm_engine(config.llm),
        tts=create_tts_engine(config.tts),
    )
