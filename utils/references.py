# utils/references.py
import re
import time
from typing import List, Dict
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; E-E-A-T-Audit/1.0)"}

def normalize_refs(textarea_value: str) -> List[str]:
    """سطر لكل مرجع. يقبل روابط كاملة فقط، ويتجاهل الفراغات/المكررات."""
    seen, out = set(), []
    for ln in (textarea_value or "").splitlines():
        u = ln.strip()
        if not u:
            continue
        # إضافة مخطط لو لم يوجد
        if not re.match(r"^https?://", u, re.I):
            u = "https://" + u
        # تصفية بسيطة
        try:
            p = urlparse(u)
            if not p.netloc:
                continue
        except Exception:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def fetch_title(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        if soup.title and soup.title.string:
            return " ".join(soup.title.string.split())
    except Exception:
        pass
    # fallback: الدومين فقط
    try:
        return urlparse(url).netloc
    except Exception:
        return url

def build_references_md(urls: List[str]) -> str:
    """قائمة مرقّمة Markdown مع عنوان الصفحة + النطاق + تاريخ الوصول."""
    if not urls:
        return "—"
    lines = []
    today = time.strftime("%Y-%m-%d")
    for i, u in enumerate(urls, 1):
        title = fetch_title(u)
        host = urlparse(u).netloc
        lines.append(f"{i}. **{title}** — {host} — *تم الوصول: {today}*  \n   {u}")
    return "\n".join(lines)

def build_citation_map(urls: List[str]) -> Dict[int, str]:
    """خريطة {رقم: رابط} لاستخدامها في JSON-LD أو أغراض أخرى."""
    return {i+1: u for i, u in enumerate(urls)}
