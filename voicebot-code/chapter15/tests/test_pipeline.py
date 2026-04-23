
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from voicebot.pipeline import Pipeline, PipelineConfig, SessionPipeline


@pytest.fixture
def mock_asr():
    """模拟 ASR 引擎"""
    asr = MagicMock()
    asr.transcribe = AsyncMock(return_value="你好，VoiceBot")
    return asr


@pytest.fixture
def mock_llm():
    """模拟 LLM 引擎（流式）"""
    llm = MagicMock()

    async def mock_stream(messages, system_prompt=""):
        for token in ["你好", "！", "很高兴", "认识你", "。"]:
            yield token

    llm.generate_stream = mock_stream
    return llm


@pytest.fixture
def mock_tts():
    """模拟 TTS 引擎（流式）"""
    tts = MagicMock()

    async def mock_synth_stream(text):
        # 返回一些假的音频数据
        yield bytes(1024)
        yield bytes(512)

    tts.synthesize_stream = mock_synth_stream
    tts.get_sample_rate = MagicMock(return_value=16000)
    return tts


@pytest.fixture
def pipeline(mock_asr, mock_llm, mock_tts):
    config = PipelineConfig(
        asr_engine="mock",
        llm_engine="mock",
        tts_engine="mock",
        system_prompt="你是测试助手",
    )
    return Pipeline(
        config=config,
        asr=mock_asr,
        llm=mock_llm,
        tts=mock_tts,
    )


@pytest.mark.asyncio
async def test_session_pipeline_clone(pipeline):
    """测试 clone 创建独立会话"""
    session_a = pipeline.clone("session-a")
    session_b = pipeline.clone("session-b")

    # 不同会话
    assert session_a.session_id != session_b.session_id

    # 但共享同一个引擎
    assert session_a.asr is session_b.asr
    assert session_a.llm is session_b.llm
    assert session_a.tts is session_b.tts


@pytest.mark.asyncio
async def test_conversation_history_isolation(pipeline):
    """测试会话历史隔离"""
    session_a = pipeline.clone("session-a")
    session_b = pipeline.clone("session-b")

    session_a.history.add_user("A 的消息")

    # B 的历史不受影响
    assert len(session_b.history.get()) == 0
    assert len(session_a.history.get()) == 1


@pytest.mark.asyncio
async def test_process_user_input(pipeline):
    """测试完整的用户输入处理流程"""
    session = pipeline.clone("test-session")

    audio_chunks = []
    audio_gen = await session.process_user_input("你好")
    async for chunk in audio_gen:
        audio_chunks.append(chunk)

    # 应该收到了音频数据
    assert len(audio_chunks) > 0

    # 对话历史应该被记录
    history = session.history.get()
    assert len(history) >= 2  # 用户消息 + 助手回复
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "你好"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_interrupt(pipeline):
    """测试打断机制"""
    session = pipeline.clone("test-session")

    chunks_before_interrupt = []
    audio_gen = await session.process_user_input("你好")

    count = 0
    async for chunk in audio_gen:
        chunks_before_interrupt.append(chunk)
        count += 1
        if count == 1:
            session.interrupt()  # 收到第一个音频块后打断
            break

    # 打断后停止
    assert session.is_interrupted


@pytest.mark.asyncio
async def test_history_max_turns(pipeline):
    """测试对话历史长度限制"""
    session = pipeline.clone("test-session")
    session.history.max_turns = 3  # 只保留 3 轮

    for i in range(5):
        session.history.add_user(f"用户消息 {i}")
        session.history.add_assistant(f"助手回复 {i}")

    history = session.history.get()
    # 应该被截断到最近 3 轮（6 条消息）
    assert len(history) <= 6


def test_pipeline_config_from_dict():
    """测试从字典创建配置"""
    data = {
        "asr": {"engine": "sensevoice", "config": {"device": "cpu"}},
        "llm": {
            "engine": "openai",
            "config": {"api_key": "test-key", "model": "gpt-4o-mini"},
            "system_prompt": "测试系统提示",
        },
        "tts": {
            "engine": "kokoro",
            "config": {"voice": "zf_xiaobei"},
            "target_sample_rate": 16000,
        },
    }

    config = PipelineConfig.from_dict(data)
    assert config.asr_engine == "sensevoice"
    assert config.llm_engine == "openai"
    assert config.tts_engine == "kokoro"
    assert config.system_prompt == "测试系统提示"
    assert config.target_sample_rate == 16000
