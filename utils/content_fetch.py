import requests, time
import trafilatura
from typing import List, Dict, Any
from requests_cache import CachedSession

_http = None

def configure_http_cache(ttl_hours=24, enabled=True):
    global _http
    if enabled:
        _http = CachedSession(cache_name="data/http_cache", backend="sqlite", expire_after=ttl_hours*3600)
    else:
        _http = requests.Session()

def _get(url: str) -> str:
    global _http
    if _http is None:
        _http = requests.Session()
    r = _http.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def fetch_and_extract(urls: List[str]) -> List[Dict[str, Any]]:
    out = []
    for url in urls:
        try:
            html = _get(url)
            downloaded = trafilatura.extract(html, include_comments=False, include_tables=False, url=url)
            if not downloaded:
                downloaded = ""
            title = trafilatura.extract_metadata(html, url=url).title if trafilatura.extract_metadata(html, url=url) else ""
            out.append({"url": url, "title": title, "text": downloaded})
        except Exception:
            out.append({"url": url, "title": "", "text": ""})
    return out
