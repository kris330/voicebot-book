# 第 19 章：中断处理

## 最让人抓狂的体验

你有没有遇到过这种情况：打客服电话，AI 在巴拉巴拉说一堆你不需要的内容，你想插话，但它根本不停。你只能坐在那里等它说完，然后才能说你想说的。

这就是没有打断功能的语音系统。

好的语音助手应该像和真人对话一样——你开口，对方就停下来听你说。不需要你等它说完，不需要你按什么键。

这个能力叫 **Barge-in（打断）**。

---

## 19.1 打断的完整流程

表面上看，打断很简单：用户说话了，AI 停止说话。

但实现起来需要客户端和服务端**同时**做响应：

```
用户开口说话
     │
     ▼
┌────────────────────────────────────────────────────────┐
│                      客户端                              │
│                                                          │
│  ① VAD 检测到用户开始说话                                │
│  ② 立刻停止当前 TTS 音频播放（清空播放队列）              │
│  ③ 发送 interrupt 消息给服务端                           │
│  ④ 继续录音，积累用户的新音频                             │
└──────────────────────────┬─────────────────────────────┘
                           │ WebSocket
                           │ {"type": "interrupt"}
                           ▼
┌────────────────────────────────────────────────────────┐
│                      服务端                              │
│                                                          │
│  ⑤ 收到 interrupt 消息                                   │
│  ⑥ 取消当前 LLM 生成任务（asyncio.Task.cancel()）         │
│  ⑦ 取消当前 TTS 合成任务                                  │
│  ⑧ 清空 TTS 音频队列（丢弃未发送的音频）                   │
│  ⑨ 切换到"接听"状态，等待用户新的语音                     │
└────────────────────────────────────────────────────────┘
     │
     ▼
用户说完新的话 → 重新触发 ASR → LLM → TTS 流水线
```

关键点：**客户端和服务端必须几乎同时响应**。

如果只有客户端停止播放，但服务端继续生成和发送音频，会造成：
- 服务端不断往队列塞音频，内存增长
- 用户说完新话后，服务端处于混乱状态（旧任务和新任务并行）

如果只有服务端取消任务，但客户端没停播放，用户会听到一段残余的 AI 语音。

---

## 19.2 为什么打断很难

难点一：**时序问题**

```
客户端时间线：
t=0    AI 开始说话
t=1.5  用户开口（VAD 检测到）
t=1.5  客户端停止播放
t=1.5  客户端发送 interrupt

服务端时间线：
t=0    开始 LLM 生成
t=0.3  LLM 第一句出来
t=0.3  TTS 开始合成
t=0.3  音频开始入队
...
t=1.55 收到 interrupt（网络延迟 50ms）
t=1.55 取消任务，清空队列
```

在 t=1.5 到 t=1.55 这 50ms 里，服务端还在往队列塞音频，客户端已经停止播放了。这些音频会被丢弃——这是正确的。

难点二：**asyncio 任务取消的正确姿势**

Python 的 `asyncio.Task.cancel()` 不是立刻终止任务，而是在下一个 `await` 点注入 `CancelledError`。如果代码里有不响应取消的循环，任务可能不会立刻停止。

难点三：**LLM 流式生成的取消**

LLM 流式生成通常是一个 HTTP 长连接，`asyncio.CancelledError` 需要能正确关闭这个连接。

难点四：**边界情况**

打断信号到达服务端时，如果 TTS 已经全部发完了怎么办？这时应该忽略打断（已经结束了）还是清空状态（为下一轮做准备）？

---

## 19.3 客户端代码：停止播放 + 发送 interrupt

```javascript
// frontend/index.html（打断相关部分）

// ============================================================
// 打断状态管理
// ============================================================
let isAISpeaking = false;       // AI 是否正在说话
let audioPlayQueue = [];        // TTS 音频播放队列
let currentSource = null;       // 当前播放的 AudioBufferSourceNode
let playContext = null;         // AudioContext

// ============================================================
// AI 开始/结束说话时更新状态
// ============================================================
function onAIStartSpeaking() {
    isAISpeaking = true;
    setStatus("AI 正在说话（开口可打断）");
    document.getElementById("mic-btn").classList.add("ai-speaking");
}

function onAIStopSpeaking() {
    isAISpeaking = false;
    setStatus("你可以说话了");
    document.getElementById("mic-btn").classList.remove("ai-speaking");
}

// ============================================================
// VAD：检测到用户开始说话
// ============================================================
function onVADStart() {
    // 如果 AI 正在说话，立刻打断
    if (isAISpeaking) {
        interruptAI();
    }
    // 开始录音
    startRecordingInternal();
}

// ============================================================
// 打断函数：停止播放 + 通知服务端
// ============================================================
function interruptAI() {
    console.log("打断 AI");

    // 1. 停止当前正在播放的音频
    if (currentSource) {
        try {
            currentSource.onended = null;  // 取消 onended 回调，防止触发 playNext
            currentSource.stop();
        } catch (e) {
            // stop() 可能因为已经结束而抛出错误，忽略
        }
        currentSource = null;
    }

    // 2. 清空播放队列
    audioPlayQueue = [];

    // 3. 更新状态
    isAISpeaking = false;

    // 4. 通知服务端
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "interrupt" }));
        console.log("已发送 interrupt 给服务端");
    }
}

// ============================================================
// 接收 TTS 音频
// ============================================================
ws.onmessage = async (event) => {
    if (event.data instanceof Blob) {
        // 收到第一帧音频时标记 AI 开始说话
        if (!isAISpeaking) {
            onAIStartSpeaking();
        }

        const arrayBuffer = await event.data.arrayBuffer();
        audioPlayQueue.push(arrayBuffer);

        if (!isCurrentlyPlaying()) {
            playNext();
        }
    } else {
        const data = JSON.parse(event.data);

        if (data.type === "tts_end") {
            // TTS 队列播完后 AI 说话结束
            // 注意：不能在这里立刻 onAIStopSpeaking，因为队列里可能还有音频
            // 应该等队列播完后再标记
            scheduleAIStopAfterQueue();
        } else if (data.type === "asr_result") {
            appendMessage("user", data.text);
        }
    }
};

function isCurrentlyPlaying() {
    return currentSource !== null;
}

function scheduleAIStopAfterQueue() {
    // 在队列全部播放完毕后调用 onAIStopSpeaking
    // playNext 中的 onended 会继续处理
    // 我们在 playNext 里加一个标志位
    queueEnded = true;
}

let queueEnded = false;

async function playNext() {
    if (audioPlayQueue.length === 0) {
        currentSource = null;
        if (queueEnded) {
            queueEnded = false;
            onAIStopSpeaking();
        }
        return;
    }

    const buffer = audioPlayQueue.shift();

    if (!playContext || playContext.state === "closed") {
        playContext = new AudioContext({ sampleRate: 24000 });
    }

    try {
        // 解码 PCM 数据
        const pcm = new Int16Array(buffer);
        const float32 = new Float32Array(pcm.length);
        for (let i = 0; i < pcm.length; i++) {
            float32[i] = pcm[i] / 32768.0;
        }

        const audioBuffer = playContext.createBuffer(1, float32.length, 24000);
        audioBuffer.copyToChannel(float32, 0);

        currentSource = playContext.createBufferSource();
        currentSource.buffer = audioBuffer;
        currentSource.connect(playContext.destination);
        currentSource.onended = playNext;  // 播完自动播下一段
        currentSource.start();

    } catch (err) {
        console.error("播放失败:", err);
        currentSource = null;
        playNext();  // 跳过这段，继续播放
    }
}

// ============================================================
// VAD 实现（简化版，基于音量检测）
// ============================================================
let silenceTimer = null;
let isSpeaking = false;

function setupVAD(stream) {
    const audioCtx = new AudioContext({ sampleRate: 16000 });
    const source = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);

    const buffer = new Uint8Array(analyser.fftSize);
    const SILENCE_THRESHOLD = 20;    // 音量阈值（0-255）
    const SPEECH_MIN_MS = 200;       // 说话持续至少这么长才算有效
    const SILENCE_TRIGGER_MS = 500;  // 静音这么长后判定说话结束

    let speechStart = null;

    function check() {
        analyser.getByteTimeDomainData(buffer);

        // 计算 RMS 音量
        let sum = 0;
        for (let i = 0; i < buffer.length; i++) {
            const x = (buffer[i] - 128) / 128.0;
            sum += x * x;
        }
        const rms = Math.sqrt(sum / buffer.length) * 255;
        const hasVoice = rms > SILENCE_THRESHOLD;

        if (hasVoice) {
            if (!isSpeaking) {
                // 用户开始说话
                isSpeaking = true;
                speechStart = Date.now();
                clearTimeout(silenceTimer);
                silenceTimer = null;
                onVADStart();  // 触发打断 + 录音
            } else {
                // 继续说话，重置静音计时器
                clearTimeout(silenceTimer);
                silenceTimer = null;
            }
        } else {
            if (isSpeaking && !silenceTimer) {
                silenceTimer = setTimeout(() => {
                    // 说话结束
                    isSpeaking = false;
                    silenceTimer = null;
                    const duration = Date.now() - speechStart;
                    if (duration >= SPEECH_MIN_MS) {
                        onVADEnd();  // 触发 ASR
                    } else {
                        // 太短，可能是噪音，取消录音
                        cancelRecording();
                    }
                }, SILENCE_TRIGGER_MS);
            }
        }

        requestAnimationFrame(check);
    }

    check();
}
```

---

## 19.4 服务端代码：处理 interrupt 事件

在 `server.py` 的消息处理函数里，加上对 `interrupt` 消息的处理：

```python
# voicebot/server.py

async def _handle_control(self, session, data: dict) -> None:
    """处理控制消息。"""
    logger = logging.getLogger(__name__)
    msg_type = data.get("type")

    if msg_type == "vad_end":
        audio_data = session.clear_asr_buffer()
        if audio_data:
            asyncio.create_task(
                self._pipeline.process(session, audio_data),
                name=f"pipeline-{session.session_id}"
            )

    elif msg_type == "interrupt":
        await self._handle_interrupt(session)

    elif msg_type == "ping":
        import json
        await session.websocket.send(json.dumps({"type": "pong"}))


async def _handle_interrupt(self, session) -> None:
    """
    处理打断信号。

    步骤：
    1. 取消当前 LLM 生成任务
    2. 取消当前 TTS 合成任务
    3. 清空 TTS 音频队列
    4. 重置 Session 状态
    """
    logger = logging.getLogger(__name__)
    logger.info(f"[{session.session_id}] 处理打断信号")

    # 取消所有进行中的任务
    await session.cancel_current_tasks()

    # 清空 TTS 队列（丢弃所有未发送的音频）
    await session.drain_tts_queue()

    # 清空 ASR 缓冲区（上一轮的残留音频）
    session.clear_asr_buffer()

    # 通知客户端打断已处理
    import json
    try:
        await session.websocket.send(json.dumps({
            "type": "interrupt_ack",
        }))
    except Exception:
        pass  # 连接可能已经断开

    logger.info(f"[{session.session_id}] 打断处理完成，等待新的用户输入")
```

---

## 19.5 asyncio.Task.cancel() 的正确使用

`Task.cancel()` 是 Python 异步编程里很容易踩坑的地方。让我们深入理解它的行为：

```python
import asyncio

# 场景一：任务在 await 点等待时
async def task_waiting():
    print("任务开始")
    await asyncio.sleep(100)  # ← 取消会在这里注入 CancelledError
    print("这里不会执行")

# 场景二：任务在循环里
async def task_in_loop():
    for i in range(1000000):
        # 如果循环体没有 await，取消不会立刻生效
        # 要等到下一个 yield 点
        pass  # ← 取消不会在这里生效

# 场景三：正确处理取消
async def task_cancellable():
    try:
        while True:
            data = await fetch_next_chunk()  # ← 取消会在这里生效
            process(data)
    except asyncio.CancelledError:
        # 清理资源
        await cleanup()
        raise  # 必须重新抛出，让调用方知道任务被取消了
```

在我们的 LLM 流式生成里，每个 `async for` 循环的迭代之间都有 `await`，所以取消是可以生效的：

```python
async def _llm_tts_pipeline(self, session: Session, record) -> None:
    messages = session.get_llm_messages()

    try:
        async for token in self._llm.generate_stream(messages):
            # ↑ 这里每次迭代都有 await，取消可以在这里生效
            ...

    except asyncio.CancelledError:
        logger.info(f"[{session.session_id}] LLM 生成被取消")
        # 不要 raise，让 Session.cancel_current_tasks() 处理
        # 实际上，cancel_current_tasks() 会 await task，
        # 所以这里 raise 也没问题，看你的设计
        raise
```

**常见坑：忘了 `raise`**

```python
# 错误：吃掉了 CancelledError
async def bad_task():
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        print("被取消了")
        # 忘了 raise！
        # 调用方 await task 时，任务会"正常结束"
        # asyncio 可能会发出警告

# 正确：
async def good_task():
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        print("被取消了，清理资源...")
        await cleanup()
        raise  # 必须重新抛出
```

**取消 HTTP 流的正确方式**

OpenAI 的流式 API 内部是一个 HTTP 长连接。当我们取消 asyncio Task 时，需要确保底层的 HTTP 连接也被关闭：

```python
# voicebot/llm/openai_llm.py（支持取消的版本）

class OpenAILLM:
    async def generate_stream(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        stream = None
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except asyncio.CancelledError:
            logger.info("LLM 流式生成被取消")
            raise  # 一定要 raise
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            yield "抱歉，我现在有点问题。"
        finally:
            # 确保关闭底层连接（释放资源）
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
```

---

## 19.6 完整的打断流程代码

现在把所有部分整合到一起，展示完整的打断处理流程：

```python
# voicebot/session.py（打断相关方法的完整实现）

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Session:
    # ... 其他代码省略 ...

    async def interrupt(self) -> None:
        """
        执行打断：取消所有任务，清空队列，重置状态。
        这是打断操作的核心方法。
        """
        logger.info(f"[{self.session_id}] 开始执行打断")

        # 1. 标记打断状态（防止任务在取消过程中继续往队列放数据）
        self._interrupted = True

        # 2. 取消所有进行中的任务
        await self.cancel_current_tasks()

        # 3. 清空 TTS 队列
        await self.drain_tts_queue()

        # 4. 清空 ASR 缓冲区
        self.clear_asr_buffer()

        # 5. 重置打断标记（为下一轮做准备）
        self._interrupted = False

        logger.info(f"[{self.session_id}] 打断完成")

    async def cancel_current_tasks(self) -> None:
        """取消当前所有进行中的任务，等待它们真正结束。"""
        tasks_cancelled = []

        if self.current_llm_task and not self.current_llm_task.done():
            self.current_llm_task.cancel()
            tasks_cancelled.append(("LLM", self.current_llm_task))

        if self.current_tts_task and not self.current_tts_task.done():
            self.current_tts_task.cancel()
            tasks_cancelled.append(("TTS", self.current_tts_task))

        # 等待任务真正结束（cancel 只是发信号，不是立刻终止）
        for task_name, task in tasks_cancelled:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                logger.debug(f"[{self.session_id}] {task_name} 任务已取消")
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self.session_id}] {task_name} 任务取消超时，强制结束"
                )
            except Exception as e:
                logger.error(
                    f"[{self.session_id}] 等待 {task_name} 任务结束时出错: {e}"
                )

        self.current_llm_task = None
        self.current_tts_task = None
        logger.debug(f"[{self.session_id}] 所有任务已取消")

    async def drain_tts_queue(self) -> None:
        """清空 TTS 队列，丢弃所有未发送的音频。"""
        count = 0
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break

        if count > 0:
            logger.debug(
                f"[{self.session_id}] TTS 队列已清空，丢弃了 {count} 个音频块"
            )
```

在流水线中，合成音频之前检查是否已被打断：

```python
# voicebot/pipeline.py（打断感知的版本）

async def _synthesize_and_enqueue(
    self,
    session: Session,
    text: str,
    record,
) -> None:
    """合成音频并放入队列，每次放入前检查是否已被打断。"""
    first_chunk = True

    try:
        async for audio_chunk in self._tts.synthesize_stream(text):
            # 检查 Session 是否已关闭或被打断
            if session.is_closed or getattr(session, "_interrupted", False):
                logger.debug(
                    f"[{session.session_id}] Session 已关闭/被打断，停止放入音频"
                )
                return

            if first_chunk:
                first_chunk = False
                if record.tts_first_chunk_at is None:
                    record.mark("tts_first_chunk")

            await session.tts_queue.put(audio_chunk)

            if record.audio_sent_at is None:
                record.mark("audio_sent")

    except asyncio.CancelledError:
        logger.debug(f"[{session.session_id}] TTS 合成被取消")
        raise
    except Exception as e:
        logger.error(
            f"[{session.session_id}] TTS 合成失败 ('{text[:20]}'): {e}"
        )
```

---

## 19.7 边界情况处理

### 边界情况一：打断信号到达时 TTS 已经发完了

```
时间线：
t=0    AI 开始说一段话
t=1.5  TTS 全部发完（服务端队列已空）
t=1.5  用户开口（客户端 VAD 触发）
t=1.6  服务端收到 interrupt

此时：
- LLM 任务：已经完成（done()=True）
- TTS 任务：已经完成（done()=True）
- TTS 队列：已经空了

处理方式：正常处理，cancel_current_tasks() 发现任务已经完成，直接返回
```

```python
async def cancel_current_tasks(self) -> None:
    tasks_cancelled = []

    if self.current_llm_task and not self.current_llm_task.done():
        # done() 检查：任务已完成时不尝试取消
        self.current_llm_task.cancel()
        tasks_cancelled.append(("LLM", self.current_llm_task))

    # 如果任务已经完成，不做任何操作——这就是边界情况的处理
    # ...
```

### 边界情况二：连续打断（用户多次开口）

```
t=0    AI 开始说
t=0.8  用户开口（第一次打断）→ 客户端发 interrupt
t=0.85 服务端收到 interrupt，开始取消任务
t=1.0  用户说了一个字停了（VAD 判定太短，丢弃）
t=1.2  服务端取消完成，切换到监听状态
t=1.5  用户再次开口（正式说话）
t=2.0  用户说完，发 vad_end → 触发新的流水线
```

处理方式：每次 interrupt 都重置状态，多次 interrupt 是幂等操作。

### 边界情况三：打断时 asyncio.Task 还在启动中

```python
# 问题：在 create_task 之后立刻 cancel，任务可能还没开始运行
task = asyncio.create_task(long_running())
# 如果这里立刻 cancel，task 可能还没开始执行
task.cancel()
```

解决方案：用 `asyncio.shield()` 保护某些操作，或者在 task 开始时检查取消状态：

```python
async def long_running():
    # 开始时检查是否已被取消
    await asyncio.sleep(0)  # yield 一次，让取消信号生效
    # 如果已被取消，上面的 sleep 会抛出 CancelledError
    ...
```

### 边界情况四：打断后立刻说话，服务端还没处理完打断

```
t=0    AI 开始说
t=0.5  用户开口 → interrupt + 开始录音
t=0.5  客户端发 interrupt
t=1.0  用户说完 → 客户端发 vad_end
t=1.05 服务端收到 interrupt，开始取消
t=1.06 服务端收到 vad_end！（打断还没处理完）
```

解决方案：服务端维护一个处理状态，在打断处理完之前，延迟处理 vad_end：

```python
async def _handle_control(self, session, data: dict) -> None:
    msg_type = data.get("type")

    if msg_type == "interrupt":
        await self._handle_interrupt(session)
        # 设置标记：打断处理完了
        session.interrupt_done = True

    elif msg_type == "vad_end":
        # 如果打断刚刚发生，等一下再处理
        if not getattr(session, "interrupt_done", True):
            await asyncio.sleep(0.1)

        session.interrupt_done = False
        audio_data = session.clear_asr_buffer()
        if audio_data:
            asyncio.create_task(
                self._pipeline.process(session, audio_data)
            )
```

---

## 19.8 单元测试

```python
# tests/test_interruption.py

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from voicebot.session import Session
from voicebot.pipeline import VoicePipeline


class TestInterruption:

    def setup_method(self):
        self.ws = AsyncMock()
        self.ws.send = AsyncMock()
        self.session = Session(self.ws)

    @pytest.mark.asyncio
    async def test_interrupt_cancels_llm_task(self):
        """打断应该取消正在运行的 LLM 任务。"""
        async def slow_llm():
            await asyncio.sleep(100)

        self.session.current_llm_task = asyncio.create_task(slow_llm())
        await self.session.interrupt()

        assert self.session.current_llm_task is None

    @pytest.mark.asyncio
    async def test_interrupt_drains_tts_queue(self):
        """打断应该清空 TTS 队列。"""
        # 往队列放一些音频
        await self.session.tts_queue.put(b"audio_chunk_1")
        await self.session.tts_queue.put(b"audio_chunk_2")
        await self.session.tts_queue.put(b"audio_chunk_3")

        assert self.session.tts_queue.qsize() == 3

        await self.session.interrupt()

        assert self.session.tts_queue.empty()

    @pytest.mark.asyncio
    async def test_interrupt_is_idempotent(self):
        """多次调用 interrupt 应该是安全的（幂等）。"""
        await self.session.interrupt()
        await self.session.interrupt()  # 不应该抛出异常

    @pytest.mark.asyncio
    async def test_interrupt_when_task_already_done(self):
        """任务已经完成时，打断应该正常处理而不报错。"""
        async def quick_task():
            return "done"

        task = asyncio.create_task(quick_task())
        await asyncio.sleep(0)  # 让任务完成
        self.session.current_llm_task = task

        # 不应该抛出异常
        await self.session.interrupt()
        assert self.session.current_llm_task is None

    @pytest.mark.asyncio
    async def test_pipeline_stops_enqueuing_after_interrupt(self):
        """打断后，合成协程不应该继续往队列放数据。"""
        chunks_enqueued = 0
        original_put = self.session.tts_queue.put

        async def count_put(item):
            nonlocal chunks_enqueued
            chunks_enqueued += 1
            await original_put(item)

        self.session.tts_queue.put = count_put

        # 模拟合成过程中被打断
        async def mock_tts_stream(text):
            for i in range(10):
                yield f"chunk_{i}".encode()
                await asyncio.sleep(0.01)

        # 启动合成，然后立刻打断
        asr_mock = AsyncMock()
        asr_mock.transcribe = AsyncMock(return_value="测试文本")

        llm_mock = MagicMock()
        async def mock_generate(messages):
            yield "你好，"
            await asyncio.sleep(0.05)
            yield "今天天气不错。"
        llm_mock.generate_stream = mock_generate

        tts_mock = MagicMock()
        tts_mock.synthesize_stream = mock_tts_stream

        pipeline = VoicePipeline(asr_mock, llm_mock, tts_mock)

        # 启动流水线
        process_task = asyncio.create_task(
            pipeline.process(self.session, b"fake_audio")
        )
        self.session.current_llm_task = process_task

        # 稍等一下，让流水线开始运行
        await asyncio.sleep(0.02)

        # 执行打断
        await self.session.interrupt()

        # 等流水线彻底结束
        try:
            await asyncio.wait_for(process_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        # 打断后，队列应该是空的（或只有很少的数据）
        assert self.session.tts_queue.qsize() == 0


class TestInterruptEdgeCases:

    @pytest.mark.asyncio
    async def test_interrupt_while_no_active_task(self):
        """没有活跃任务时打断应该是安全的。"""
        ws = AsyncMock()
        session = Session(ws)
        # current_llm_task 和 current_tts_task 都是 None
        await session.interrupt()  # 不应该报错

    @pytest.mark.asyncio
    async def test_rapid_interrupts(self):
        """快速连续打断不应该造成问题。"""
        ws = AsyncMock()
        session = Session(ws)

        async def long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        for _ in range(5):
            task = asyncio.create_task(long_task())
            session.current_llm_task = task
            await asyncio.sleep(0.01)
            await session.interrupt()

        assert session.current_llm_task is None
```

运行测试：

```bash
pytest tests/test_interruption.py -v
```

---

## 19.9 完整的打断时序图

```
客户端                              服务端
──────────────────────────────────────────────────────────
用户开口说话
    │
    ├── VAD 检测到语音
    │
    ├── [如果 isAISpeaking]
    │    ├── currentSource.stop()    ←─ 立刻停止播放
    │    ├── audioPlayQueue = []     ←─ 清空播放队列
    │    └── ws.send("interrupt")   ──────────────────────→ 收到 interrupt
    │                                                              │
    ├── startRecording()             ←─ 开始录音                  ├── session.interrupt()
    │                                                              │    ├── task.cancel()
    │                                                              │    ├── await task
    │                                                              │    └── drain_queue()
    │                                                              │
    │                                                              └── ws.send("interrupt_ack")
    │                               ←────────────────────────────
    │
    ├── [收到 interrupt_ack]
    │    └── 可以继续说话（已确认服务端处理完毕）
    │
用户说完话
    │
    ├── VAD 检测到静音
    └── ws.send("vad_end")          ──────────────────────→ 收到 vad_end
                                                                  │
                                                                  └── 启动新的 ASR→LLM→TTS 流水线
```

---

## 19.10 优化：预测性打断

基础打断方案已经够用。但有一个更进阶的技巧：**预测性打断**。

用户在说话的时候，服务端不需要等到完整的 interrupt 消息才响应。当服务端收到 `vad_start`（用户开始说话）时，就可以提前取消当前任务：

```python
async def _handle_control(self, session, data: dict) -> None:
    msg_type = data.get("type")

    if msg_type == "vad_start":
        # 用户开始说话！
        # 如果 AI 正在说话，提前取消（不等 interrupt 消息）
        if session.current_llm_task and not session.current_llm_task.done():
            logger.info(
                f"[{session.session_id}] 收到 vad_start，"
                f"预测性取消当前任务"
            )
            await self._handle_interrupt(session)

    elif msg_type == "interrupt":
        # 显式打断（客户端已经停止播放了）
        await self._handle_interrupt(session)
        # 此时服务端可能已经在 vad_start 时处理过了，是幂等的

    elif msg_type == "vad_end":
        audio_data = session.clear_asr_buffer()
        if audio_data:
            asyncio.create_task(
                self._pipeline.process(session, audio_data)
            )
```

这样可以节省 50-100ms（网络来回的时间）。

---

## 本章小结

本章我们实现了 VoiceBot 的打断功能（Barge-in）：

- **打断的完整流程**：客户端 VAD 触发 → 停止播放 → 发送 interrupt → 服务端取消任务 → 清空队列 → 等待新输入
- **客户端代码**：VAD 检测到用户开口时立刻停止 AudioBufferSourceNode，清空播放队列，发送 interrupt 消息
- **服务端代码**：收到 interrupt 后通过 `asyncio.Task.cancel()` 取消 LLM 生成和 TTS 合成，清空音频队列
- **asyncio 取消的正确使用**：`cancel()` 只是发信号，要 `await task` 等待真正结束；`CancelledError` 必须重新 `raise`
- **边界情况**：任务已完成时的打断、连续打断、打断后立刻说话
- **预测性打断**：在 `vad_start` 时就提前取消，比等 `interrupt` 消息快 50-100ms

至此，VoiceBot 的核心功能都已经实现了：Session 管理、完整流水线、延迟优化、打断处理。

一个能真正对话的语音助手，已经在你手里了。
