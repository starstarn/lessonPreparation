from __future__ import annotations

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from lesson_prep.config import (
    MOCK_LLM,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_MODEL,
)


def get_chat_model(temperature: float = 0.2) -> ChatOpenAI:
    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM=true 时不应调用真实 Chat 模型")
    if not OPENAI_API_KEY:
        raise RuntimeError("缺少 OPENAI_API_KEY，请复制 .env.example 为 .env 并填写")

    return ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE,
        temperature=temperature,
        timeout=120,
        max_retries=2,
    )


def get_embeddings() -> OpenAIEmbeddings:
    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM=true 时不应调用真实 Embedding 模型")
    if not OPENAI_API_KEY:
        raise RuntimeError("缺少 OPENAI_API_KEY，请复制 .env.example 为 .env 并填写")

    return OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE,
        # 智谱等非 OpenAI 模型名无法映射 tiktoken，关闭本地切分校验
        check_embedding_ctx_length=False,
    )
