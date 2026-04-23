"""
对比不同 VAD 参数配置的效果

评估指标：
- 漏检率（有语音但 VAD 没有触发）
- 误触发率（静音被判定为语音）
- 截断率（一句话被错误分割成两段）
- 延迟（从说完到 VAD 触发的时间）
"""

import asyncio
import numpy as np
import wave

from voicebot.vad.vad_manager import VADManager, VADConfig


async def evaluate_config(audio_file: str, config: VADConfig) -> dict:
    """评估一组 VAD 参数配置"""
    vad = VADManager(config)
    await vad.init()

    # 读取音频
    with wave.open(audio_file, 'r') as f:
        audio = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)

    # 模拟流式处理
    segments = []
    for i in range(0, len(audio), config.chunk_samples):
        frame = audio[i:i + config.chunk_samples]
        if len(frame) < config.chunk_samples:
            frame = np.pad(frame, (0, config.chunk_samples - len(frame)))

        segment = await vad.process_chunk(frame)
        if segment:
            segments.append(segment)

    return {
        'num_segments': len(segments),
        'avg_duration_ms': np.mean([s.duration_ms for s in segments]) if segments else 0,
        'total_audio_ms': sum(s.duration_ms for s in segments),
        'segments': [(s.start_ms, s.end_ms, s.duration_ms) for s in segments],
    }


async def grid_search():
    """网格搜索最优参数"""
    test_file = "test_audio/test_sample.wav"

    configs = [
        VADConfig(silence_threshold_ms=400, speech_threshold=0.4),
        VADConfig(silence_threshold_ms=600, speech_threshold=0.5),
        VADConfig(silence_threshold_ms=800, speech_threshold=0.5),
        VADConfig(silence_threshold_ms=600, speech_threshold=0.6),
        VADConfig(silence_threshold_ms=1000, speech_threshold=0.5),
    ]

    print(f"{'静音阈值(ms)':<15} {'语音概率阈值':<15} {'段数':<8} {'平均时长(ms)':<15}")
    print("-" * 60)

    for cfg in configs:
        result = await evaluate_config(test_file, cfg)
        print(
            f"{cfg.silence_threshold_ms:<15} "
            f"{cfg.speech_threshold:<15} "
            f"{result['num_segments']:<8} "
            f"{result['avg_duration_ms']:<15.0f}"
        )


if __name__ == "__main__":
    asyncio.run(grid_search())
