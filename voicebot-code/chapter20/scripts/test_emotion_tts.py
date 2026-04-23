
import asyncio
from voicebot.emotion import Emotion, get_emotion_config
from voicebot.tts.openai_tts import OpenAITTSWithEmotion

async def main():
    tts = OpenAITTSWithEmotion(api_key="YOUR_KEY")

    test_cases = [
        (Emotion.COMFORT, "非常抱歉，请允许我来帮您解决这个问题。"),
        (Emotion.HAPPY, "太好了！您的操作完全正确！"),
        (Emotion.SERIOUS, "请注意，这是一个重要的安全提示。"),
    ]

    for emotion, text in test_cases:
        config = get_emotion_config(emotion)
        print(f"\n情感: {emotion.name}, 语速: {config.speed}, 音色: {config.voice_style}")
        print(f"文本: {text}")

        audio_data = await tts.synthesize_all(text, config)

        filename = f"test_emotion_{emotion.name.lower()}.pcm"
        with open(filename, "wb") as f:
            f.write(audio_data)
        print(f"已保存: {filename}")

if __name__ == "__main__":
    asyncio.run(main())
