import os
from typing import List, Dict, Any
from openai import OpenAI

def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing in environment/Secrets.")
    return OpenAI(api_key=api_key)

def chat_complete_cached(messages: List[Dict[str, Any]], model: str, temperature: float=0.2, max_tokens: int=2048) -> str:
    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=messages
    )
    return resp.choices[0].message.content.strip()
