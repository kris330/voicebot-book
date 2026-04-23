# 第 21 章：配置驱动架构

## 凌晨两点的线上故障

你收到报警，线上 TTS 服务延迟飙升。你需要立刻切换到备用的 TTS 引擎。

但是，切换逻辑硬编码在 `server.py` 里。你需要改代码、提交、走 CI、部署……半小时后才能切换完毕。

这半小时里，用户一直在体验卡顿的 VoiceBot。

这个场景说明了一个道理：**凡是可能变化的，都不应该硬编码在代码里**。

配置驱动（Config-Driven）架构的核心思想就是：**把"用什么"写进配置文件，把"怎么做"写进代码。** 切换模型只需要改一行配置，重启服务即可。

---

## 21.1 哪些东西应该放进配置

先做一个分类：

```
┌─────────────────────────────────────────────────────────┐
│                    VoiceBot 系统                         │
├──────────────────┬──────────────────────────────────────┤
│  放进代码        │  放进配置                            │
├──────────────────┼──────────────────────────────────────┤
│ 流水线逻辑       │ 使用哪个 ASR 引擎                    │
│ 错误处理策略     │ ASR 的 API 地址、模型名              │
│ 音频处理算法     │ 使用哪个 LLM                         │
│ 流式传输实现     │ LLM 的 temperature、max_tokens       │
│ WebSocket 协议   │ 使用哪个 TTS                         │
│ 情感解析逻辑     │ TTS 的 voice、speed 参数             │
│                  │ 服务监听端口                         │
│                  │ 日志级别                             │
│                  │ API Key（用环境变量）                │
└──────────────────┴──────────────────────────────────────┘
```

---

## 21.2 配置文件格式选择

常见的配置格式有 JSON、YAML、TOML。我们选择 **JSON**，原因：

- Python 标准库内置支持，无需额外依赖
- 结构严格，不容易写错（YAML 有很多坑）
- 方便用代码生成和修改
- 广泛被其他系统支持

不选 YAML 的原因：YAML 的缩进敏感性和特殊值处理（`yes` 会被解析成 `True`）是生产事故的常见来源。

---

## 21.3 配置结构设计

一个好的配置文件应该：层次清晰、分模块、有注释（JSON 不支持注释，我们用约定代替）。

### 完整配置文件示例

**config.json（全云端版）**

```json
{
  "_comment": "全云端版配置 - 使用 OpenAI + CosyVoice 云服务",
  "version": "1.0",

  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "cors_origins": ["*"]
  },

  "asr": {
    "engine": "openai_whisper",
    "config": {
      "model": "whisper-1",
      "language": "zh"
    }
  },

  "llm": {
    "engine": "openai_chat",
    "config": {
      "model": "gpt-4o-mini",
      "temperature": 0.7,
      "max_tokens": 500,
      "stream": true
    },
    "system_prompt_file": "prompts/default_system.txt"
  },

  "tts": {
    "engine": "openai_tts",
    "config": {
      "model": "tts-1",
      "default_voice": "alloy",
      "default_speed": 1.0,
      "response_format": "pcm"
    }
  },

  "emotion": {
    "enabled": true,
    "default_emotion": 3,
    "buffer_size": 20
  },

  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "chunk_size_ms": 100
  }
}
```

**config_local.json（全本地版）**

```json
{
  "_comment": "全本地版配置 - 所有模型在本地运行，无需 API Key",
  "version": "1.0",

  "server": {
    "host": "127.0.0.1",
    "port": 8080,
    "log_level": "DEBUG",
    "cors_origins": ["http://localhost:3000"]
  },

  "asr": {
    "engine": "sensevoice_local",
    "config": {
      "model_path": "./models/SenseVoiceSmall",
      "device": "cuda",
      "language": "auto"
    }
  },

  "llm": {
    "engine": "ollama",
    "config": {
      "base_url": "http://localhost:11434",
      "model": "qwen2.5:7b",
      "temperature": 0.7,
      "stream": true
    },
    "system_prompt_file": "prompts/default_system.txt"
  },

  "tts": {
    "engine": "cosyvoice_local",
    "config": {
      "model_path": "./models/CosyVoice2-0.5B",
      "device": "cuda",
      "default_voice": "中文女",
      "default_speed": 1.0
    }
  },

  "emotion": {
    "enabled": true,
    "default_emotion": 3,
    "buffer_size": 20
  },

  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "chunk_size_ms": 100
  }
}
```

**config_hybrid.json（混合版）**

```json
{
  "_comment": "混合版 - 本地 ASR/TTS + 云端 LLM",
  "version": "1.0",

  "server": {
    "host": "0.0.0.0",
    "port": 8080,
    "log_level": "INFO",
    "cors_origins": ["*"]
  },

  "asr": {
    "engine": "sensevoice_local",
    "config": {
      "model_path": "./models/SenseVoiceSmall",
      "device": "cpu",
      "language": "zh"
    }
  },

  "llm": {
    "engine": "openai_chat",
    "config": {
      "model": "gpt-4o",
      "temperature": 0.8,
      "max_tokens": 800,
      "stream": true
    },
    "system_prompt_file": "prompts/default_system.txt"
  },

  "tts": {
    "engine": "cosyvoice_grpc",
    "config": {
      "endpoint": "localhost:50051",
      "default_voice": "中文女",
      "default_speed": 1.0
    }
  },

  "emotion": {
    "enabled": true,
    "default_emotion": 3,
    "buffer_size": 20
  },

  "audio": {
    "sample_rate": 16000,
    "channels": 1,
    "chunk_size_ms": 100
  }
}
```

---

## 21.4 配置数据类

用 Python dataclass 来表示配置，这样有类型提示，IDE 可以自动补全：

```python
# voicebot/config.py

from __future__ import annotations
import json
import os
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ("*",)


@dataclass(frozen=True)
class EngineConfig:
    """某个引擎（ASR/LLM/TTS）的配置"""
    engine: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMConfig:
    engine: str
    config: dict[str, Any] = field(default_factory=dict)
    system_prompt_file: str = "prompts/default_system.txt"


@dataclass(frozen=True)
class EmotionConfig:
    enabled: bool = True
    default_emotion: int = 3
    buffer_size: int = 20


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size_ms: int = 100


@dataclass(frozen=True)
class VoiceBotConfig:
    """VoiceBot 完整配置"""
    server: ServerConfig
    asr: EngineConfig
    llm: LLMConfig
    tts: EngineConfig
    emotion: EmotionConfig
    audio: AudioConfig
    version: str = "1.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceBotConfig:
        """从字典创建配置对象"""
        return cls(
            version=data.get("version", "1.0"),
            server=ServerConfig(**data.get("server", {})),
            asr=EngineConfig(**data.get("asr", {"engine": "openai_whisper"})),
            llm=LLMConfig(**data.get("llm", {"engine": "openai_chat"})),
            tts=EngineConfig(**data.get("tts", {"engine": "openai_tts"})),
            emotion=EmotionConfig(**data.get("emotion", {})),
            audio=AudioConfig(**data.get("audio", {})),
        )

    @classmethod
    def from_file(cls, path: str) -> VoiceBotConfig:
        """从 JSON 文件加载配置"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded config from: {path}")
        return cls.from_dict(data)
```

---

## 21.5 模型注册表

注册表（Registry）是配置驱动的关键机制：**字符串名称 → 类的映射**。

```python
# voicebot/registry.py

from typing import Any, TypeVar
import logging

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Registry:
    """
    模型注册表：把字符串名称映射到具体的类。

    用法：
        registry = Registry("ASR")
        registry.register("openai_whisper", OpenAIWhisperASR)
        asr_class = registry.get("openai_whisper")
        asr = asr_class(**config)
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._classes: dict[str, type] = {}

    def register(self, name: str, cls: type) -> None:
        if name in self._classes:
            logger.warning(
                f"[{self._name}] Overriding existing registration for '{name}'"
            )
        self._classes[name] = cls
        logger.debug(f"[{self._name}] Registered '{name}' -> {cls.__name__}")

    def get(self, name: str) -> type:
        if name not in self._classes:
            available = list(self._classes.keys())
            raise ValueError(
                f"[{self._name}] Unknown engine '{name}'. "
                f"Available: {available}"
            )
        return self._classes[name]

    def list_all(self) -> list[str]:
        return list(self._classes.keys())


# 全局注册表
asr_registry = Registry("ASR")
llm_registry = Registry("LLM")
tts_registry = Registry("TTS")
```

注册各个引擎：

```python
# voicebot/engines/__init__.py
# 在这里统一注册所有引擎，保证注册表在启动时已填满

from ..registry import asr_registry, llm_registry, tts_registry

# ── ASR 引擎 ──────────────────────────────────────────
from .asr.openai_whisper import OpenAIWhisperASR
from .asr.sensevoice_local import SenseVoiceLocalASR

asr_registry.register("openai_whisper",   OpenAIWhisperASR)
asr_registry.register("sensevoice_local", SenseVoiceLocalASR)

# ── LLM 引擎 ──────────────────────────────────────────
from .llm.openai_chat import OpenAIChatLLM
from .llm.ollama import OllamaLLM

llm_registry.register("openai_chat", OpenAIChatLLM)
llm_registry.register("ollama",      OllamaLLM)

# ── TTS 引擎 ──────────────────────────────────────────
from .tts.openai_tts import OpenAITTSEngine
from .tts.cosyvoice_local import CosyVoiceLocalTTS
from .tts.cosyvoice_grpc import CosyVoiceGRPCTTS

tts_registry.register("openai_tts",      OpenAITTSEngine)
tts_registry.register("cosyvoice_local", CosyVoiceLocalTTS)
tts_registry.register("cosyvoice_grpc",  CosyVoiceGRPCTTS)
```

---

## 21.6 工厂函数：从配置创建系统

工厂函数负责读取配置、查注册表、实例化具体引擎：

```python
# voicebot/factory.py

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
```

---

## 21.7 环境变量支持

API Key 等敏感信息绝对不能写进配置文件（配置文件会被提交到 Git）。

**配置文件中的写法：**

```json
{
  "asr": {
    "engine": "openai_whisper",
    "config": {
      "api_key": "${OPENAI_API_KEY}",
      "model": "whisper-1"
    }
  },
  "llm": {
    "engine": "openai_chat",
    "config": {
      "api_key": "${OPENAI_API_KEY}",
      "model": "gpt-4o-mini"
    }
  },
  "tts": {
    "engine": "cosyvoice_grpc",
    "config": {
      "endpoint": "${TTS_ENDPOINT:localhost:50051}"
    }
  }
}
```

**启动时设置环境变量：**

```bash
# .env 文件（加入 .gitignore，不提交）
OPENAI_API_KEY=sk-proj-xxxxxxxx
TTS_ENDPOINT=tts.internal.company.com:50051
```

```python
# voicebot/main.py 入口点

from dotenv import load_dotenv
load_dotenv()  # 从 .env 文件加载环境变量
```

---

## 21.8 配置验证

启动时验证配置，比运行到一半崩溃要好得多：

```python
# voicebot/config_validator.py

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
```

### 把验证集成到启动流程

```python
# voicebot/main.py

import asyncio
import argparse
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

from .config import VoiceBotConfig
from .config_validator import validate_config
from .factory import create_voicebot
import voicebot.engines  # 触发引擎注册


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceBot Server")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config file (default: config.json)"
    )
    return parser.parse_args()


def main() -> None:
    # 加载环境变量
    load_dotenv()

    args = parse_args()

    # 加载配置
    try:
        config = VoiceBotConfig.from_file(args.config)
    except FileNotFoundError:
        print(f"Error: Config file not found: {args.config}")
        raise SystemExit(1)
    except Exception as e:
        print(f"Error: Failed to parse config: {e}")
        raise SystemExit(1)

    # 设置日志
    setup_logging(config.server.log_level)
    logger = logging.getLogger(__name__)

    # 验证配置
    validation = validate_config(config)
    validation.log_warnings()
    validation.raise_if_invalid()

    logger.info(f"Config loaded: ASR={config.asr.engine}, "
                f"LLM={config.llm.engine}, TTS={config.tts.engine}")

    # 创建引擎
    engines = create_voicebot(config)
    logger.info("All engines initialized successfully")

    # 启动服务器
    from .server import create_app
    app = create_app(config, engines)

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
```

---

## 21.9 运行时切换模型（高级功能）

在不重启服务的情况下切换模型，适用于 A/B 测试或紧急降级：

```python
# voicebot/model_switcher.py

import asyncio
import logging
from typing import Any
from .config import EngineConfig
from .factory import create_tts_engine, create_llm_engine
from .engines.tts.base import BaseTTSEngine
from .engines.llm.base import BaseLLMEngine

logger = logging.getLogger(__name__)


class ModelSwitcher:
    """
    支持运行时切换 TTS / LLM 引擎。

    使用 asyncio.Lock 保证切换过程的原子性：
    - 新请求等待切换完成
    - 进行中的请求继续使用旧引擎直到完成
    """

    def __init__(
        self,
        initial_tts: BaseTTSEngine,
        initial_llm: BaseLLMEngine,
    ) -> None:
        self._tts = initial_tts
        self._llm = initial_llm
        self._lock = asyncio.Lock()

    @property
    def tts(self) -> BaseTTSEngine:
        return self._tts

    @property
    def llm(self) -> BaseLLMEngine:
        return self._llm

    async def switch_tts(self, new_config: EngineConfig) -> None:
        """切换 TTS 引擎（会等待 lock，不中断进行中的请求）"""
        async with self._lock:
            logger.info(f"Switching TTS to: {new_config.engine}")
            old_engine = self._tts
            self._tts = create_tts_engine(new_config)
            logger.info(f"TTS switched successfully")
            # 如果旧引擎有清理方法，调用它
            if hasattr(old_engine, "close"):
                await old_engine.close()

    async def switch_llm(self, new_config: EngineConfig) -> None:
        """切换 LLM 引擎"""
        async with self._lock:
            logger.info(f"Switching LLM to: {new_config.engine}")
            old_engine = self._llm
            self._llm = create_llm_engine(new_config)
            logger.info(f"LLM switched successfully")
            if hasattr(old_engine, "close"):
                await old_engine.close()
```

提供管理 API（只在内部网络暴露）：

```python
# voicebot/admin_api.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from .model_switcher import ModelSwitcher
from .config import EngineConfig

router = APIRouter(prefix="/admin", tags=["admin"])


class SwitchEngineRequest(BaseModel):
    engine: str
    config: dict = {}


@router.post("/switch/tts")
async def switch_tts(
    request: SwitchEngineRequest,
    switcher: ModelSwitcher = Depends(get_switcher),
) -> dict:
    """切换 TTS 引擎（管理接口，仅限内网）"""
    try:
        await switcher.switch_tts(
            EngineConfig(engine=request.engine, config=request.config)
        )
        return {"status": "ok", "engine": request.engine}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Switch failed: {e}")


@router.get("/engines")
async def list_engines() -> dict:
    """查看所有可用引擎"""
    from .registry import asr_registry, llm_registry, tts_registry
    return {
        "asr": asr_registry.list_all(),
        "llm": llm_registry.list_all(),
        "tts": tts_registry.list_all(),
    }
```

---

## 21.10 项目目录结构

配置驱动架构下，项目目录长这样：

```
voicebot/
├── config.json              ← 默认配置（提交到 Git）
├── config_local.json        ← 本地开发配置（提交到 Git）
├── config_prod.json         ← 生产配置（不含 Key，提交到 Git）
├── .env                     ← API Key 等（加入 .gitignore）
├── .env.example             ← 环境变量示例（提交到 Git）
├── prompts/
│   ├── default_system.txt   ← 默认 system prompt
│   └── mental_health.txt    ← 特定场景的 system prompt
├── voicebot/
│   ├── __init__.py
│   ├── main.py              ← 入口点
│   ├── config.py            ← 配置数据类
│   ├── config_validator.py  ← 配置验证
│   ├── registry.py          ← 引擎注册表
│   ├── factory.py           ← 工厂函数
│   ├── model_switcher.py    ← 运行时切换
│   ├── emotion.py           ← 情感定义
│   ├── emotion_parser.py    ← 情感解析
│   ├── emotion_pipeline.py  ← 情感流水线
│   ├── server.py            ← WebSocket 服务器
│   ├── admin_api.py         ← 管理接口
│   └── engines/
│       ├── __init__.py      ← 引擎注册
│       ├── asr/
│       │   ├── base.py
│       │   ├── openai_whisper.py
│       │   └── sensevoice_local.py
│       ├── llm/
│       │   ├── base.py
│       │   ├── openai_chat.py
│       │   └── ollama.py
│       └── tts/
│           ├── base.py
│           ├── openai_tts.py
│           ├── cosyvoice_local.py
│           └── cosyvoice_grpc.py
```

`.env.example` 文件（必须提交，方便协作）：

```bash
# .env.example - 复制为 .env 并填入实际值

# OpenAI API Key（云端 ASR/LLM/TTS 需要）
OPENAI_API_KEY=sk-proj-your-key-here

# CosyVoice gRPC 服务地址（使用 cosyvoice_grpc 引擎时需要）
TTS_ENDPOINT=localhost:50051

# 其他云服务配置（按需填写）
# AZURE_SPEECH_KEY=
# AZURE_SPEECH_REGION=
```

---

## 21.11 一个完整的启动流程演示

```bash
# 使用本地配置启动
python -m voicebot --config config_local.json

# 使用云端配置启动
OPENAI_API_KEY=sk-xxx python -m voicebot --config config.json

# 使用混合配置启动（部分参数在 .env 中）
cp .env.example .env
# 编辑 .env 填入实际 Key
python -m voicebot --config config_hybrid.json
```

启动时会看到这样的日志：

```
2024-01-15 10:23:01 [INFO] voicebot.config: Loaded config from: config_local.json
2024-01-15 10:23:01 [INFO] voicebot.registry: [ASR] Registered 'sensevoice_local' -> SenseVoiceLocalASR
2024-01-15 10:23:01 [INFO] voicebot.registry: [LLM] Registered 'ollama' -> OllamaLLM
2024-01-15 10:23:01 [INFO] voicebot.registry: [TTS] Registered 'cosyvoice_local' -> CosyVoiceLocalTTS
2024-01-15 10:23:01 [INFO] voicebot.main: Config loaded: ASR=sensevoice_local, LLM=ollama, TTS=cosyvoice_local
2024-01-15 10:23:01 [INFO] voicebot.factory: Creating ASR engine: sensevoice_local
2024-01-15 10:23:03 [INFO] voicebot.factory: Creating LLM engine: ollama
2024-01-15 10:23:03 [INFO] voicebot.factory: Creating TTS engine: cosyvoice_local
2024-01-15 10:23:05 [INFO] voicebot.main: All engines initialized successfully
2024-01-15 10:23:05 [INFO] uvicorn: Application startup complete.
```

---

## 本章小结

本章我们为 VoiceBot 建立了配置驱动架构：

1. **配置文件设计**：用 JSON 格式，按模块（server/asr/llm/tts/emotion/audio）组织，提供了云端版、本地版、混合版三套示例配置。

2. **配置数据类**：用 frozen dataclass 表示配置，保证不可变性，同时获得 IDE 类型提示支持。

3. **模型注册表**：`Registry` 类实现字符串名称到类的映射，新增引擎只需在 `engines/__init__.py` 中注册一行。

4. **工厂函数**：`create_voicebot()` 统一处理注册表查找、环境变量替换、引擎实例化，调用方不关心具体实现。

5. **环境变量支持**：配置文件中用 `${VAR_NAME}` 引用环境变量，API Key 放在 `.env` 文件中，不提交到 Git。

6. **启动验证**：`validate_config()` 在启动时检查引擎是否注册、路径是否存在，比运行中崩溃好得多。

7. **运行时切换**：`ModelSwitcher` 配合管理 API，实现不重启切换引擎，用于紧急降级和 A/B 测试。

现在 VoiceBot 既能处理情感，又能灵活配置。最后一章，我们来把它真正部署到生产环境。
