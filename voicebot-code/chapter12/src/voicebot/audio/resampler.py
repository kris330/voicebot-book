
import numpy as np
import logging

logger = logging.getLogger(__name__)


def resample_pcm(
    pcm_bytes: bytes,
    src_rate: int,
    dst_rate: int,
) -> bytes:
    """
    对 PCM 音频进行重采样

    Args:
        pcm_bytes: 16-bit 有符号 PCM 音频数据
        src_rate: 源采样率（Hz）
        dst_rate: 目标采样率（Hz）

    Returns:
        重采样后的 PCM 音频数据
    """
    if src_rate == dst_rate:
        return pcm_bytes

    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    # 计算目标长度
    target_length = int(len(audio) * dst_rate / src_rate)

    try:
        from scipy import signal
        resampled = signal.resample(audio, target_length)
    except ImportError:
        # 没有 scipy，用简单的线性插值
        logger.warning("scipy 未安装，使用线性插值重采样（质量较低）")
        indices = np.linspace(0, len(audio) - 1, target_length)
        resampled = np.interp(indices, np.arange(len(audio)), audio)

    # 限幅并转回 int16
    resampled = np.clip(resampled, -32768, 32767)
    return resampled.astype(np.int16).tobytes()


def get_audio_duration_ms(pcm_bytes: bytes, sample_rate: int) -> float:
    """计算 PCM 音频时长（毫秒）"""
    num_samples = len(pcm_bytes) // 2  # 16-bit = 2 bytes/sample
    return num_samples / sample_rate * 1000
