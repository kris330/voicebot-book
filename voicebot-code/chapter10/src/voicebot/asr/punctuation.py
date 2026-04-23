
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PunctuationRestorer:
    """
    使用 FunASR CT-Transformer 模型添加标点

    模型：ct-transformer-zh-cn-punct
    """

    def __init__(self):
        self._model = None

    async def init(self):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_model)
        logger.info("标点恢复模型加载完成")

    def _load_model(self):
        from funasr import AutoModel
        self._model = AutoModel(
            model="ct-transformer-zh-cn-punct",
            disable_log=True,
        )

    async def restore(self, text: str) -> str:
        """为无标点文本添加标点"""
        if not text.strip():
            return text

        if self._model is None:
            return text  # 未初始化时直接返回原文

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model.generate(input=text)
        )

        if result and result[0].get("text"):
            return result[0]["text"]
        return text


# 简单的规则后处理（不依赖模型，速度快）
def simple_punctuation_fix(text: str) -> str:
    """
    基于规则的简单标点修复
    适合对延迟极其敏感的场景（模型方式需要额外 50-200ms）
    """
    if not text:
        return text

    text = text.strip()

    # 句末没有标点时，根据疑问词判断加什么标点
    question_words = ["吗", "呢", "啊", "嘛", "吧", "么", "什么", "怎么", "哪里", "谁"]
    ends_with_question = any(text.endswith(w) for w in question_words)

    if not text[-1] in "。？！，、；：.?!":
        if ends_with_question:
            text += "？"
        else:
            text += "。"

    return text
