
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理（替代 on_event 的现代写法）
    """
    # 启动
    logger.info("VoiceBot 服务启动中...")
    # 这里可以初始化数据库连接、加载模型等
    yield
    # 关闭
    logger.info("VoiceBot 服务关闭中...")
    # 这里执行清理工作


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="VoiceBot Gateway",
        lifespan=lifespan,
    )

    # 错误处理中间件
    @app.middleware("http")
    async def error_middleware(request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(f"未处理的 HTTP 错误：{e}", exc_info=True)
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"},
            )

    return app
