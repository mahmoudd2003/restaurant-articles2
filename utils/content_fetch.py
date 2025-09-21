import re
import requests
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDoc
import trafilatura
from trafilatura.settings import use_config
import extruct
from w3lib.html import get_base_url

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ContentAuditBot/1.0; +https://example.com/bot)"}

def fetch_url(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
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
