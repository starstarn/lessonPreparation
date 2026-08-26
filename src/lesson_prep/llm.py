from __future__ import annotations

import ssl
import threading
import time
from typing import Any, Literal, Sequence

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from lesson_prep.config import (
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    LLM_FALLBACK_API_BASE,
    LLM_FALLBACK_API_KEY,
    LLM_FALLBACK_ENABLED,
    LLM_MODEL_FALLBACK,
    LLM_MODEL_FAST,
    LLM_MODEL_STRONG,
    LLM_ROUTING_ENABLED,
    MOCK_LLM,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_MODEL,
)
from lesson_prep.logutil import safe_log

ModelRole = Literal["fast", "strong", "default"]

_lock = threading.Lock()
_chat_cache: dict[tuple[str, str, str, float], ChatOpenAI] = {}
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
    # 预热默认档，避免首请求冷启动
    try:
        get_chat_model(role="strong")
        if LLM_ROUTING_ENABLED and LLM_MODEL_FAST != LLM_MODEL_STRONG:
            get_chat_model(role="fast")
    except Exception as exc:  # noqa: BLE001
        safe_log(f"Chat 模型预热跳过: {exc}")
    safe_log(
        "SSL / Chat 预热完成 | "
        f"routing={LLM_ROUTING_ENABLED} fallback={LLM_FALLBACK_ENABLED} | "
        f"fast={LLM_MODEL_FAST} strong={LLM_MODEL_STRONG} "
        f"fallback_model={LLM_MODEL_FALLBACK or '(cross-tier/none)'}"
    )


def resolve_model_name(role: ModelRole = "strong") -> str:
    """按角色解析主模型名（不含降级链）。"""
    if not LLM_ROUTING_ENABLED or role == "default":
        return OPENAI_MODEL
    if role == "fast":
        return LLM_MODEL_FAST
    return LLM_MODEL_STRONG


def model_attempt_chain(role: ModelRole = "strong") -> list[tuple[str, str, str]]:
    """返回尝试列表：(model_name, api_key, api_base)。

    顺序：角色主模型 → 显式 FALLBACK → 另一档（若不同）。
    """
    primary = resolve_model_name(role)
    chain: list[tuple[str, str, str]] = [
        (primary, OPENAI_API_KEY, OPENAI_API_BASE),
    ]

    if not LLM_FALLBACK_ENABLED:
        return _dedupe_chain(chain)

    if LLM_MODEL_FALLBACK and LLM_MODEL_FALLBACK != primary:
        chain.append(
            (LLM_MODEL_FALLBACK, LLM_FALLBACK_API_KEY, LLM_FALLBACK_API_BASE)
        )

    # 跨档兜底：fast 挂了试 strong，vice versa
    if LLM_ROUTING_ENABLED:
        other = LLM_MODEL_STRONG if role == "fast" else LLM_MODEL_FAST
        if other and other != primary:
            chain.append((other, OPENAI_API_KEY, OPENAI_API_BASE))

    return _dedupe_chain(chain)


def _dedupe_chain(
    chain: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for item in chain:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def routing_status() -> dict[str, Any]:
    return {
        "routing_enabled": LLM_ROUTING_ENABLED,
        "fallback_enabled": LLM_FALLBACK_ENABLED,
        "default": OPENAI_MODEL,
        "fast": LLM_MODEL_FAST,
        "strong": LLM_MODEL_STRONG,
        "fallback": LLM_MODEL_FALLBACK or None,
    }


def get_chat_model(
    temperature: float = 0.2,
    *,
    role: ModelRole = "strong",
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ChatOpenAI:
    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM=true 时不应调用真实 Chat 模型")
    if not OPENAI_API_KEY and not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY，请复制 .env.example 为 .env 并填写")

    model_name = model or resolve_model_name(role)
    key = api_key or OPENAI_API_KEY
    base = api_base or OPENAI_API_BASE
    cache_key = (model_name, key, base, float(temperature))

    with _lock:
        cached = _chat_cache.get(cache_key)
        if cached is not None:
            return cached
        _ensure_ssl_context()
        client = ChatOpenAI(
            model=model_name,
            api_key=key,
            base_url=base,
            temperature=temperature,
            timeout=120,
            max_retries=2,
        )
        _chat_cache[cache_key] = client
        return client


def _is_transport_error(exc: BaseException) -> bool:
    """适合换模型重试的错误：限流、超时、5xx、连接失败等。"""
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
            return True
        if isinstance(exc, APIStatusError):
            code = getattr(exc, "status_code", None) or 0
            return int(code) >= 500 or int(code) == 429
    except ImportError:
        pass

    name = type(exc).__name__
    if name in {"RateLimitError", "APITimeoutError", "APIConnectionError", "TimeoutError"}:
        return True
    msg = str(exc).lower()
    return any(
        k in msg
        for k in (
            "rate limit",
            "429",
            "timeout",
            "timed out",
            "connection",
            "503",
            "502",
            "504",
            "overloaded",
        )
    )


def invoke_with_fallback(
    messages: Sequence[Any],
    *,
    role: ModelRole = "strong",
    temperature: float = 0.2,
    tools: list | None = None,
    tool_choice: str | None = None,
):
    """按角色选主模型调用；传输类失败时沿降级链切换模型。"""
    chain = model_attempt_chain(role)
    last_error: Exception | None = None

    for idx, (model_name, api_key, api_base) in enumerate(chain):
        try:
            llm = get_chat_model(
                temperature=temperature,
                model=model_name,
                api_key=api_key,
                api_base=api_base,
            )
            if tools:
                llm = llm.bind_tools(tools)
            kwargs: dict[str, Any] = {}
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
            if idx > 0:
                safe_log(f"  [llm] 降级使用模型: {model_name} (role={role})")
            else:
                safe_log(f"  [llm] 使用模型: {model_name} (role={role})")
            return llm.invoke(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            can_failover = idx < len(chain) - 1 and _is_transport_error(exc)
            if can_failover:
                wait_s = 2 * (idx + 1)
                safe_log(
                    f"  [llm] {model_name} 失败（{type(exc).__name__}: {exc}），"
                    f"{wait_s}s 后尝试下一模型..."
                )
                time.sleep(wait_s)
                continue
            raise

    assert last_error is not None
    raise last_error


def get_embeddings() -> OpenAIEmbeddings:
    if MOCK_LLM:
        raise RuntimeError("MOCK_LLM=true 时不应调用真实 Embedding 模型")
    if not EMBEDDING_API_KEY:
        raise RuntimeError("缺少 EMBEDDING_API_KEY / OPENAI_API_KEY，请在 .env 中填写")

    _ensure_ssl_context()
    return OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
        check_embedding_ctx_length=False,
    )
