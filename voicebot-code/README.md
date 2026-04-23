# VoiceBot 随书源码

本目录包含书中每一章的完整示例代码，按章节分目录存放，文件路径与书中代码块的文件名一一对应，方便直接复制使用。

## 目录结构

```
voicebot-code/
├── chapter01/   第一章：VoiceAI 系统全貌
├── chapter02/   第二章：音频信号基础
├── chapter03/   第三章：异步编程基础
├── chapter04/   第四章：浏览器麦克风采集
├── chapter05/   第五章：客户端 VAD
├── chapter06/   第六章：音频流传输协议
├── chapter07/   第七章：TTS 音频播放
├── chapter08/   第八章：移动端适配与 PWA
├── chapter09/   第九章：服务端 VAD
├── chapter10/   第十章：ASR 语音识别
├── chapter11/   第十一章：LLM Agent 对话引擎
├── chapter12/   第十二章：TTS 语音合成
├── chapter13/   第十三章：WebSocket 网关
├── chapter14/   第十四章：事件总线
├── chapter15/   第十五章：Pipeline 设计
├── chapter16/   第十六章：Session 管理
├── chapter17/   第十七章：第一个完整系统
├── chapter18/   第十八章：端到端延迟分析
├── chapter19/   第十九章：中断处理
├── chapter20/   第二十章：情感与音色控制
├── chapter21/   第二十一章：配置驱动架构
└── chapter22/   第二十二章：部署实战
```

## 使用说明

每个章节目录下的文件路径与书中的代码保持一致。例如，第九章的 VAD 管理器：

```
chapter09/
└── src/
    └── voicebot/
        └── vad/
            └── vad_manager.py
```

对应书中标注为 `src/voicebot/vad/vad_manager.py` 的代码块。

## 代码更新

源码由根目录的 `extract_code.py` 脚本从章节 Markdown 文件自动提取生成。如需重新提取（例如书稿更新后），在项目根目录执行：

```bash
python3 extract_code.py
```
