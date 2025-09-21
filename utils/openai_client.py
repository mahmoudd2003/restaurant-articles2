import os
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import OpenAI
import streamlit as st

DEFAULT_MODEL = "gpt-4.1"
DEFAULT_FALLBACK = "gpt-4o-mini"

def _get_api_key():
    if hasattr(st, "secrets") and "OPENAI_API_KEY" in st.secrets and st.secrets["OPENAI_API_KEY"]:
        return st.secrets["OPENAI_API_KEY"]
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("لم يتم العثور على مفتاح OpenAI. أضف OPENAI_API_KEY في st.secrets أو كمتغير بيئة.")
    return key

def get_client():
    api_key = _get_api_key()
    project = os.getenv("OPENAI_PROJECT", None)
    if project:
        return OpenAI(api_key=api_key, project=project)
    return OpenAI(api_key=api_key)

def _is_proj_key():
    try:
        if hasattr(st, "secrets") and "OPENAI_API_KEY" in st.secrets:
            return str(st.secrets["OPENAI_API_KEY"]).startswith("sk-proj-")
    except Exception:
        pass
    return str(os.getenv("OPENAI_API_KEY") or "").startswith("sk-proj-")

@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), retry=retry_if_exception_type(Exception))
def chat_complete(client, messages, model=DEFAULT_MODEL, temperature=0.7, max_tokens=1800, fallback_model: str = None):
    try:
        return client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        ).choices[0].message.content
    except Exception as e:
        use_responses = _is_proj_key() or ("invalid_api_key" in str(e).lower()) or ("incorrect api key" in str(e).lower())
        if use_responses:
            try:
                resp = client.responses.create(model=model, input=messages, temperature=temperature, max_output_tokens=max_tokens)
                if hasattr(resp, "output_text") and resp.output_text:
                    return resp.output_text
                try:
                    return resp.output[0].content[0].text
                except Exception:
                    return str(resp)
            except Exception:
                fb = fallback_model or DEFAULT_FALLBACK
                resp = client.responses.create(model=fb, input=messages, temperature=temperature, max_output_tokens=max_tokens)
                if hasattr(resp, "output_text") and resp.output_text:
                    return resp.output_text
                try:
                    return resp.output[0].content[0].text
                except Exception:
                    return str(resp)
        fb = fallback_model or DEFAULT_FALLBACK
        return client.chat.completions.create(
            model=fb, messages=messages, temperature=temperature, max_tokens=max_tokens
        ).choices[0].message.content
