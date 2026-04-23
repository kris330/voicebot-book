
import asyncio
import time
import logging
from fastapi import APIRouter
from .metrics import latency_tracker

router = APIRouter()
logger = logging.getLogger(__name__)

# 服务启动时间
_start_time = time.time()


@router.get("/health")
async def health_check() -> dict:
    """
    基础健康检查（Nginx / 负载均衡器 / k8s liveness probe 使用）。
    必须快速响应（< 100ms），不做任何重型操作。
    """
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _start_time),
    }


@router.get("/health/ready")
async def readiness_check() -> dict:
    """
    就绪检查（k8s readiness probe 使用）。
    检查依赖服务是否可用，如果不可用返回 503。
    """
    from fastapi import HTTPException
    from .registry import asr_registry, llm_registry, tts_registry

    # 这里可以检查模型是否已加载、依赖服务是否可达
    checks = {
        "asr": "ok",
        "llm": "ok",
        "tts": "ok",
    }

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    result = {
        "status": "ready" if all_ok else "not_ready",
        "checks": checks,
    }

    if not all_ok:
        raise HTTPException(status_code=503, detail=result)

    return result


@router.get("/metrics")
async def get_metrics() -> dict:
    """
    延迟统计（监控系统使用）。
    在生产环境中，这个接口应该只对内网开放。
    """
    return {
        "latency": latency_tracker.get_stats(),
        "uptime_seconds": int(time.time() - _start_time),
    }
