
import time
import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class LatencyRecord:
    """一次对话的延迟记录"""
    session_id: str
    timestamp: float
    asr_ms: float        # ASR 用时
    llm_ttft_ms: float   # LLM Time to First Token
    tts_ttfs_ms: float   # TTS Time to First Sound
    total_ttfs_ms: float # 端到端延迟（用户说完 → 听到回复）


class LatencyTracker:
    """
    延迟追踪器，保存最近 N 条记录，提供统计信息。
    线程安全。
    """

    def __init__(self, max_records: int = 1000) -> None:
        self._records: deque[LatencyRecord] = deque(maxlen=max_records)
        self._lock = Lock()

    def record(self, record: LatencyRecord) -> None:
        with self._lock:
            self._records.append(record)

        # 顺便记录到日志（方便日志系统聚合）
        logger.info("latency_record", extra={
            "session_id": record.session_id,
            "asr_ms": record.asr_ms,
            "llm_ttft_ms": record.llm_ttft_ms,
            "tts_ttfs_ms": record.tts_ttfs_ms,
            "total_ttfs_ms": record.total_ttfs_ms,
        })

    def get_stats(self) -> dict:
        """计算最近记录的延迟统计"""
        with self._lock:
            if not self._records:
                return {"count": 0}

            totals = [r.total_ttfs_ms for r in self._records]
            return {
                "count": len(totals),
                "p50_ms": statistics.median(totals),
                "p90_ms": statistics.quantiles(totals, n=10)[8] if len(totals) >= 10 else max(totals),
                "p99_ms": statistics.quantiles(totals, n=100)[98] if len(totals) >= 100 else max(totals),
                "avg_ms": statistics.mean(totals),
                "max_ms": max(totals),
                "min_ms": min(totals),
            }


# 全局实例
latency_tracker = LatencyTracker()


class SessionLatencyTimer:
    """在一次对话中使用，记录各阶段耗时"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._start = time.monotonic()
        self._asr_done: float | None = None
        self._llm_first_token: float | None = None
        self._tts_first_sound: float | None = None

    def mark_asr_done(self) -> None:
        self._asr_done = time.monotonic()

    def mark_llm_first_token(self) -> None:
        self._llm_first_token = time.monotonic()

    def mark_tts_first_sound(self) -> None:
        self._tts_first_sound = time.monotonic()

    def finalize(self) -> LatencyRecord | None:
        """完成计时，记录到追踪器"""
        if not all([self._asr_done, self._llm_first_token, self._tts_first_sound]):
            return None

        record = LatencyRecord(
            session_id=self.session_id,
            timestamp=time.time(),
            asr_ms=(self._asr_done - self._start) * 1000,
            llm_ttft_ms=(self._llm_first_token - self._asr_done) * 1000,
            tts_ttfs_ms=(self._tts_first_sound - self._llm_first_token) * 1000,
            total_ttfs_ms=(self._tts_first_sound - self._start) * 1000,
        )
        latency_tracker.record(record)
        return record
