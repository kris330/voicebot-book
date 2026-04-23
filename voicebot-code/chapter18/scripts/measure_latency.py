
"""
批量延迟测量脚本。

使用方法：
    python scripts/measure_latency.py --count 20 --audio test_audio.wav

原理：
    模拟真实用户请求，通过 WebSocket 发送音频，测量到收到第一帧 TTS 音频的时间。
"""

import asyncio
import argparse
import struct
import time
import logging

import websockets

logger = logging.getLogger(__name__)


async def measure_one(
    ws_url: str,
    audio_data: bytes,
    request_num: int,
) -> float:
    """发送一次请求并测量 TTFS（从发送 vad_end 到收到第一帧音频）。"""
    import json

    async with websockets.connect(ws_url) as ws:
        # 等待连接确认
        await ws.recv()

        # 发送音频数据（分块，模拟真实场景）
        chunk_size = 4096
        for i in range(0, len(audio_data), chunk_size):
            await ws.send(audio_data[i:i + chunk_size])
            await asyncio.sleep(0.01)  # 模拟实时录音的速度

        # 发送 vad_end，开始计时
        t_start = time.monotonic()
        await ws.send(json.dumps({"type": "vad_end"}))

        # 等待第一帧音频（二进制消息）
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
            if isinstance(msg, bytes):
                # 收到第一帧音频
                ttfs = (time.monotonic() - t_start) * 1000
                print(f"  请求 #{request_num}: TTFS = {ttfs:.0f}ms")
                return ttfs
            else:
                # 跳过控制消息
                data = json.loads(msg)
                if data.get("type") == "asr_result":
                    print(f"  请求 #{request_num}: ASR = '{data['text']}'")


async def run_benchmark(
    ws_url: str,
    audio_path: str,
    count: int,
    concurrency: int = 1,
) -> None:
    """运行基准测试。"""
    # 读取测试音频
    with open(audio_path, "rb") as f:
        # 跳过 WAV 头（44 字节）
        f.seek(44)
        audio_data = f.read()

    print(f"测试配置:")
    print(f"  WebSocket 地址: {ws_url}")
    print(f"  测试次数: {count}")
    print(f"  并发数: {concurrency}")
    print(f"  音频大小: {len(audio_data)} bytes")
    print()

    ttfs_values = []

    # 串行执行（concurrency=1）或并行执行
    for i in range(count):
        try:
            ttfs = await measure_one(ws_url, audio_data, i + 1)
            ttfs_values.append(ttfs)
        except Exception as e:
            print(f"  请求 #{i + 1} 失败: {e}")

        # 两次请求之间稍作停顿
        if i < count - 1:
            await asyncio.sleep(1.0)

    # 统计结果
    if not ttfs_values:
        print("没有成功的测试请求")
        return

    ttfs_values.sort()
    n = len(ttfs_values)

    def pct(p):
        return ttfs_values[int(n * p / 100)]

    print()
    print("=" * 50)
    print("测试结果:")
    print(f"  成功请求: {n}/{count}")
    print(f"  TTFS 最小值: {min(ttfs_values):.0f}ms")
    print(f"  TTFS P50:    {pct(50):.0f}ms")
    print(f"  TTFS P95:    {pct(95):.0f}ms")
    print(f"  TTFS P99:    {pct(min(99, 100)):.0f}ms")
    print(f"  TTFS 最大值: {max(ttfs_values):.0f}ms")
    print("=" * 50)

    # 分布直方图
    print("\n延迟分布:")
    buckets = [500, 1000, 1500, 2000, 2500, 3000, float("inf")]
    labels = ["<500ms", "500-1000ms", "1000-1500ms", "1500-2000ms",
              "2000-2500ms", "2500-3000ms", ">3000ms"]
    counts = [0] * len(buckets)

    for v in ttfs_values:
        for i, b in enumerate(buckets):
            if v < b:
                counts[i] += 1
                break

    for label, cnt in zip(labels, counts):
        pct_str = f"{cnt/n*100:.0f}%"
        bar = "█" * cnt
        print(f"  {label:12s}: {bar:20s} {cnt} ({pct_str})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoiceBot 延迟基准测试")
    parser.add_argument("--url", default="ws://localhost:8765", help="WebSocket 地址")
    parser.add_argument("--audio", required=True, help="测试音频文件（WAV 格式）")
    parser.add_argument("--count", type=int, default=10, help="测试次数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_benchmark(args.url, args.audio, args.count))
