
class OpenAILLM:
    async def generate_stream(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        stream = None
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except asyncio.CancelledError:
            logger.info("LLM 流式生成被取消")
            raise  # 一定要 raise
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            yield "抱歉，我现在有点问题。"
        finally:
            # 确保关闭底层连接（释放资源）
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    pass
