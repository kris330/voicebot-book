# 第三章：异步编程基础

## 为什么语音系统必须用 asyncio

---

先来看一个问题：假设你用最朴素的方式实现 VoiceBot，代码大概长这样：

```python
# ❌ 同步实现的伪代码
def handle_user():
    audio = receive_audio()        # 等待用户说完话（可能 3 秒）
    text = asr.recognize(audio)    # 等待 ASR 识别（可能 0.5 秒）
    reply = llm.generate(text)     # 等待 LLM 生成（可能 1 秒）
    tts_audio = tts.synthesize(reply)  # 等待 TTS 合成（可能 0.5 秒）
    send_audio(tts_audio)
```

这段代码有两个致命问题：

**问题一：每一步都在"等"**

`receive_audio()` 在等用户说话，CPU 什么都没干，只是在等。ASR 在等 GPU 计算，CPU 又在等。整个程序大部分时间在等，而不是在工作。

**问题二：无法并发**

当第一个用户在说话时，第二个用户来了，他必须排队——因为程序只有一个执行路径，它被第一个用户"占住了"。

这就是**同步阻塞**编程的本质问题：等待 I/O 时 CPU 被浪费，多个任务无法并行。

VoiceAI 系统里几乎每个操作都是 I/O 等待：等网络数据、等模型推理、等文件写入……用同步方式写，性能会极差。

`asyncio` 解决的正是这个问题。

---

## 3.1 asyncio 的核心思想

`asyncio` 的核心思想是：**等待期间，去做别的事。**

用一个餐厅类比：

- **同步模式**：一个服务员同一时间只服务一桌客人。客人点菜时，服务员站在旁边等。客人想好了，服务员把菜单交给厨房，然后站在厨房门口等菜出来。一桌客人服务完，再去下一桌。

- **异步模式**：一个服务员服务很多桌。A 桌客人点菜时，服务员给他们菜单就去服务 B 桌了。A 桌想好了按铃，服务员过来收菜单，然后去服务 C 桌。厨房出菜时按铃，服务员过来端菜。

关键不是服务员变快了，而是**等待期间不再浪费**。

`asyncio` 用一个叫做**事件循环（Event Loop）**的机制实现这个模式：

```
事件循环（Event Loop）：

  ┌──────────────────────────────────────────────┐
  │  等待队列                                      │
  │  [任务A: 等待用户音频] [任务B: 等待ASR结果]     │
  │  [任务C: 等待LLM输出] [任务D: 等待TTS合成]      │
  └──────────────────────────────────────────────┘
            ↕ 某个任务"好了"
  ┌──────────────────────────────────────────────┐
  │  执行队列                                      │
  │  [任务A: 处理收到的音频帧] → 处理完 → 回等待队列│
  └──────────────────────────────────────────────┘
```

事件循环不断地检查：有没有任务"好了"（比如数据到来、计算完成）？有的话就运行它，运行到它下一个等待点，然后切换到下一个"好了"的任务。

---

## 3.2 协程：async/await

`asyncio` 通过 `async def` 和 `await` 来定义和使用协程（Coroutine）。

```python
import asyncio

# async def 定义一个协程函数
async def fetch_asr_result(audio: bytes) -> str:
    print("发送音频给 ASR...")
    # await 表示：这里要等一段时间，等的时候可以去做别的事
    await asyncio.sleep(0.5)  # 模拟 ASR 延迟 0.5 秒
    print("ASR 完成")
    return "今天天气怎么样"

async def main():
    result = await fetch_asr_result(b"fake audio")
    print(f"识别结果：{result}")

# 启动事件循环
asyncio.run(main())
```

规则很简单：
- 调用一个 `async def` 函数，要加 `await`
- `await` 只能在 `async def` 函数内部使用
- `asyncio.run()` 是程序的入口，启动事件循环

---

## 3.3 并发执行多个任务

`asyncio` 的价值在并发。下面这个例子展示了同步和异步的时间差距：

```python
import asyncio
import time

# 模拟三个并行的耗时操作
async def run_asr() -> str:
    await asyncio.sleep(0.5)    # 模拟 ASR 用时 0.5s
    return "识别完成"

async def run_llm() -> str:
    await asyncio.sleep(1.0)    # 模拟 LLM 用时 1.0s
    return "生成完成"

async def run_tts() -> str:
    await asyncio.sleep(0.3)    # 模拟 TTS 用时 0.3s
    return "合成完成"


# 方式一：顺序执行（总耗时 = 0.5 + 1.0 + 0.3 = 1.8s）
async def sequential():
    start = time.perf_counter()
    a = await run_asr()
    b = await run_llm()
    c = await run_tts()
    print(f"顺序执行耗时：{time.perf_counter() - start:.2f}s")


# 方式二：并发执行（总耗时 ≈ max(0.5, 1.0, 0.3) = 1.0s）
async def concurrent():
    start = time.perf_counter()
    a, b, c = await asyncio.gather(run_asr(), run_llm(), run_tts())
    print(f"并发执行耗时：{time.perf_counter() - start:.2f}s")


async def main():
    await sequential()
    await concurrent()

asyncio.run(main())
# 输出：
# 顺序执行耗时：1.80s
# 并发执行耗时：1.00s
```

`asyncio.gather()` 同时启动多个协程，等所有都完成后返回结果列表。

---

## 3.4 Task：让任务在后台运行

`asyncio.gather()` 会等所有任务完成才继续。但有时候你想"启动一个任务，不等它，继续干别的"——这就是 `Task`：

```python
import asyncio

async def tts_consumer():
    """持续消费 TTS 队列，在后台运行"""
    print("TTS 消费者启动")
    while True:
        await asyncio.sleep(0.1)  # 每 100ms 检查一次队列
        # ... 处理 TTS 音频块

async def main():
    # 把 tts_consumer 变成一个后台 Task，立刻返回，不等它完成
    consumer_task = asyncio.create_task(tts_consumer())
    print("TTS 消费者已在后台启动")

    # 主流程继续运行
    await asyncio.sleep(0.5)
    print("主流程干了其他事情")

    # 需要停止时，取消它
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        print("TTS 消费者已停止")

asyncio.run(main())
```

VoiceBot 里大量使用这个模式——ASR 的推理循环、TTS 的播放队列、定时刷新录音……都是作为后台 Task 运行的。

---

## 3.5 Queue：模块间传递数据

语音系统里，不同模块之间需要传递数据：ASR 把识别结果传给 LLM，LLM 把文字传给 TTS。`asyncio.Queue` 是异步安全的队列，专门用来做这件事：

```python
import asyncio

async def asr_producer(queue: asyncio.Queue) -> None:
    """ASR 模块：识别完成后把文字放入队列"""
    sentences = ["今天天气怎么样", "帮我设个明天早上八点的闹钟", "播放周杰伦的歌"]
    for sentence in sentences:
        await asyncio.sleep(0.8)    # 模拟识别耗时
        print(f"[ASR] 识别完成：{sentence}")
        await queue.put(sentence)   # 放入队列
    await queue.put(None)           # None 表示结束信号


async def llm_consumer(queue: asyncio.Queue) -> None:
    """LLM 模块：从队列取文字，生成回复"""
    while True:
        text = await queue.get()    # 等待队列有数据
        if text is None:
            print("[LLM] 收到结束信号，退出")
            break
        await asyncio.sleep(0.5)    # 模拟 LLM 耗时
        print(f"[LLM] 回复：针对「{text}」的回答...")
        queue.task_done()


async def main():
    q = asyncio.Queue()

    # ASR 生产者和 LLM 消费者并发运行
    await asyncio.gather(
        asr_producer(q),
        llm_consumer(q),
    )
    print("对话处理完毕")

asyncio.run(main())
```

Queue 的关键特性：
- `await queue.put(item)`：放入数据，如果队列满了会等待
- `await queue.get()`：取出数据，如果队列空了会等待
- 天然线程安全（asyncio 单线程模型里不需要加锁）

---

## 3.6 Lock：保护共享状态

虽然 asyncio 是单线程的，但协程之间的切换是在 `await` 点发生的。如果两个协程都在修改同一份数据，就可能出现数据竞争：

```python
import asyncio

# 危险示例：两个协程同时修改 counter（不加锁）
counter = 0

async def increment_unsafe():
    global counter
    val = counter        # 读取当前值
    await asyncio.sleep(0)  # 模拟 await 点，这里可能被切换！
    counter = val + 1   # 写入，但此时另一个协程可能已经修改了 counter

async def main_unsafe():
    await asyncio.gather(*[increment_unsafe() for _ in range(100)])
    print(f"不加锁结果：{counter}")  # 可能不是 100！
```

用 `asyncio.Lock` 修复：

```python
import asyncio

counter = 0
lock = asyncio.Lock()

async def increment_safe():
    global counter
    async with lock:        # 获取锁，其他协程在这里等待
        val = counter
        await asyncio.sleep(0)
        counter = val + 1   # 临界区内，安全修改

async def main_safe():
    await asyncio.gather(*[increment_safe() for _ in range(100)])
    print(f"加锁结果：{counter}")  # 一定是 100
```

VoiceBot 里，录音的缓冲区、TTS 的待播队列，都需要 Lock 保护。

---

## 3.7 Event：等待某个条件

有时候一个协程需要等待另一个协程"通知它可以继续了"，用 `asyncio.Event`：

```python
import asyncio

async def wait_for_user_stop(stop_event: asyncio.Event) -> None:
    """等待用户停止说话"""
    print("等待用户停止说话...")
    await stop_event.wait()     # 阻塞，直到 event 被 set()
    print("用户停止说话，开始识别")

async def vad_detector(stop_event: asyncio.Event) -> None:
    """VAD 检测到用户停止说话"""
    await asyncio.sleep(2.0)    # 模拟 2 秒后检测到停止
    print("VAD：检测到停止说话")
    stop_event.set()            # 通知等待者

async def main():
    stop_event = asyncio.Event()
    await asyncio.gather(
        wait_for_user_stop(stop_event),
        vad_detector(stop_event),
    )

asyncio.run(main())
# 输出：
# 等待用户停止说话...
# （2秒后）
# VAD：检测到停止说话
# 用户停止说话，开始识别
```

---

## 3.8 一个完整的迷你流水线

把上面的知识串起来，写一个模拟 VoiceBot 核心流程的迷你版本：

```python
# mini_voicebot.py
import asyncio
import random


async def fake_asr(audio_bytes: bytes) -> str:
    """模拟 ASR：花费随机时间，返回识别文字"""
    await asyncio.sleep(random.uniform(0.3, 0.7))
    return "今天天气怎么样"


async def fake_llm_stream(text: str):
    """模拟 LLM 流式输出：逐 token 产出文字"""
    reply = "今天北京天气晴，气温十八度，适合出行。"
    for char in reply:
        await asyncio.sleep(0.05)   # 每个字间隔 50ms
        yield char


async def fake_tts(sentence: str) -> bytes:
    """模拟 TTS：合成一句话，返回音频字节"""
    await asyncio.sleep(0.2)
    return f"[audio:{sentence}]".encode()


async def handle_session(session_id: int) -> None:
    """处理一次完整的对话轮次"""
    print(f"[Session {session_id}] 开始处理")

    # 1. ASR：语音 → 文字
    user_text = await fake_asr(b"audio data")
    print(f"[Session {session_id}] ASR: {user_text}")

    # 2. LLM 流式输出 + 按句切分 → TTS
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
    sentence_buffer = ""
    sentence_enders = {"。", "！", "？", "…"}

    async def llm_to_tts_producer():
        nonlocal sentence_buffer
        async for token in fake_llm_stream(user_text):
            sentence_buffer += token
            # 遇到句末标点，把完整句子送入 TTS 队列
            if token in sentence_enders:
                print(f"[Session {session_id}] LLM -> TTS: {sentence_buffer}")
                await tts_queue.put(sentence_buffer)
                sentence_buffer = ""
        # 剩余内容
        if sentence_buffer.strip():
            await tts_queue.put(sentence_buffer)
        await tts_queue.put(None)   # 结束信号

    async def tts_consumer():
        while True:
            sentence = await tts_queue.get()
            if sentence is None:
                break
            audio = await fake_tts(sentence)
            print(f"[Session {session_id}] TTS 合成: {audio}")

    # LLM 生产者和 TTS 消费者并发运行
    await asyncio.gather(llm_to_tts_producer(), tts_consumer())
    print(f"[Session {session_id}] 对话完成")


async def main():
    # 模拟 3 个用户同时发起对话
    await asyncio.gather(
        handle_session(1),
        handle_session(2),
        handle_session(3),
    )

asyncio.run(main())
```

运行这段代码，你会看到三个 session 的输出交错出现——这正是异步并发的效果：三个用户在同一个线程里被"同时"服务。

---

## 3.9 asyncio 的边界和陷阱

### 陷阱一：CPU 密集型任务会阻塞事件循环

`asyncio` 适合 I/O 密集型任务（网络、文件、等待）。如果有 CPU 密集型任务（比如本地 ASR 模型推理），它会阻塞整个事件循环，让所有其他协程都卡住：

```python
import asyncio
import time

def cpu_heavy():
    """纯 CPU 计算，会阻塞事件循环"""
    result = 0
    for i in range(10_000_000):
        result += i
    return result

async def main():
    loop = asyncio.get_event_loop()
    # ✅ 正确做法：把 CPU 任务放到线程池里运行
    result = await loop.run_in_executor(None, cpu_heavy)
    print(f"结果：{result}")

asyncio.run(main())
```

`run_in_executor(None, func)` 把同步函数放到线程池执行，不阻塞事件循环。VoiceBot 里所有本地模型的推理调用都用这个方式包装。

### 陷阱二：忘记 await

```python
async def main():
    # ❌ 错误：没有 await，协程根本没有执行
    fetch_asr_result(b"audio")

    # ✅ 正确
    await fetch_asr_result(b"audio")
```

Python 不会报错，但什么也不会发生。如果你发现某个异步函数"没有执行"，先检查是否漏了 `await`。

### 陷阱三：在异步代码里使用同步阻塞调用

```python
import asyncio
import time

async def bad_example():
    # ❌ time.sleep 是同步的，会阻塞整个事件循环
    time.sleep(1)

async def good_example():
    # ✅ asyncio.sleep 是异步的，等待时会让出控制权
    await asyncio.sleep(1)
```

同样的问题出现在网络请求：用 `requests` 库会阻塞，要换成 `httpx` 或 `aiohttp`。

---

## 3.10 VoiceBot 的异步架构预览

理解了 asyncio 之后，我们来看 VoiceBot 的整体异步结构是如何组织的：

```
asyncio 事件循环（单线程）
│
├── Task: WebSocket 接收循环
│     每收到一帧音频 → 发布事件
│
├── Task: VAD 处理循环
│     监听音频帧事件 → 判断说话状态 → 发布 VAD 事件
│
├── Task: ASR 推理
│     └── run_in_executor → 线程池（CPU 密集）
│
├── Task: LLM 流式生成
│     每产出一个 token → 放入 TTS 文本队列
│
├── Task: TTS 合成循环
│     从文本队列取句子 → 合成音频 → 放入音频队列
│     └── run_in_executor → 线程池（CPU 密集）
│
├── Task: TTS 音频发送循环
│     从音频队列取块 → 通过 WebSocket 发送给客户端
│
└── Task: 录音管理
      监听音频事件 → 写入 WAV 文件
      └── run_in_executor → 线程池（I/O 密集）
```

每个模块是一个独立的后台 Task，通过事件（Event）或队列（Queue）通信，互不阻塞。这就是 VoiceBot 能同时服务多个用户、保持低延迟的根本原因。

从第 14 章开始，我们会从零实现这套事件总线，把所有模块串联起来。

---

## 3.11 本章小结

本章打下了异步编程的基础：

| 工具 | 作用 | 典型用途 |
|------|------|----------|
| `async def` / `await` | 定义和调用协程 | 所有异步函数 |
| `asyncio.gather()` | 并发执行多个协程，等全部完成 | 并行调用 ASR + LLM |
| `asyncio.create_task()` | 创建后台任务，不等待 | 启动消费者循环 |
| `asyncio.Queue` | 异步安全的生产者-消费者队列 | LLM → TTS 数据传递 |
| `asyncio.Lock` | 保护共享状态 | 保护音频缓冲区 |
| `asyncio.Event` | 协程间的通知机制 | VAD 触发 ASR |
| `run_in_executor` | 把同步/CPU 密集型任务放到线程池 | 本地模型推理 |

这些工具在后续每一章都会反复出现。如果某段代码读不懂，大概率是用到了上面某个工具——翻回这章查一下就好。

下一章，我们进入前端——浏览器的麦克风是怎么工作的，以及怎么把采集到的音频实时发送到服务端。

---

> **本章代码**都是可以直接粘贴运行的，不需要安装额外依赖（`asyncio` 是标准库）。建议把 `mini_voicebot.py` 跑一遍，观察三个 session 的输出顺序，感受并发的效果。
