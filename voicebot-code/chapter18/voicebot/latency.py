
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LatencyRecord:
    """一次请求的完整延迟记录。"""
    session_id: str
    request_id: str
    started_at: float = field(default_factory=time.monotonic)

    # 各节点时间戳（monotonic）
    vad_end_at: Optional[float] = None        # VAD 判断说话结束
    audio_received_at: Optional[float] = None  # 服务端收到完整音频
    asr_done_at: Optional[float] = None        # ASR 识别完成
    llm_first_token_at: Optional[float] = None # LLM 输出第一个 token
    tts_triggered_at: Optional[float] = None   # 第一次触发 TTS
    tts_first_chunk_at: Optional[float] = None # TTS 输出第一帧音频
    audio_sent_at: Optional[float] = None      # 第一帧音频发送到客户端

    def mark(self, stage: str) -> None:
        """记录某个阶段的时间戳。"""
        setattr(self, f"{stage}_at", time.monotonic())

    def elapsed_ms(self, from_stage: str, to_stage: str) -> Optional[float]:
        """计算两个阶段之间的耗时（毫秒）。"""
        t_from = getattr(self, f"{from_stage}_at")
        t_to = getattr(self, f"{to_stage}_at")
        if t_from is None or t_to is None:
            return None
        return (t_to - t_from) * 1000

    def total_ttfs_ms(self) -> Optional[float]:
        """
        计算 TTFS：从 VAD 判断说话结束，到第一帧音频发送。
        """
        if self.vad_end_at is None or self.audio_sent_at is None:
            return None
        return (self.audio_sent_at - self.vad_end_at) * 1000

    def report(self) -> str:
        """生成可读的延迟报告。"""
        lines = [f"[{self.session_id}] 延迟报告 (request: {self.request_id})"]

        stages = [
            ("vad_end", "audio_received", "上行网络"),
            ("audio_received", "asr_done", "ASR 识别"),
            ("asr_done", "llm_first_token", "LLM 首token"),
            ("llm_first_token", "tts_triggered", "文字积累"),
            ("tts_triggered", "tts_first_chunk", "TTS 首帧"),
            ("tts_first_chunk", "audio_sent", "下行网络"),
        ]

        for from_s, to_s, label in stages:
            ms = self.elapsed_ms(from_s, to_s)
            if ms is not None:
                bar = "█" * int(ms / 50)  # 每 50ms 一格
                lines.append(f"  {label:12s}: {ms:6.0f}ms  {bar}")

        ttfs = self.total_ttfs_ms()
        if ttfs is not None:
            lines.append(f"  {'TTFS':12s}: {ttfs:6.0f}ms  ← 总延迟")

        return "\n".join(lines)


class LatencyTracker:
    """追踪所有请求的延迟，支持统计分析。"""

    def __init__(self, max_records: int = 1000) -> None:
        self._records: list[LatencyRecord] = []
        self._max_records = max_records

    def new_record(self, session_id: str, request_id: str) -> LatencyRecord:
        record = LatencyRecord(session_id=session_id, request_id=request_id)
        self._records.append(record)
        # 保持记录数量在上限内
        if len(self._records) > self._max_records:
            self._records.pop(0)
        return record

    def get_stats(self) -> dict:
        """计算统计数据（P50、P95、P99）。"""
        ttfs_values = [
            r.total_ttfs_ms()
            for r in self._records
            if r.total_ttfs_ms() is not None
        ]

        if not ttfs_values:
            return {"count": 0}

        ttfs_values.sort()
        n = len(ttfs_values)

        def percentile(p):
            idx = int(n * p / 100)
            return ttfs_values[min(idx, n - 1)]

        return {
            "count": n,
            "ttfs_p50_ms": round(percentile(50), 0),
            "ttfs_p95_ms": round(percentile(95), 0),
            "ttfs_p99_ms": round(percentile(99), 0),
            "ttfs_min_ms": round(min(ttfs_values), 0),
            "ttfs_max_ms": round(max(ttfs_values), 0),
        }
