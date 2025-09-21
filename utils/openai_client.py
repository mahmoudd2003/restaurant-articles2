# utils/openai_client.py
import os
from functools import lru_cache
from typing import List, Dict, Any
from openai import OpenAI

@lru_cache
def get_client() -> OpenAI:
    """
    يبني عميل OpenAI مرة واحدة (مع كاش داخل العملية).
    يعتمد على متغير البيئة OPENAI_API_KEY.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)

def chat_complete_cached(
    messages: List[Dict[str, Any]],
    model: str = "gpt-4o-mini",
    **kwargs: Any
):
    """
    التفاف بسيط على Chat Completions API (openai==1.x).
    لو عندك كاش خارجي تقدر تضيفه هنا قبل/بعد الاتصال.
    """
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=kwargs.get("temperature", 0.2),
        max_tokens=kwargs.get("max_tokens"),
        top_p=kwargs.get("top_p", 1),
        presence_penalty=kwargs.get("presence_penalty"),
        frequency_penalty=kwargs.get("frequency_penalty"),
        seed=kwargs.get("seed"),
    )
    return resp
