
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
