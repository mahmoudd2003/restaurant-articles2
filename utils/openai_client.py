# utils/openai_client.py
import os, json, hashlib
from typing import Any, Dict, List, Optional
from openai import OpenAI

def get_client(api_key: Optional[str] = None) -> OpenAI:
    """يرجع عميل OpenAI باستخدام OPENAI_API_KEY من env أو Streamlit secrets."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        try:
            import streamlit as st  # type: ignore
            key = st.secrets.get("OPENAI_API_KEY")  # type: ignore[attr-defined]
        except Exception:
            pass
    if not key:
        raise RuntimeError("OPENAI_API_KEY مفقود")
    return OpenAI(api_key=key)

def _hash_payload(payload: Dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def chat_complete_cached(
    client: OpenAI,
    messages: List[Dict[str, str]],
    *,
    model: str = "gpt-4o",
    fallback_model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    cacher: Optional[Any] = None,
    cache_extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    استدعاء Chat Completions مع كاش (اختياري).
    """
    key_payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
        "extra": cache_extra or {},
    }
    cache_key = _hash_payload(key_payload)

    if cacher is not None:
        cached = cacher.get(cache_key)
        if isinstance(cached, str) and cached.strip():
            return cached

    def _call(the_model: str) -> str:
        resp = client.chat.completions.create(
            model=the_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    try:
        out = _call(model)
    except Exception:
        if fallback_model and fallback_model != model:
            out = _call(fallback_model)
        else:
            raise

    if cacher is not None and out:
        cacher.set(cache_key, out)
    return out
