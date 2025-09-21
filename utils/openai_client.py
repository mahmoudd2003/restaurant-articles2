# utils/openai_client.py — عميل OpenAI موحّد + كاش + Logs
from __future__ import annotations
import os, json, hashlib, time
from typing import Any, Dict, List, Tuple, Optional

# Logging
try:
    from utils.logging_setup import get_logger
    logger = get_logger("openai_client")
except Exception:  # fallback لو ما تم إعداد اللوجينغ
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("openai_client")

# دعم لكل من العميل الجديد (openai>=1) والقديم (openai<1)
_ClientNew = None
_openai_legacy = None
try:
    from openai import OpenAI  # مكتبة جديدة
    _ClientNew = OpenAI
    logger.debug("openai_client: using new OpenAI client")
except Exception:
    try:
        import openai as _openai_legacy  # المكتبة القديمة
        logger.debug("openai_client: using legacy openai module")
    except Exception:
        _openai_legacy = None

def _get_secret(key: str, default: str = "") -> str:
    # نحاول من env أولًا، ثم من streamlit.secrets إن وُجدت
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default

def get_client():
    """يرجع كائن عميل OpenAI جاهز للاستخدام مع مفاتيح secrets/env."""
    api_key = _get_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY غير موجود في البيئة أو secrets.")
    if _ClientNew is not None:
        # العميل الجديد
        return _ClientNew(api_key=api_key)
    if _openai_legacy is not None:
        # العميل القديم يعتمد على متغير البيئة
        os.environ["OPENAI_API_KEY"] = api_key
        return _openai_legacy
    raise RuntimeError("لم يتم العثور على مكتبة OpenAI مناسبة. ثبّت الحزمة openai.")

def _hash_messages(messages: List[Dict[str, str]]) -> str:
    # هاش ثابت للرسائل لتغذية الكاش
    h = hashlib.sha256()
    for m in messages:
        role = m.get("role","")
        content = m.get("content","")
        h.update(role.encode("utf-8", "ignore"))
        if isinstance(content, str):
            h.update(content.encode("utf-8", "ignore"))
        else:
            h.update(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8", "ignore"))
    return h.hexdigest()

def _call_chat_new(client, *, model: str, messages: List[Dict[str, Any]], temperature: float, max_tokens: int) -> Tuple[str, Dict[str, Any]]:
    """استدعاء عبر عميل OpenAI الجديد (openai>=1)."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    u = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if usage:
        u["prompt_tokens"] = getattr(usage, "prompt_tokens", 0) or 0
        u["completion_tokens"] = getattr(usage, "completion_tokens", 0) or 0
        u["total_tokens"] = getattr(usage, "total_tokens", 0) or (u["prompt_tokens"] + u["completion_tokens"])
    return text, u

def _call_chat_legacy(client, *, model: str, messages: List[Dict[str, Any]], temperature: float, max_tokens: int) -> Tuple[str, Dict[str, Any]]:
    """استدعاء عبر عميل OpenAI القديم (openai<1)."""
    resp = client.ChatCompletion.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = resp["choices"][0]["message"]["content"] or ""
    usage = resp.get("usage") or {}
    u = {
        "prompt_tokens": usage.get("prompt_tokens", 0) or 0,
        "completion_tokens": usage.get("completion_tokens", 0) or 0,
        "total_tokens": usage.get("total_tokens", 0) or (usage.get("prompt_tokens",0) + usage.get("completion_tokens",0)),
    }
    return text, u

def _chat_once(client, *, model: str, messages: List[Dict[str, Any]], temperature: float, max_tokens: int) -> Tuple[str, Dict[str, Any]]:
    if _ClientNew is not None and isinstance(client, _ClientNew):
        return _call_chat_new(client, model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
    # احتمال أن المستخدم مرّر openai legacy نفسه
    if _openai_legacy is not None:
        return _call_chat_legacy(client, model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
    # fallback (لو فشل كشف النوع)
    try:
        return _call_chat_new(client, model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
    except Exception:
        return _call_chat_legacy(client, model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)

def chat_complete_cached(
    client,
    messages: List[Dict[str, Any]],
    *,
    model: str = "gpt-4.1",
    fallback_model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    cacher: Any = None,
    cache_extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    استدعاء Chat مع كاش اختياري (LLMCacher).
    - cacher: كائن يوفّر .get(key) و .set(key, value) و .configure(...) و .clear()
    - cache_extra: حقول إضافية تدخل في مفتاح الكاش (مثل task/required/use_snapshot)
    """
    # نبني مفتاح الكاش
    cache_key_obj = {
        "model": model,
        "temperature": round(float(temperature), 3),
        "max_tokens": int(max_tokens),
        "messages_hash": _hash_messages(messages),
        "extra": cache_extra or {},
    }
    cache_key = hashlib.sha256(json.dumps(cache_key_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    # محاولة الكاش
    if cacher and getattr(cacher, "enabled", True):
        hit = cacher.get(cache_key)
        if hit is not None:
            logger.info("llm.cache.hit", extra={"model": model, "key": cache_key[:10]})
            return hit
        logger.info("llm.cache.miss", extra={"model": model, "key": cache_key[:10]})

    # استدعاء النموذج (مع fallback)
    tried = []
    last_err: Optional[Exception] = None
    for mdl in [model, fallback_model] if fallback_model else [model]:
        if not mdl:
            continue
        try:
            t0 = time.perf_counter()
            text, usage = _chat_once(client, model=mdl, messages=messages, temperature=temperature, max_tokens=max_tokens)
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            logger.info("llm.usage", extra={"model": mdl, **usage, "ms": dt_ms})
            # حفظ في الكاش
            if cacher and getattr(cacher, "enabled", True):
                cacher.set(cache_key, text)
            return text
        except Exception as e:
            last_err = e
            tried.append(mdl)
            logger.exception("llm.error", extra={"model": mdl})
            # جرّب التالي (fallback) إن وُجد
            continue

    # إذا كل المحاولات فشلت
    if last_err:
        raise last_err
    raise RuntimeError(f"LLM call failed (tried={tried})")
