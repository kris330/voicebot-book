
import json
import logging

from aiohttp import web

from .pipeline import latency_tracker
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


def create_monitor_app(session_manager: SessionManager) -> web.Application:
    """创建延迟监控 HTTP 服务（和 WebSocket 服务分开端口）。"""
    app = web.Application()

    async def health(request):
        return web.json_response({"status": "ok"})

    async def stats(request):
        latency_stats = latency_tracker.get_stats()
        session_stats = session_manager.get_stats()
        return web.json_response({
            "latency": latency_stats,
            "sessions": session_stats,
        })

    async def reset_stats(request):
        latency_tracker._records.clear()
        return web.json_response({"status": "reset"})

    app.router.add_get("/health", health)
    app.router.add_get("/stats", stats)
    app.router.add_post("/stats/reset", reset_stats)

    return app


# 启动监控服务（在 main.py 里调用）
async def start_monitor(session_manager: SessionManager, port: int = 8766) -> None:
    app = create_monitor_app(session_manager)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"延迟监控服务已启动: http://localhost:{port}/stats")
