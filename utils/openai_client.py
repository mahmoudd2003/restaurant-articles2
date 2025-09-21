# utils/openai_client.py
from typing import List, Dict, Any, Optional
import os

# يدعم openai==1.x
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # سيتعامل app.py مع عدم توفره

_client: Optional["OpenAI"] = None

def _ensure_client() -> "OpenAI":
    global _client
    if _client is not None:
        return _client
    if OpenAI is None:
        raise RuntimeError("openai package not installed")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    _client = OpenAI(api_key=api_key)
    return _client

def chat_complete_cached(messages: List[Dict[str, Any]], model: str = "gpt-4o-mini", **kwargs):
    """
    التفاف مباشر على Chat Completions، بدون أقواس ناقصة :)
    """
    client = _ensure_client()
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=kwargs.get("temperature", 0.2),
        max_tokens=kwargs.get("max_tokens"),
        top_p=kwargs.get("top_p", 1),
        presence_penalty=kwargs.get("presence_penalty"),
        frequency_penalty=kwargs.get("frequency_penalty"),
        seed=kwargs.get("seed"),
    )
