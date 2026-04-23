
from enum import IntEnum
from dataclasses import dataclass

class Emotion(IntEnum):
    ANGRY      = 0   # 愤怒（用户很生气，AI 需要平息）
    COMFORT    = 1   # 安慰（用户沮丧，AI 给予支持）
    HAPPY      = 2   # 高兴（轻松愉快的对话）
    NEUTRAL    = 3   # 中性（默认，信息类回复）
    SERIOUS    = 4   # 严肃（重要事项，需要认真对待）
    EXCITED    = 5   # 激动（用户分享好消息）
    APOLOGETIC = 6   # 道歉（AI 出错需要致歉）
    ENCOURAGING= 7   # 鼓励（用户在学习或尝试）
    CURIOUS    = 8   # 好奇（探讨性话题）
    WARM       = 9   # 温暖（日常问候、关怀）

@dataclass
class EmotionConfig:
    """每种情感对应的 TTS 参数"""
    emotion: Emotion
    speed: float        # 语速倍率，1.0 = 正常
    voice_style: str    # TTS 音色风格名称（取决于具体 TTS）
    pitch_shift: float  # 音调偏移，0.0 = 不变

# 情感 → TTS 参数映射表
EMOTION_CONFIGS: dict[Emotion, EmotionConfig] = {
    Emotion.ANGRY:       EmotionConfig(Emotion.ANGRY,       0.9,  "calm",        -0.1),
    Emotion.COMFORT:     EmotionConfig(Emotion.COMFORT,     0.85, "gentle",      -0.05),
    Emotion.HAPPY:       EmotionConfig(Emotion.HAPPY,       1.1,  "cheerful",     0.05),
    Emotion.NEUTRAL:     EmotionConfig(Emotion.NEUTRAL,     1.0,  "default",      0.0),
    Emotion.SERIOUS:     EmotionConfig(Emotion.SERIOUS,     0.95, "serious",     -0.05),
    Emotion.EXCITED:     EmotionConfig(Emotion.EXCITED,     1.15, "excited",      0.1),
    Emotion.APOLOGETIC:  EmotionConfig(Emotion.APOLOGETIC,  0.9,  "gentle",      -0.05),
    Emotion.ENCOURAGING: EmotionConfig(Emotion.ENCOURAGING, 1.05, "cheerful",     0.05),
    Emotion.CURIOUS:     EmotionConfig(Emotion.CURIOUS,     1.0,  "default",      0.0),
    Emotion.WARM:        EmotionConfig(Emotion.WARM,        0.95, "gentle",       0.02),
}

def get_emotion_config(emotion: Emotion) -> EmotionConfig:
    return EMOTION_CONFIGS.get(emotion, EMOTION_CONFIGS[Emotion.NEUTRAL])
