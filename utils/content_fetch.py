# utils/content_fetch.py
from typing import Dict, Any, Optional
import requests

def configure_http_cache(ttl_hours: int = 24, enabled: bool = True):
    if not enabled:
        return
    try:
        import requests_cache
        requests_cache.install_cache("http_cache", expire_after=ttl_hours * 3600)
    except Exception:
        pass

def fetch_and_extract(url: str) -> Dict[str, Any]:
    title = ""
    text  = ""
    html  = ""
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        html = resp.text
        # جرّب trafilatura أولاً
        try:
            from trafilatura import extract  # type: ignore
            text = extract(html) or ""
        except Exception:
            text = ""
        # لو فشل، ارجع إلى BeautifulSoup
        if not text:
            try:
                from bs4 import BeautifulSoup  # type: ignore
                soup = BeautifulSoup(html, "lxml")
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else ""
                text = soup.get_text(" ", strip=True)
            except Exception:
                pass
    except Exception:
        pass
    return {"url": url, "title": title, "text": text}
