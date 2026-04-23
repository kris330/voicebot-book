
import json
import logging
from pathlib import Path

from .pipeline import Pipeline, PipelineConfig
from .registry import create_asr, create_llm, create_tts

logger = logging.getLogger(__name__)


class PipelineFactory:
    """
    Pipeline 工厂

    负责从配置（dict 或 JSON 文件）创建 Pipeline 实例。
    """

    @classmethod
    def from_config(cls, config: PipelineConfig) -> Pipeline:
        """
        从 PipelineConfig 对象创建 Pipeline

        这是创建 Pipeline 的核心方法。
        """
        logger.info("开始创建 Pipeline...")

        # 创建 ASR 引擎
        logger.info(f"加载 ASR 引擎：{config.asr_engine}")
        asr = create_asr(config.asr_engine, config.asr_config)

        # 创建 LLM 引擎
        logger.info(f"加载 LLM 引擎：{config.llm_engine}")
        llm = create_llm(config.llm_engine, config.llm_config)

        # 创建 TTS 引擎
        logger.info(f"加载 TTS 引擎：{config.tts_engine}")
        tts = create_tts(config.tts_engine, config.tts_config)

        # 可选：创建文本改写器
        rewriter = None
        if config.rewriter_engine:
            from .registry import _tts_registry  # 复用注册表概念
            logger.info(f"加载改写器：{config.rewriter_engine}")

        logger.info("Pipeline 创建完成")
        return Pipeline(
            config=config,
            asr=asr,
            llm=llm,
            tts=tts,
            rewriter=rewriter,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Pipeline:
        """从字典创建 Pipeline"""
        config = PipelineConfig.from_dict(data)
        return cls.from_config(config)

    @classmethod
    def from_json_file(cls, path: str | Path) -> Pipeline:
        """从 JSON 文件创建 Pipeline"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在：{path}")

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        logger.info(f"从配置文件创建 Pipeline：{path}")
        return cls.from_dict(data)
