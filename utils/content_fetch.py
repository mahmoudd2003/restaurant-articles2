# utils/content_fetch.py
import os
import re
import requests
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDoc
import trafilatura
from trafilatura.settings import use_config
import extruct
from w3lib.html import get_base_url

# —— (جديد) كاش على مستوى requests —— #
try:
    import requests_cache
except Exception:
    requests_cache = None

_CACHE_INSTALLED = False

def configure_http_cache(enabled: bool = True, hours: int = 24, backend: str = "sqlite", name: str = "http_cache"):
    """
    تهيئة/تعطيل الكاش لطلبات HTTP عبر requests-cache.
    - enabled: تفعيل/تعطيل الكاش
    - hours: مدة الصلاحية
    - backend: 'sqlite' أو 'filesystem'
    - name: اسم قاعدة الكاش
    """
    global _CACHE_INSTALLED
    if not requests_cache:
        return False
    # إزالة أي إعداد سابق
    if _CACHE_INSTALLED:
        try:
            requests_cache.uninstall_cache()
        except Exception:
            pass
        _CACHE_INSTALLED = False
    if enabled:
        from datetime import timedelta
        requests_cache.install_cache(
            cache_name=name,
            backend=backend,
            expire_after=timedelta(hours=max(1, int(hours))),
            allowable_methods=("GET",),
            allowable_codes=(200,),
            stale_if_error=True,
        )
        _CACHE_INSTALLED = True
    return _CACHE_INSTALLED

def clear_http_cache() -> bool:
    """يمسح محتوى الكاش (إن وُجد)."""
    if not requests_cache:
        return False
    try:
        requests_cache.clear()
        return True
    except Exception:
        return False

# تهيئة افتراضية قابلة للتحكم عبر المتغيرات البيئية
if requests_cache:
    _default_enabled = os.getenv("HTTP_CACHE_ENABLED", "1") == "1"
    _default_hours = int(os.getenv("HTTP_CACHE_HOURS", "24"))
    _default_backend = os.getenv("HTTP_CACHE_BACKEND", "sqlite")
    _default_name = os.getenv("HTTP_CACHE_NAME", "http_cache")
    try:
        configure_http_cache(_default_enabled, _default_hours, _default_backend, _default_name)
    except Exception:
        pass
# —— /انتهى الكاش —— #

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ContentAuditBot/1.0; +https://example.com/bot)"}

def fetch_url(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    if hasattr(resp, "from_cache"):
        print(f"[http-cache] {'HIT ' if resp.from_cache else 'MISS'} {url}")
    return resp.text

def extract_with_trafilatura(html: str, url: str = None) -> str:
    cfg = use_config()
    cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    txt = trafilatura.extract(html, include_comments=False, no_fallback=False, url=url, config=cfg)
    return (txt or "").strip()

def extract_with_readability(html: str) -> str:
    doc = ReadabilityDoc(html)
    article_html = doc.summary()
    soup = BeautifulSoup(article_html, "lxml")
    for tag in soup(["script","style","nav","header","footer","aside"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return text

def extract_metadata(html: str, url: str) -> dict:
    base = get_base_url(html, url)
    data = {}
    try:
        all_md = extruct.extract(html, base_url=base, syntaxes=['json-ld','microdata','opengraph'])
        data["jsonld"] = all_md.get("json-ld", [])
        data["opengraph"] = all_md.get("opengraph", [])
    except Exception:
        pass
    soup = BeautifulSoup(html, "lxml")
    data["title"] = (soup.title.string.strip() if soup.title and soup.title.string else "")
    heads = []
    for tag in soup.find_all(["h1","h2","h3"]):
        t = " ".join(tag.get_text(" ", strip=True).split())
        if t:
            heads.append(f"{tag.name.upper()}: {t}")
    data["headings"] = heads
    return data

def fetch_and_extract(url: str) -> dict:
    html = fetch_url(url)
    meta = extract_metadata(html, url)
    text = extract_with_trafilatura(html, url)
    if not text or len(text.split()) < 120:
        try:
            text = extract_with_readability(html)
        except Exception:
            soup = BeautifulSoup(html, "lxml")
            text = soup.get_text("\n", strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return {
        "url": url,
        "title": meta.get("title",""),
        "headings": meta.get("headings",[]),
        "text": text,
        "word_count": len(text.split()),
        "metadata": {k:v for k,v in meta.items() if k not in ["headings"]},
    }
    # utils/content_fetch.py
try:
    from content_fetch import *  # يعيد تصدير دوالك الأصلية
except Exception as _e:
    raise ImportError("لم يتم العثور على content_fetch.py في الجذر") from _e

