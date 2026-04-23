
import logging
import sys
import json
import time
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    输出 JSON 格式的日志，方便日志系统解析。

    输出示例：
    {"timestamp": "2024-01-15T10:23:01.234Z", "level": "INFO",
     "logger": "voicebot.server", "message": "Session started",
     "session_id": "abc123", "user_ip": "1.2.3.4"}
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 附加额外字段（通过 logger.info("msg", extra={"key": "val"}) 传入）
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            }:
                log_entry[key] = value

        # 异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_production_logging(log_level: str = "INFO") -> None:
    """配置生产环境日志"""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 清除已有 handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
