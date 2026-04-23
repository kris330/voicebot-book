
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from .model_switcher import ModelSwitcher
from .config import EngineConfig

router = APIRouter(prefix="/admin", tags=["admin"])


class SwitchEngineRequest(BaseModel):
    engine: str
    config: dict = {}


@router.post("/switch/tts")
async def switch_tts(
    request: SwitchEngineRequest,
    switcher: ModelSwitcher = Depends(get_switcher),
) -> dict:
    """切换 TTS 引擎（管理接口，仅限内网）"""
    try:
        await switcher.switch_tts(
            EngineConfig(engine=request.engine, config=request.config)
        )
        return {"status": "ok", "engine": request.engine}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Switch failed: {e}")


@router.get("/engines")
async def list_engines() -> dict:
    """查看所有可用引擎"""
    from .registry import asr_registry, llm_registry, tts_registry
    return {
        "asr": asr_registry.list_all(),
        "llm": llm_registry.list_all(),
        "tts": tts_registry.list_all(),
    }
