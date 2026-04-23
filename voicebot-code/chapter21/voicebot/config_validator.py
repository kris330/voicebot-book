
import os
import logging
from dataclasses import dataclass
from .config import VoiceBotConfig
from .registry import asr_registry, llm_registry, tts_registry

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            error_text = "\n".join(f"  - {e}" for e in self.errors)
            raise ValueError(f"Configuration validation failed:\n{error_text}")

    def log_warnings(self) -> None:
        for warning in self.warnings:
            logger.warning(f"Config warning: {warning}")


def validate_config(config: VoiceBotConfig) -> ValidationResult:
    """
    验证配置文件的合法性。
    在系统启动时调用，发现问题早于运行时错误。
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 检查引擎名称是否已注册
    if config.asr.engine not in asr_registry.list_all():
        errors.append(
            f"Unknown ASR engine: '{config.asr.engine}'. "
            f"Available: {asr_registry.list_all()}"
        )

    if config.llm.engine not in llm_registry.list_all():
        errors.append(
            f"Unknown LLM engine: '{config.llm.engine}'. "
            f"Available: {llm_registry.list_all()}"
        )

    if config.tts.engine not in tts_registry.list_all():
        errors.append(
            f"Unknown TTS engine: '{config.tts.engine}'. "
            f"Available: {tts_registry.list_all()}"
        )

    # 检查端口范围
    if not (1024 <= config.server.port <= 65535):
        errors.append(
            f"Invalid server port: {config.server.port}. Must be 1024-65535"
        )

    # 检查日志级别
    valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if config.server.log_level.upper() not in valid_log_levels:
        errors.append(
            f"Invalid log_level: '{config.server.log_level}'. "
            f"Must be one of {valid_log_levels}"
        )

    # 检查情感配置
    if config.emotion.enabled:
        if not (0 <= config.emotion.default_emotion <= 9):
            errors.append(
                f"Invalid default_emotion: {config.emotion.default_emotion}. Must be 0-9"
            )

    # 检查 system prompt 文件是否存在（警告而非错误）
    if config.llm.system_prompt_file:
        if not os.path.exists(config.llm.system_prompt_file):
            warnings.append(
                f"System prompt file not found: {config.llm.system_prompt_file}. "
                f"Will use empty system prompt."
            )

    # 检查本地模型路径
    if "local" in config.asr.engine:
        model_path = config.asr.config.get("model_path", "")
        if model_path and not os.path.exists(model_path):
            errors.append(
                f"ASR model path not found: '{model_path}'"
            )

    if "local" in config.tts.engine:
        model_path = config.tts.config.get("model_path", "")
        if model_path and not os.path.exists(model_path):
            errors.append(
                f"TTS model path not found: '{model_path}'"
            )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
