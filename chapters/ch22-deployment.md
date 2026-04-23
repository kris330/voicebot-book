# 第 22 章：部署实战

## 在自己电脑上能跑，服务器上跑不起来

你在本地开发了几周，VoiceBot 的每一个功能都工作正常。你信心满满地把代码部署到服务器，然后——浏览器报错："NotAllowedError: The request is not allowed by the user agent or the platform in the current context"。

麦克风权限被拒绝了。

原因很简单：**浏览器只在 HTTPS 或 localhost 下允许访问麦克风**。你的服务器是 HTTP，所以失败。

这只是本地和生产环境之间众多差异之一。本章我们系统地解决部署问题，让 VoiceBot 真正在生产环境中稳定运行。

---

## 22.1 开发环境 vs 生产环境的差异

```
┌────────────────┬─────────────────────────┬──────────────────────────┐
│ 方面           │ 开发环境                │ 生产环境                 │
├────────────────┼─────────────────────────┼──────────────────────────┤
│ 协议           │ HTTP/WS (localhost)     │ HTTPS/WSS (域名)         │
│ 进程管理       │ 手动 python main.py     │ systemd/supervisor 守护  │
│ 崩溃恢复       │ 手动重启                │ 自动重启                 │
│ 日志           │ 打印到终端              │ 写文件 + 日志系统        │
│ 并发           │ 1 个用户自测            │ N 个用户同时连接         │
│ 资源限制       │ 不限制                  │ CPU/内存 需要规划        │
│ 静态文件       │ uvicorn 直接服务        │ Nginx 服务               │
│ SSL 证书       │ 不需要                  │ 必须（Let's Encrypt）    │
│ 监控           │ 眼睛盯着                │ 自动告警                 │
└────────────────┴─────────────────────────┴──────────────────────────┘
```

---

## 22.2 HTTPS/WSS：不是可选项

### 为什么必须用 HTTPS

浏览器的麦克风 API（`getUserMedia`）是"强大功能"（Powerful Feature），要求**安全上下文**（Secure Context）。安全上下文只有两种情况：

1. `localhost` 或 `127.0.0.1`（只在本地开发有效）
2. HTTPS/WSS 连接

所以，只要你的 VoiceBot 要在外网访问，必须用 HTTPS。

同时，WebSocket 连接也需要从 `ws://` 升级为 `wss://`（WebSocket Secure）。你的前端代码需要相应修改：

```javascript
// frontend/src/js/index.js

// 自动选择 ws:// 或 wss://
const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${wsProtocol}//${location.host}/ws`;
const socket = new WebSocket(wsUrl);
```

### 证书选择

**自签证书**：适合内网或测试，浏览器会显示安全警告，用户需要手动信任。

**Let's Encrypt**：免费的公信 CA，适合对外服务，浏览器完全信任，90 天自动续期。

本章使用 Let's Encrypt。

---

## 22.3 用 Nginx 做反向代理 + SSL 终止

整体架构：

```
互联网
   │
   │  HTTPS/WSS (443)
   ↓
┌──────────────────────────────┐
│           Nginx              │
│  - SSL 终止（解密 HTTPS）    │
│  - WebSocket 代理            │
│  - 静态文件服务              │
│  - 负载均衡（可选）          │
└──────────────┬───────────────┘
               │ HTTP/WS (8080, 内网)
               ↓
┌──────────────────────────────┐
│    VoiceBot (uvicorn)        │
│  - WebSocket 处理            │
│  - API 接口                  │
└──────────────────────────────┘
```

### 安装 Nginx 和 Certbot

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx

# CentOS/RHEL
sudo yum install -y nginx certbot python3-certbot-nginx
```

### 申请 Let's Encrypt 证书

```bash
# 替换为你的域名和邮箱
sudo certbot --nginx -d voicebot.example.com -m admin@example.com --agree-tos

# 证书存放位置
# /etc/letsencrypt/live/voicebot.example.com/fullchain.pem
# /etc/letsencrypt/live/voicebot.example.com/privkey.pem

# 设置自动续期（certbot 安装时通常已配置）
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
```

### Nginx 配置文件

```nginx
# /etc/nginx/sites-available/voicebot

# HTTP → HTTPS 重定向
server {
    listen 80;
    server_name voicebot.example.com;
    return 301 https://$host$request_uri;
}

# HTTPS + WSS 服务
server {
    listen 443 ssl http2;
    server_name voicebot.example.com;

    # SSL 证书（Let's Encrypt）
    ssl_certificate     /etc/letsencrypt/live/voicebot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/voicebot.example.com/privkey.pem;

    # SSL 安全配置
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # 静态文件（前端）
    location / {
        root /var/www/voicebot/dist;
        index index.html;
        try_files $uri $uri/ /index.html;

        # 静态资源缓存
        location ~* \.(js|css|png|jpg|ico|woff2)$ {
            expires 30d;
            add_header Cache-Control "public, immutable";
        }
    }

    # WebSocket 代理（关键配置）
    location /ws {
        proxy_pass http://127.0.0.1:8080;

        # WebSocket 升级头
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 传递真实客户端信息
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时设置（语音对话可能持续很久）
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # 禁用缓冲（WebSocket 不需要）
        proxy_buffering off;
    }

    # REST API 代理
    location /api {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 健康检查（不记录日志）
    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        access_log off;
    }
}
```

激活配置：

```bash
sudo ln -s /etc/nginx/sites-available/voicebot /etc/nginx/sites-enabled/
sudo nginx -t      # 测试配置语法
sudo systemctl reload nginx
```

---

## 22.4 Docker 化

Docker 让 VoiceBot 可以在任何环境运行，消除"在我机器上能跑"的问题。

### Dockerfile

```dockerfile
# Dockerfile

# ── 阶段 1：构建前端 ──────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --only=production

COPY frontend/ ./
RUN npm run build
# 产物在 /frontend/dist

# ── 阶段 2：Python 依赖安装 ───────────────────────────
FROM python:3.11-slim AS python-deps

# 安装构建依赖（用完就删）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /deps

# 先复制依赖文件（利用 Docker 层缓存）
COPY pyproject.toml ./
COPY requirements.txt ./

# 安装到独立目录（方便后续复制）
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── 阶段 3：生产镜像 ──────────────────────────────────
FROM python:3.11-slim AS production

# 安装运行时依赖（不包含构建工具）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN groupadd -r voicebot && useradd -r -g voicebot voicebot

WORKDIR /app

# 从 python-deps 阶段复制已安装的包
COPY --from=python-deps /install /usr/local

# 从 frontend-builder 阶段复制构建好的前端
COPY --from=frontend-builder /frontend/dist /app/static

# 复制应用代码
COPY voicebot/ ./voicebot/
COPY prompts/ ./prompts/
COPY config.json ./

# 不复制 .env（通过环境变量或 secrets 传入）

# 切换到非 root 用户
USER voicebot

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# 启动命令
CMD ["python", "-m", "voicebot", "--config", "config.json"]
```

### docker-compose.yml

```yaml
# docker-compose.yml

version: "3.9"

services:
  voicebot:
    build:
      context: .
      dockerfile: Dockerfile
      target: production
    image: voicebot:latest
    container_name: voicebot-app
    restart: unless-stopped

    environment:
      # API Key 通过环境变量传入，不写进镜像
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TTS_ENDPOINT=${TTS_ENDPOINT:-cosyvoice:50051}

    volumes:
      # 挂载模型目录（本地模型）
      - ./models:/app/models:ro
      # 挂载配置（方便修改后不用重建镜像）
      - ./config.json:/app/config.json:ro
      # 日志目录
      - ./logs:/app/logs

    ports:
      # 只暴露给 Nginx，不直接对外
      - "127.0.0.1:8080:8080"

    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      start_period: 30s
      retries: 3

    # 资源限制（根据实际情况调整）
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 8G
        reservations:
          cpus: "1.0"
          memory: 2G

  # 如果使用本地 CosyVoice TTS 服务
  cosyvoice:
    image: cosyvoice:latest
    container_name: voicebot-tts
    restart: unless-stopped

    volumes:
      - ./models/CosyVoice2-0.5B:/app/models/CosyVoice2-0.5B:ro

    ports:
      # 只在容器网络内暴露
      - "127.0.0.1:50051:50051"

    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

networks:
  default:
    name: voicebot-network
```

### 常用 Docker 命令

```bash
# 构建镜像
docker compose build

# 启动所有服务（后台运行）
docker compose up -d

# 查看日志（实时）
docker compose logs -f voicebot

# 重启单个服务（修改配置后）
docker compose restart voicebot

# 停止并删除容器（保留镜像和 volumes）
docker compose down

# 完整清理
docker compose down -v --rmi local

# 进入容器调试
docker compose exec voicebot bash
```

### 镜像体积优化对比

多阶段构建对镜像体积的影响：

```
单阶段构建（包含构建工具）:  ~2.1 GB
多阶段构建（仅运行时依赖）:  ~680 MB

节省约 67% 的空间，同时减小了攻击面。
```

---

## 22.5 进程管理：用 systemd 防止崩溃

如果不用 Docker，直接在服务器运行，需要 systemd 来管理进程：

```ini
# /etc/systemd/system/voicebot.service

[Unit]
Description=VoiceBot Voice AI Server
Documentation=https://github.com/yourname/voicebot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=voicebot
Group=voicebot
WorkingDirectory=/opt/voicebot

# 环境变量（或用 EnvironmentFile）
EnvironmentFile=/opt/voicebot/.env

# 启动命令
ExecStart=/opt/voicebot/.venv/bin/python -m voicebot --config /opt/voicebot/config.json

# 崩溃后自动重启
Restart=always
RestartSec=5s
StartLimitBurst=5         # 5 分钟内最多重启 5 次
StartLimitInterval=300s   # 超过限制则停止尝试（发告警）

# 资源限制
LimitNOFILE=65536         # 最大文件描述符数（WebSocket 连接需要）
LimitNPROC=4096

# 日志设置
StandardOutput=journal
StandardError=journal
SyslogIdentifier=voicebot

# 优雅关闭
KillMode=mixed
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```

```bash
# 安装并启动
sudo systemctl daemon-reload
sudo systemctl enable voicebot
sudo systemctl start voicebot

# 查看状态
sudo systemctl status voicebot

# 查看日志（最近 100 行）
sudo journalctl -u voicebot -n 100

# 实时日志
sudo journalctl -u voicebot -f
```

---

## 22.6 结构化日志

生产环境的日志需要能被日志系统解析（如 ELK、Loki）。结构化日志用 JSON 格式：

```python
# voicebot/logging_setup.py

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
```

### 在代码中使用结构化日志

```python
import logging

logger = logging.getLogger(__name__)

# 基本用法
logger.info("Session started", extra={
    "session_id": session_id,
    "user_ip": client_ip,
})

# 记录延迟
logger.info("TTS synthesis completed", extra={
    "session_id": session_id,
    "ttfs_ms": ttfs_ms,              # Time to First Sound
    "text_length": len(text),
    "engine": tts_engine_name,
})

# 记录错误
logger.error("ASR failed", extra={
    "session_id": session_id,
    "error_type": type(e).__name__,
    "audio_duration_ms": audio_duration_ms,
}, exc_info=True)
```

---

## 22.7 延迟监控：记录每次对话的 TTFS

TTFS（Time to First Sound）是衡量 VoiceBot 质量最重要的指标。

```python
# voicebot/metrics.py

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
```

---

## 22.8 健康检查接口

```python
# voicebot/health.py

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
```

---

## 22.9 并发压测：用 locust 模拟多用户

### 安装 locust

```bash
pip install locust websocket-client
```

### 压测脚本

```python
# tests/load_test/locustfile.py

import json
import time
import wave
import struct
import threading
import logging
from locust import User, task, between, events
import websocket

logger = logging.getLogger(__name__)


def generate_test_audio(duration_ms: int = 2000, sample_rate: int = 16000) -> bytes:
    """生成测试用的静音 PCM 音频（实际测试应使用真实语音）"""
    num_samples = int(sample_rate * duration_ms / 1000)
    # 生成低幅度噪声（完全静音可能被 VAD 过滤）
    import random
    samples = [int(random.gauss(0, 100)) for _ in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


class VoiceBotUser(User):
    """模拟一个 VoiceBot 用户的行为"""

    wait_time = between(3, 8)  # 每次对话间隔 3-8 秒

    def on_start(self) -> None:
        """用户开始时建立 WebSocket 连接"""
        self.ws = None
        self._connect()

    def on_stop(self) -> None:
        """用户停止时关闭连接"""
        if self.ws:
            self.ws.close()

    def _connect(self) -> None:
        target_url = self.host.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{target_url}/ws"

        self.ws = websocket.WebSocket()
        try:
            self.ws.connect(ws_url, timeout=10)
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self.ws = None

    @task
    def voice_conversation(self) -> None:
        """模拟一次完整的语音对话"""
        if not self.ws:
            self._connect()
            if not self.ws:
                return

        start_time = time.time()

        try:
            # 发送音频数据（模拟用户说话）
            audio_data = generate_test_audio(duration_ms=2000)
            chunk_size = 3200  # 100ms @ 16kHz 16bit

            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                self.ws.send_binary(chunk)
                time.sleep(0.1)  # 模拟实时发送

            # 发送说话结束信号
            self.ws.send(json.dumps({"type": "speech_end"}))

            # 等待第一个音频响应（TTFS）
            first_audio_time = None
            while True:
                try:
                    self.ws.settimeout(10)
                    msg = self.ws.recv()

                    if isinstance(msg, bytes):
                        # 收到音频数据
                        if first_audio_time is None:
                            first_audio_time = time.time()
                            ttfs_ms = (first_audio_time - start_time) * 1000
                            # 上报自定义指标
                            events.request.fire(
                                request_type="WS",
                                name="TTFS (Time to First Sound)",
                                response_time=ttfs_ms,
                                response_length=len(msg),
                                exception=None,
                                context={},
                            )
                    elif isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("type") == "audio_end":
                            break

                except websocket.WebSocketTimeoutException:
                    logger.warning("Timeout waiting for response")
                    break

        except Exception as e:
            events.request.fire(
                request_type="WS",
                name="voice_conversation",
                response_time=(time.time() - start_time) * 1000,
                response_length=0,
                exception=e,
                context={},
            )
            # 连接可能已断开，重置
            self.ws = None
```

运行压测：

```bash
# 启动 locust Web UI
locust -f tests/load_test/locustfile.py --host=https://voicebot.example.com

# 打开 http://localhost:8089 设置用户数和增长速率
# 建议从 5 个用户开始，逐步增加到 50、100

# 无 UI 模式（CI/CD 中使用）
locust -f tests/load_test/locustfile.py \
    --host=https://voicebot.example.com \
    --headless \
    --users=20 \
    --spawn-rate=2 \
    --run-time=5m \
    --csv=results/load_test
```

---

## 22.10 容量规划

一台服务器能支持多少并发会话？取决于以下因素：

```
每个并发会话的资源消耗：

┌────────────────────┬─────────────────────────────────────────────┐
│ 组件               │ 资源占用（估算）                             │
├────────────────────┼─────────────────────────────────────────────┤
│ WebSocket 连接     │ ~50KB 内存/连接                              │
│ ASR（云端）        │ 几乎 0（请求发出去，等响应）                 │
│ ASR（本地 GPU）    │ ~2GB 显存（模型），+~200MB/并发             │
│ LLM（云端）        │ 几乎 0（流式请求，CPU 处理 token）           │
│ LLM（本地 7B）     │ ~14GB 显存（模型），推理用 CPU              │
│ TTS（云端）        │ 几乎 0                                       │
│ TTS（本地 GPU）    │ ~1GB 显存（模型），+~100MB/并发             │
│ 音频处理（CPU）    │ ~1 CPU core / 5-10 并发                     │
└────────────────────┴─────────────────────────────────────────────┘
```

**全云端版本** 的容量规划（受网络带宽限制）：

```
服务器配置: 4 核 CPU, 8GB RAM, 100Mbps 带宽

每会话带宽消耗：
  上行（用户语音）: 16kHz × 16bit × 1ch = 256 Kbps = 32 KB/s
  下行（AI 语音）:  48kHz × 16bit × 1ch = 768 Kbps = 96 KB/s
  合计约 128 KB/s/会话

100 Mbps = 12.5 MB/s
理论并发上限: 12.5 MB/s ÷ 128 KB/s ≈ 100 并发

实际建议: 50-60 并发（留余量）
```

**本地模型版本** 的容量规划（受 GPU 显存限制）：

```
服务器配置: 8 核 CPU, 32GB RAM, 1×A10G (24GB 显存)

显存分配：
  SenseVoice ASR:    ~1.5 GB
  CosyVoice TTS:     ~2.0 GB
  Qwen2.5 7B LLM:   ~14.0 GB
  系统余量:           ~3.0 GB
  可用于并发:        ~3.5 GB

实际并发: 5-8 个会话（LLM 推理是串行瓶颈）
优化方向: 升级 GPU 或使用更小的 LLM（3B）
```

---

## 22.11 完整的生产部署检查清单

```
部署前检查：
  □ HTTPS 证书已申请并生效（curl https://yourdomain.com 无证书警告）
  □ WebSocket 连接可以建立（wss:// 协议）
  □ .env 文件已配置，不在 Git 仓库中
  □ config.json 中无硬编码 API Key
  □ Nginx 配置已测试（nginx -t）
  □ 防火墙只开放 80 和 443 端口
  □ systemd service 已设置自动重启
  □ 日志目录权限正确（voicebot 用户可写）

功能验证：
  □ 浏览器可以申请麦克风权限（HTTPS 才行）
  □ 语音识别正常工作
  □ LLM 回复正常（情感标记被过滤）
  □ TTS 音频正常播放
  □ 打断功能正常（说话时 AI 停止）
  □ 健康检查接口返回 200：curl https://yourdomain.com/health

性能验证：
  □ TTFS < 1500ms（P90）
  □ 20 并发压测无错误
  □ 内存无泄漏（压测后内存平稳）

监控配置：
  □ 延迟监控已接入（/metrics 接口）
  □ 日志收集已配置
  □ 告警规则已设置（TTFS P99 > 3s 告警）
  □ 服务崩溃告警已配置
```

---

## 本章小结

本章我们走完了 VoiceBot 从开发到生产的最后一公里：

1. **HTTPS/WSS**：浏览器麦克风权限需要安全上下文，生产环境必须用 HTTPS/WSS，Let's Encrypt 提供免费证书。

2. **Nginx 反向代理**：处理 SSL 终止、WebSocket 代理（注意 `Upgrade` 和 `Connection` 头）、静态文件服务，配置了长连接超时。

3. **Docker 化**：多阶段构建同时处理前端（Node.js）和后端（Python），最终镜像体积减少 67%，使用非 root 用户提升安全性。

4. **进程管理**：systemd service 配置了崩溃自动重启、资源限制、日志集成，比简单 `nohup` 可靠得多。

5. **结构化日志**：JSON 格式日志方便 ELK/Loki 等日志系统解析，每次对话的 TTFS 都被记录。

6. **延迟监控**：`SessionLatencyTimer` 记录 ASR/LLM/TTS 各阶段耗时，`/metrics` 接口提供 P50/P90/P99 统计。

7. **压测**：locust 脚本模拟多用户并发，上报 TTFS 等自定义指标，帮助验证容量规划。

---

## 全书总结

恭喜你走到了这里。从第 1 章的系统全貌，到第 22 章的生产部署，你已经从零构建了一个完整的 VoiceBot 系统。

让我们回顾一下这段旅程。

### 我们构建了什么

```
第 1-3 章：打基础
  ├── 系统全貌：一次对话的完整数据流
  ├── 音频基础：PCM、采样率、帧的概念
  └── asyncio：异步编程，让流水线不阻塞

第 4-7 章：核心模块
  ├── VAD：检测用户说话的开始和结束
  ├── ASR：语音转文字（云端 Whisper + 本地 SenseVoice）
  ├── LLM：流式生成回复（OpenAI + Ollama）
  └── TTS：文字转语音（CosyVoice gRPC 流式）

第 8-12 章：工程化
  ├── WebSocket 服务器：双向实时通信
  ├── 流水线编排：把所有模块串起来
  ├── 打断处理：用户说话时 AI 停下来
  ├── 延迟优化：TTFS < 1s 的设计原则
  └── 前端：浏览器音频采集和播放

第 13-17 章：进阶功能
  ├── 多轮对话：上下文管理和记忆
  ├── 函数调用：让 AI 能查天气、执行操作
  ├── 本地模型：完全离线运行
  ├── 多语言：中英文混合识别
  └── 电话接入：SIP/VoIP 集成

第 18-22 章：生产就绪
  ├── 安全：认证、速率限制、输入过滤
  ├── 测试：单元测试、集成测试、端到端测试
  ├── 情感控制：LLM 输出情感标记，TTS 调整音色
  ├── 配置驱动：不改代码换模型，工厂函数 + 注册表
  └── 部署：HTTPS、Docker、systemd、压测、监控
```

### 关键设计原则回顾

这本书贯穿了几个核心原则，值得再强调一遍：

**1. 流式是第一公民**

VoiceBot 的低延迟来自于"不等待"：ASR 边识别边出字，LLM 边生成边送 TTS，TTS 边合成边播放。每个模块都是流式接口，才能让端到端延迟在 1 秒以内。

**2. 错误降级，不崩溃**

TTS 失败了？发一个"抱歉，我刚才没听清"。情感解析失败了？用默认情感继续。LLM 超时了？返回缓存的兜底回复。每个模块都有降级路径。

**3. 可观测性**

你不能优化你看不见的东西。每次对话的 TTFS 都要记录，每个异常都要有 session_id 可以追踪。当线上出问题时，日志是你唯一的眼睛。

**4. 配置驱动，代码稳定**

"什么模型"是配置，"怎么运行"是代码。代码应该尽量少改，配置可以随时改。这让你能快速切换 LLM 供应商，或者在不同环境（开发/测试/生产）用不同的模型。

### 你现在有了什么

读完这本书，你有了：

- 一个可以实际部署的 VoiceBot 代码库
- 理解了语音 AI 系统的每个核心模块
- 掌握了流式处理、异步编程、WebSocket 通信等关键技术
- 能独立排查延迟问题、解决部署难题
- 一套生产就绪的架构模式（注册表、工厂、配置驱动）

### 接下来可以去哪里

**多模态：加上视觉**

GPT-4o、Qwen-VL 这类多模态模型可以理解图像。你可以在 VoiceBot 基础上加上摄像头输入，让 AI 不仅能听，还能"看"：看白板上写的内容、看用户展示的物品、看屏幕截图。

```
用户展示一张图 + 说"这段代码哪里有问题？"
  → 图像 + 语音同时发给多模态 LLM
  → AI 语音回答代码的问题
```

**实时视频对话**

WebRTC 是浏览器原生的实时视频协议，延迟比 WebSocket 更低。结合视频流处理，你可以构建类似 Google Duplex 或 GPT-4o Demo 里那样的实时视频对话系统。

**电话接入：触达更多用户**

不是所有用户都打开浏览器。通过 SIP（Session Initiation Protocol）协议，VoiceBot 可以接打电话。企业客服、预约提醒、自动回访——电话场景的需求量比 App 场景大得多。

Asterisk（开源 PBX）、Twilio（云电话）、FreeSWITCH 都可以把 SIP 信令和 RTP 音频流桥接到你的 VoiceBot 服务。

**更小的延迟：端侧推理**

随着 Apple Silicon、骁龙 X 等芯片的进步，在手机或 PC 上直接运行 ASR 和小型 LLM 已经成为现实。端侧推理消除了网络往返延迟，TTFS 可以压缩到 300ms 以内。

**Agent 能力：做事，不只是说话**

LLM 的函数调用能力让 VoiceBot 变成一个能"做事"的 Agent：查数据库、发邮件、控制智能家居、操作电脑界面。语音是最自然的指令输入方式，而 Agent 让这些指令真正被执行。

---

语音 AI 正处于快速发展期。今天你看到的延迟数字、模型规模、部署成本，一年后都会大幅改善。但那些核心原则不会变：流式处理、低延迟、可观测性、优雅降级——这些是所有实时 AI 系统的共同基础。

**你现在有了这个基础。去构建吧。**
