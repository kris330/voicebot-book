
import asyncio
from dataclasses import dataclass, field


@dataclass
class ConversationHistory:
    """对话历史"""
    messages: list[dict] = field(default_factory=list)
    max_turns: int = 20  # 最多保留的对话轮数

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self._trim()

    def get(self) -> list[dict]:
        return list(self.messages)

    def clear(self) -> None:
        self.messages.clear()

    def _trim(self) -> None:
        """保持对话历史在限制范围内"""
        max_messages = self.max_turns * 2  # 每轮 2 条消息
        if len(self.messages) > max_messages:
            # 保留最新的消息
            self.messages = self.messages[-max_messages:]


class SessionPipeline:
    """
    会话级别的处理管道

    每个用户会话独有一个 SessionPipeline 实例。
    共享底层引擎（ASR/LLM/TTS），但有独立的：
    - 对话历史
    - 当前状态
    - 配置覆盖（比如用户选择的 TTS 声音）
    """

    def __init__(
        self,
        pipeline: "Pipeline",
        session_id: str,
    ) -> None:
        self._pipeline = pipeline
        self.session_id = session_id
        self.history = ConversationHistory()
        self._is_speaking = False  # TTS 是否正在播放
        self._interrupt_flag = asyncio.Event()

    @property
    def asr(self) -> ASREngine:
        return self._pipeline.asr

    @property
    def llm(self) -> LLMEngine:
        return self._pipeline.llm

    @property
    def tts(self) -> TTSEngine:
        return self._pipeline.tts

    @property
    def system_prompt(self) -> str:
        return self._pipeline.config.system_prompt

    def interrupt(self) -> None:
        """打断当前 TTS 播放"""
        self._interrupt_flag.set()

    def reset_interrupt(self) -> None:
        """重置打断标志"""
        self._interrupt_flag.clear()

    @property
    def is_interrupted(self) -> bool:
        return self._interrupt_flag.is_set()

    async def process_user_input(
        self,
        user_text: str,
    ) -> "AsyncIterator[bytes]":
        """
        处理用户输入，返回 TTS 音频流

        完整流程：
        1. 记录用户输入到历史
        2. 调用 LLM 生成回复
        3. （可选）文本改写
        4. 流式 TTS 合成
        5. 记录回复到历史
        """
        self.reset_interrupt()
        self.history.add_user(user_text)

        # 收集完整回复（用于保存到历史）
        full_response = ""

        async def audio_generator():
            nonlocal full_response

            sentence_buffer = ""
            from voicebot.tts.text_processor import SentenceSplitter, TextPreprocessor
            splitter = SentenceSplitter()
            preprocessor = TextPreprocessor()

            async for token in self._pipeline.llm.generate_stream(
                messages=self.history.get(),
                system_prompt=self.system_prompt,
            ):
                if self.is_interrupted:
                    break

                full_response += token
                sentence_buffer += token

                # 检查是否有完整句子可以合成
                sentences = splitter.split(sentence_buffer)
                if len(sentences) > 1:
                    for sentence in sentences[:-1]:
                        if sentence.strip() and not self.is_interrupted:
                            clean = preprocessor.process(sentence)
                            async for chunk in self._pipeline.tts.synthesize_stream(clean):
                                if self.is_interrupted:
                                    return
                                yield chunk
                    sentence_buffer = sentences[-1]

            # 处理最后剩余的文字
            if sentence_buffer.strip() and not self.is_interrupted:
                clean = preprocessor.process(sentence_buffer)
                async for chunk in self._pipeline.tts.synthesize_stream(clean):
                    if self.is_interrupted:
                        return
                    yield chunk

            # 保存完整回复到历史
            if full_response:
                self.history.add_assistant(full_response)

        return audio_generator()
