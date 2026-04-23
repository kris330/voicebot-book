# 运行前准备一个测试音频文件：test.wav（16kHz, 单声道）

from funasr import AutoModel

# 加载 FSMN-VAD 模型
vad_model = AutoModel(
    model="fsmn-vad",
    model_revision="v2.0.4",
)

# 对完整音频文件做 VAD
result = vad_model.generate(input="test.wav")
print("VAD 结果:", result)
# 输出格式示例：
# [{'key': 'test', 'value': [[0, 2300], [3500, 6000]]}]
# 表示 0-2300ms 和 3500-6000ms 是语音段
