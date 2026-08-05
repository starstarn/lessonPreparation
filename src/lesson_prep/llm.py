from __future__ import annotations

import ssl
import threading

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from lesson_prep.config import (
    MOCK_LLM,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_MODEL,
)
from lesson_prep.logutil import safe_log

_lock = threading.Lock()
_chat_model: ChatOpenAI | None = None
_ssl_ctx: ssl.SSLContext | None = None


def _ensure_ssl_context() -> ssl.SSLContext:
    """在主线程预创建 SSL context，避免 Windows 子线程 Errno 22。"""
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    _ssl_ctx = ctx
    return ctx


def warm_clients() -> None:
    """应用启动时在主线程调用，预创建 SSL context 和 Chat 模型。"""
    if MOCK_LLM:
        return
    _ensure_ssl_context()
    safe_log("SSL context 预热完成")


def get_chat_model(temperature: float = 0.2) -> ChatOpenAI:
    global _chat_model
    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM=true 时不应调用真实 Chat 模型")
    if not OPENAI_API_KEY:
        raise RuntimeError("缺少 OPENAI_API_KEY，请复制 .env.example 为 .env 并填写")

    with _lock:
        if _chat_model is None:
            _ensure_ssl_context()
            _chat_model = ChatOpenAI(
                model=OPENAI_MODEL,
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_API_BASE,
                temperature=0.2,
                timeout=120,
                max_retries=2,
            )
    if temperature == 0.2:
        return _chat_model
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

    _ensure_ssl_context()
    return OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE,
        check_embedding_ctx_length=False,
    )
