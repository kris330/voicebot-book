
import asyncio
import pytest
from voicebot.emotion_parser import EmotionStreamParser
from voicebot.emotion import Emotion


async def stream_from_text(text: str):
    """把字符串模拟成流式输出（每次一个字符）"""
    for char in text:
        yield char


@pytest.mark.asyncio
async def test_emotion_tag_extracted():
    parser = EmotionStreamParser()
    llm_output = "[EMOTION:2] 太棒了，你做得很好！"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    assert parser.emotion == Emotion.HAPPY
    assert "[EMOTION" not in result
    assert "太棒了" in result


@pytest.mark.asyncio
async def test_no_emotion_tag_uses_default():
    parser = EmotionStreamParser(default_emotion=Emotion.NEUTRAL)
    llm_output = "好的，我来帮您处理这个问题。"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    assert parser.emotion == Emotion.NEUTRAL
    assert "好的" in result


@pytest.mark.asyncio
async def test_unknown_emotion_value():
    parser = EmotionStreamParser(default_emotion=Emotion.NEUTRAL)
    llm_output = "[EMOTION:99] 这是一个不存在的情感值。"

    result = ""
    async for chunk in parser.process_stream(stream_from_text(llm_output)):
        result += chunk

    # 应该回退到默认情感，而不是崩溃
    assert parser.emotion == Emotion.NEUTRAL
