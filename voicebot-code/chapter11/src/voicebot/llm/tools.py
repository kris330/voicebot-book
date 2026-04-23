
from typing import Any, Callable, Awaitable
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具定义，符合 OpenAI 函数调用格式"""
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[str]]

    def to_openai_format(self) -> dict:
        """转换为 OpenAI tools 参数格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ==================== 工具实现 ====================

async def get_weather(city: str, date: str = "today") -> str:
    """
    查询天气（示例：实际项目中调用真实天气 API）
    """
    # 真实项目中这里调用天气 API，如：
    # response = await http_client.get(f"https://api.weather.com/v1/{city}")
    # return format_weather(response.json())

    # 示例数据
    weather_data = {
        "北京": {"temp": 18, "condition": "多云", "wind": "北风三级"},
        "上海": {"temp": 22, "condition": "晴", "wind": "东南风二级"},
        "广州": {"temp": 28, "condition": "阵雨", "wind": "南风四级"},
    }

    data = weather_data.get(city, {"temp": 20, "condition": "未知", "wind": "微风"})
    return f"{city}{date}天气：{data['condition']}，气温{data['temp']}度，{data['wind']}"


async def query_calendar(date: str) -> str:
    """查询日历（示例）"""
    # 真实项目中查询 Google Calendar 或企业日历
    if date == "today" or date == "今天":
        return "今天下午三点有一个项目评审会议，预计一小时。"
    return f"{date}暂无日程安排。"


async def control_device(device: str, action: str) -> str:
    """控制智能家居设备（示例）"""
    # 真实项目中调用 Home Assistant API 或涂鸦 API
    return f"已将{device}{action}"


# ==================== 工具注册表 ====================

AVAILABLE_TOOLS = [
    ToolDefinition(
        name="get_weather",
        description="查询指定城市的天气情况",
        parameters={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称，如北京、上海",
                },
                "date": {
                    "type": "string",
                    "description": "日期，如 today（今天）、tomorrow（明天）",
                    "default": "today",
                },
            },
            "required": ["city"],
        },
        handler=get_weather,
    ),
    ToolDefinition(
        name="query_calendar",
        description="查询日历上的日程安排",
        parameters={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "日期，如 today、tomorrow 或具体日期",
                }
            },
            "required": ["date"],
        },
        handler=query_calendar,
    ),
    ToolDefinition(
        name="control_device",
        description="控制智能家居设备，如打开灯、调节空调温度",
        parameters={
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "设备名称，如客厅灯、空调",
                },
                "action": {
                    "type": "string",
                    "description": "操作指令，如打开、关闭、调到26度",
                },
            },
            "required": ["device", "action"],
        },
        handler=control_device,
    ),
]

# 方便快速查找
TOOLS_BY_NAME = {tool.name: tool for tool in AVAILABLE_TOOLS}
