
from ..emotion import Emotion, EmotionConfig

# CosyVoice 支持的情感指令
COSYVOICE_STYLE_MAP: dict[str, str] = {
    "calm":       "<|CALM|>",
    "gentle":     "<|GENTLE|>",
    "cheerful":   "<|CHEERFUL|>",
    "default":    "",
    "serious":    "<|SERIOUS|>",
    "excited":    "<|EXCITED|>",
}

def wrap_text_with_emotion(text: str, config: EmotionConfig) -> str:
    """
    用 CosyVoice 的情感标签包装文本。
    不同 TTS 引擎格式不同，这是 CosyVoice 的格式。
    """
    style_tag = COSYVOICE_STYLE_MAP.get(config.voice_style, "")
    if style_tag:
        return f"{style_tag}{text}"
    return text


class CosyVoiceTTSWithEmotion:
    def __init__(self, grpc_endpoint: str) -> None:
        self._endpoint = grpc_endpoint
        # 初始化 gRPC channel（省略具体实现）

    async def synthesize_stream(
        self,
        text: str,
        emotion_config: EmotionConfig,
    ) -> AsyncIterator[bytes]:
        """带情感控制的流式合成"""
        wrapped_text = wrap_text_with_emotion(text, emotion_config)
        speed = emotion_config.speed

        # 调用 gRPC 接口（省略具体实现，参考第 7 章）
        async for audio_chunk in self._grpc_synthesize(
            text=wrapped_text,
            speed=speed,
        ):
            yield audio_chunk
