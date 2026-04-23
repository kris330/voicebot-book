
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Optional


@dataclass
class ASRResult:
    """ASR 识别结果"""
    text: str                          # 识别文本
    is_final: bool                     # 是否为最终结果
    confidence: float = 1.0            # 置信度 [0, 1]
    words: list[dict] = None           # 词级时间戳（可选）
    language: str = "zh"               # 识别语言


class BaseASR(ABC):
    """ASR 抽象基类

    所有 ASR 实现都必须继承这个类，实现以下方法。
    """

    @abstractmethod
    async def init(self) -> None:
        """初始化（加载模型、建立连接等）"""
        ...

    @abstractmethod
    async def transcribe(self, audio: bytes | "np.ndarray") -> ASRResult:
        """
        对一段完整音频进行识别（非流式）

        Args:
            audio: int16 音频数据，16kHz 单声道

        Returns:
            ASRResult，is_final=True
        """
        ...

    @abstractmethod
    async def transcribe_stream(
        self, audio_generator: AsyncGenerator
    ) -> AsyncGenerator[ASRResult, None]:
        """
        流式识别（可选实现，不支持的子类可以抛出 NotImplementedError）

        Args:
            audio_generator: 异步生成器，持续产生音频帧

        Yields:
            ASRResult，中间结果 is_final=False，最终结果 is_final=True
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放资源"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """ASR 实现的名称（用于日志）"""
        ...
