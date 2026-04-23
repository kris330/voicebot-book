
class SentenceSplitter:
    """
    句子切分器

    策略：
    - 按句号/问号/感叹号切分
    - 保证每个片段有足够长度（避免过短）
    - 超长句子按长度强制切分
    """

    # 触发切分的标点符号
    SPLIT_PUNCTUATION = set("。！？\n")

    # 单个合成片段的长度范围
    MIN_LENGTH = 5    # 太短的片段合成质量差
    MAX_LENGTH = 100  # 太长的片段延迟高

    def split(self, text: str) -> list[str]:
        """
        把文本切分成适合 TTS 合成的片段列表

        Args:
            text: 预处理后的文本

        Returns:
            句子片段列表，每个片段适合单次 TTS 合成
        """
        sentences: list[str] = []
        current = ""

        for char in text:
            current += char

            if char in self.SPLIT_PUNCTUATION:
                # 到达切分点
                if len(current) >= self.MIN_LENGTH:
                    sentences.append(current)
                    current = ""
                # 太短则继续积累

            elif len(current) >= self.MAX_LENGTH:
                # 超过最大长度，强制切分
                # 尽量在最近的标点处切
                cut_pos = self._find_last_punctuation(current)
                if cut_pos > self.MIN_LENGTH:
                    sentences.append(current[:cut_pos + 1])
                    current = current[cut_pos + 1:]
                else:
                    sentences.append(current)
                    current = ""

        # 处理剩余文本
        if current.strip():
            sentences.append(current)

        return sentences

    def _find_last_punctuation(self, text: str) -> int:
        """在文本中找最后一个可切分的标点位置"""
        # 扩展切分点：末尾找不到强切分点时，考虑更多标点
        soft_split = set("；:：")

        for i in range(len(text) - 1, -1, -1):
            if text[i] in self.SPLIT_PUNCTUATION or text[i] in soft_split:
                return i

        return -1  # 没找到，返回 -1 表示不在标点处切
