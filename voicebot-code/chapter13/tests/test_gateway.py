
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from voicebot.gateway.gateway import VoiceBotGateway


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用"""
    app = FastAPI()
    gateway = VoiceBotGateway()
    gateway.attach_to_app(app)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_websocket_connection(client):
    """测试基本连接建立"""
    with client.websocket_connect("/ws") as ws:
        # 应该收到 session_ready 消息
        data = ws.receive_json()
        assert data["type"] == "session_ready"
        assert "connection_id" in data["data"]


def test_ping_pong(client):
    """测试心跳 ping-pong"""
    with client.websocket_connect("/ws") as ws:
        # 跳过 session_ready
        ws.receive_json()

        # 发送 ping
        ws.send_json({
            "type": "ping",
            "data": {"timestamp": 1234567890.0},
        })

        # 应该收到 pong
        response = ws.receive_json()
        assert response["type"] == "pong"
        assert response["data"]["timestamp"] == 1234567890.0


def test_unknown_message_type(client):
    """测试未知消息类型不会导致连接崩溃"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 跳过 session_ready

        # 发送未知类型
        ws.send_json({"type": "unknown_type", "data": {}})

        # 发送 ping，验证连接还活着
        ws.send_json({"type": "ping", "data": {}})
        response = ws.receive_json()
        assert response["type"] == "pong"


def test_binary_audio_message(client):
    """测试接收二进制音频数据"""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # 跳过 session_ready

        # 发送模拟音频数据
        fake_audio = bytes(1024)  # 1024 字节的空音频
        ws.send_bytes(fake_audio)

        # 发送 ping 确认连接正常
        ws.send_json({"type": "ping", "data": {}})
        response = ws.receive_json()
        assert response["type"] == "pong"
