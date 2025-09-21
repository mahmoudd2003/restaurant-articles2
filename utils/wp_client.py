# utils/wp_client.py
from __future__ import annotations
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional

import requests

def _slugify(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\u0600-\u06FF-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "post"

class WPError(RuntimeError):
    pass

class WPClient:
    """عميل مبسّط لـ WordPress REST (v2) مع Basic Auth (Application Password)."""
    def __init__(self, base_url: str, username: str, app_password: str, session: Optional[requests.Session] = None):
        self.base = (base_url or "").rstrip("/")
        if not self.base.endswith("/wp-json/wp/v2"):
            # اسمح بتمرير دومين فقط، وأكمله تلقائيًا
            if self.base.endswith("/wp-json"):
                self.base = self.base + "/wp/v2"
            else:
                self.base = self.base + "/wp-json/wp/v2"
        self.sess = session or requests.Session()
        self.sess.auth = (username, app_password)
        self.sess.headers.update({"Content-Type": "application/json; charset=utf-8"})

    # ------------ HTTP ------------
    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = r.text
            raise WPError(f"HTTP {r.status_code}: {data}")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        r = self.sess.get(self.base + path, params=params, timeout=30)
        self._check(r)
        return r.json()

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        r = self.sess.post(self.base + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=30)
        self._check(r)
        return r.json()

    def put(self, path: str, payload: Dict[str, Any]) -> Any:
        r = self.sess.put(self.base + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=30)
        self._check(r)
        return r.json()

    # ------------ Terms (categories/tags) ------------
    def _ensure_term(self, taxonomy: str, name: str) -> Optional[int]:
        """يُرجع ID للتصنيف/الوسم؛ ينشئه إن لم يوجد."""
        if not name:
            return None
        slug = _slugify(name)
        # ابحث أولًا بالـ slug
        items = self.get(f"/{taxonomy}", params={"slug": slug, "per_page": 1})
        if items:
            return items[0]["id"]
        # جرّب بالاسم (قد يكون slug مختلفًا)
        items = self.get(f"/{taxonomy}", params={"search": name, "per_page": 5})
        for it in items:
            if it["name"].strip().lower() == name.strip().lower():
                return it["id"]
        # أنشئ جديد
        created = self.post(f"/{taxonomy}", {"name": name, "slug": slug})
        return created["id"]

    def ensure_category(self, name: str) -> Optional[int]:
        return self._ensure_term("categories", name)

    def ensure_tag(self, name: str) -> Optional[int]:
        return self._ensure_term("tags", name)

    # ------------ Posts ------------
    def find_post_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        items = self.get("/posts", params={"slug": slug, "per_page": 1, "context": "edit"})
        return items[0] if items else None

    def create_post(self, title: str, content_html: str, status: str = "draft",
                    categories: Optional[List[int]] = None, tags: Optional[List[int]] = None,
                    excerpt: Optional[str] = None, meta: Optional[Dict[str, Any]] = None,
                    slug: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "title": title,
            "content": content_html,
            "status": status,
        }
        if slug:
            payload["slug"] = slug
        if categories:
            payload["categories"] = categories
        if tags:
            payload["tags"] = tags
        if excerpt:
            payload["excerpt"] = excerpt
        # ملاحظة: meta عبر REST تتطلب keys مسجلة show_in_rest=True؛ سنحاول، ونُسقط بصمت إن رفض الخادم
        if meta:
            payload["meta"] = meta
        return self.post("/posts", payload)

    def update_post(self, post_id: int, **fields) -> Dict[str, Any]:
        return self.put(f"/posts/{post_id}", fields)

    def upsert_post(self, *, title: str, slug: str, content_html: str, status: str = "draft",
                    categories: Optional[List[int]] = None, tags: Optional[List[int]] = None,
                    excerpt: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        existing = self.find_post_by_slug(slug)
        if existing:
            pid = existing["id"]
            return self.update_post(pid, title=title, content=content_html, status=status,
                                    categories=categories or [], tags=tags or [], excerpt=excerpt or "",
                                    meta=meta or {})
        return self.create_post(title, content_html, status=status, categories=categories, tags=tags, excerpt=excerpt, meta=meta, slug=slug)
