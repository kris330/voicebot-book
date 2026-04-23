
import json
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _interpolate_env_vars(obj: object) -> object:
    """
    递归替换配置中的环境变量占位符

    "${VAR_NAME}" → os.environ["VAR_NAME"]
    """
    if isinstance(obj, str):
        pattern = r"\$\{([^}]+)\}"
        matches = re.findall(pattern, obj)
        for var_name in matches:
            value = os.environ.get(var_name)
            if value is None:
                logger.warning(f"环境变量未设置：{var_name}")
                value = ""
            obj = obj.replace(f"${{{var_name}}}", value)
        return obj
    elif isinstance(obj, dict):
        return {k: _interpolate_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_interpolate_env_vars(item) for item in obj]
    return obj


def load_config(path: str | Path) -> dict:
    """
    加载配置文件，自动替换环境变量

    Args:
        path: JSON 配置文件路径

    Returns:
        处理后的配置字典
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    return _interpolate_env_vars(raw)
