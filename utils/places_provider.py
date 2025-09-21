# utils/places_provider.py
import math
import re
import time
import json
import hashlib
import unicodedata
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse

import requests

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# ---------- أدوات نصية ----------
def _slug(s: str) -> str:
    s = s or ""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-") or "x"

def _last4_digits(phone: str) -> str:
    if not phone: return ""
    digits = re.sub(r"\D+", "", phone)
    return digits[-4:] if len(digits) >= 4 else ""

def _domain(u: str) -> str:
    try:
        p = urlparse(u)
        return p.netloc.lower()
    except Exception:
        return ""

def _google_maps_url_from_id(resource_name: str) -> str:
    # resource_name مثال: "places/ChIJrTLr-GyuEmsRBfy61i59si0"
    pid = resource_name.split("/")[-1]
    return f"https://www.google.com/maps/place/?q=place_id:{pid}"

# ---------- تحويل السعر ----------
PRICE_BANDS = {
    0: "مجاني/اقتصادي",
    1: "≤ 50 ر.س",
    2: "50 – 75 ر.س",
    3: "75 – 120 ر.س",
    4: "120+ ر.س",
}
def price_band(level: int | None) -> str:
    if level is None: return "غير محدد"
    return PRICE_BANDS.get(level, "غير محدد")

# ---------- ساعات الخميس ----------
def extract_thursday_range(weekday_desc: List[str]) -> str:
    """
    weekday_desc: مثل ["الاثنين: ...", "الخميس: 12:00 م – 2:30 ص", ...]
    نبحث بالعربية ثم احتياطًا بالإنجليزية.
    """
    if not weekday_desc:
        return "غير متاح"
    # عربي
    for ln in weekday_desc:
        if re.match(r"^\s*الخميس\s*[:：]", ln.strip()):
            return ln.split(":", 1)[-1].strip()
    # إنجليزي
    for ln in weekday_desc:
        if re.match(r"^\s*Thursday\s*[:：]", ln.strip(), flags=re.I):
            return ln.split(":", 1)[-1].strip()
    return "غير متاح"

# ---------- مطابقة الاستعلام ----------
TYPE_SYNONYMS = {
    "برجر": {"hamburger_restaurant", "fast_food_restaurant", "american_restaurant"},
    "burger": {"hamburger_restaurant"},
    "بيتزا": {"pizza_restaurant", "italian_restaurant"},
    "pizza": {"pizza_restaurant"},
    "قهوة": {"cafe", "coffee_shop"},
    "كافيه": {"cafe", "coffee_shop"},
}
def _hits(keyword: str, name: str, types: List[str]) -> Tuple[int, int]:
    kw = (keyword or "").lower()
    n_hit = 1 if kw and kw.split()[0] in (name or "").lower() else 0  # مطابقة بسيطة داخل الاسم
    t_hit = 0
    for k, syn in TYPE_SYNONYMS.items():
        if k in kw:
            if set(types or []).intersection(syn):
                t_hit = 1
                break
    return n_hit, t_hit

# ---------- ترتيب ----------
def place_score(rating: float | None, reviews: int | None, name_hit: int, type_hit: int,
                open_now: bool | None, phone: str | None, website: str | None) -> float:
    r = (rating or 0.0)
    c = max(0, reviews or 0)
    # Boost open_now = 0 (ثابت كما طلبت) — اتركه لإن أردت تعديله لاحقًا
    open_boost = 0.0
    phone_pen = 0.2 if not phone else 0.0
    site_pen  = 0.2 if not website else 0.0
    return (r * math.log1p(c)) + 0.8*name_hit + 0.5*type_hit + open_boost - phone_pen - site_pen

# ---------- تنقية (dedupe) ----------
def _geo_tile(lat: float | None, lng: float | None) -> str:
    if lat is None or lng is None:
        return ""
    return f"{round(lat, 3):.3f},{round(lng, 3):.3f}"

def dedupe_places(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for it in items:
        slug = _slug(it.get("name") or "")
        phone4 = _last4_digits(it.get("phone") or "")
        dom = _domain(it.get("website") or "")
        tile = _geo_tile(*(it.get("lat"), it.get("lng")))
        key = f"{slug}|{phone4 or dom or tile}"
        if key not in buckets:
            buckets[key] = it
        else:
            a = buckets[key]; b = it
            # اختر الأقوى: عدد مراجعات أعلى ثم تقييم أعلى
            if (b.get("reviews_count", 0), b.get("rating", 0)) > (a.get("reviews_count", 0), a.get("rating", 0)):
                buckets[key] = b
    return list(buckets.values())

# ---------- عميل Google Places ----------
class PlacesClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.api_key = api_key
        self.sess = session or requests.Session()
        self.sess.headers.update({"X-Goog-Api-Key": api_key})

    def search_text(self, text_query: str, language: str = "ar", max_results: int = 20) -> List[Dict[str, Any]]:
        """
        نستخدم places:searchText مع FieldMask لنسترجع الحقول التي نحتاجها مباشرة.
        """
        field_mask = ",".join([
            "places.name",
            "places.displayName",
            "places.formattedAddress",
            "places.nationalPhoneNumber",
            "places.internationalPhoneNumber",
            "places.websiteUri",
            "places.rating",
            "places.userRatingsTotal",
            "places.priceLevel",
            "places.types",
            "places.googleMapsUri",
            "places.currentOpeningHours.weekdayDescriptions",
            "places.location"
        ])
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }
        body = {
            "textQuery": text_query,
            "languageCode": language,
            "maxResultCount": max_results
        }
        r = self.sess.post(PLACES_SEARCH_URL, headers=headers, json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("places", []) or []

def normalize_from_google(raw: Dict[str, Any]) -> Dict[str, Any]:
    name_res = raw.get("name") or ""         # resource name: "places/ChI..."
    display = (raw.get("displayName") or {}).get("text") or ""
    address = raw.get("formattedAddress") or ""
    phone = raw.get("nationalPhoneNumber") or raw.get("internationalPhoneNumber") or ""
    website = raw.get("websiteUri") or ""
    guri = raw.get("googleMapsUri") or _google_maps_url_from_id(name_res)
    rating = raw.get("rating")
    reviews = raw.get("userRatingsTotal") or 0
    level = raw.get("priceLevel")
    types = raw.get("types") or []
    weekday = ((raw.get("currentOpeningHours") or {}).get("weekdayDescriptions")) or []
    th_range = extract_thursday_range(weekday)
    loc = raw.get("location") or {}
    lat = loc.get("latitude"); lng = loc.get("longitude")
    return {
        "place_resource": name_res,
        "place_id": name_res.split("/")[-1] if name_res else "",
        "name": display,
        "address": address,
        "phone": phone,
        "website": website,
        "google_url": guri,
        "rating": rating,
        "reviews_count": reviews,
        "price_level": level,
        "price_band": price_band(level),
        "types": types,
        "thursday_range": th_range if th_range else "غير متاح",
        "open_now": None,  # لا نعتمد open_now في هذا الطور
        "lat": lat, "lng": lng,
        "evidence": {"source": "google", "last_accessed": time.strftime("%Y-%m-%d")},
        "match": {}
    }

def get_places_dataset(api_key: str, keyword: str, city: str,
                       min_reviews: int = 50, language: str = "ar",
                       max_results: int = 40) -> List[Dict[str, Any]]:
    """
    يرجع قائمة منظّفة ومرتّبة من أماكن مطابقة: Burger in Riyadh => "برجر الرياض"
    - فلترة بالحد الأدنى للمراجعات
    - إزالة المكرر
    - ترتيب ذكي
    """
    client = PlacesClient(api_key)
    query = f"{keyword.strip()} {city.strip()}".strip()
    raw = client.search_text(query, language=language, max_results=max_results)
    # طبيع
    items = [normalize_from_google(p) for p in raw]
    # فلترة حد المراجعات
    items = [it for it in items if (it.get("reviews_count") or 0) >= min_reviews]
    # إزالة المكرر
    items = dedupe_places(items)
    # حساب الضربات والترتيب
    for it in items:
        n_hit, t_hit = _hits(keyword, it.get("name",""), it.get("types",[]))
        it["match"] = {"name_hit": n_hit, "type_hit": t_hit}
        it["score"] = place_score(it.get("rating"), it.get("reviews_count"), n_hit, t_hit, None, it.get("phone"), it.get("website"))
    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    # Top 20 افتراضيًا
    return items[:20]

# ---------- مراجع وحقائق ----------
def references_from_places(places: List[Dict[str, Any]]) -> List[str]:
    out = []
    for p in places:
        if p.get("google_url"): out.append(p["google_url"])
        if p.get("website"): out.append(p["website"])
    # إزالة المكرر مع المحافظة على الترتيب
    seen = set(); uniq = []
    for u in out:
        if u and u not in seen:
            seen.add(u); uniq.append(u)
    return uniq

def facts_markdown(places: List[Dict[str, Any]]) -> str:
    """
    تبني كتلة حقائق مختصرة لتغذية البرومبت (لا تُطبع كما هي في المقال بالضرورة).
    """
    lines = []
    for p in places:
        name = p.get("name","")
        pr = p.get("price_band","غير محدد")
        th = p.get("thursday_range","غير متاح")
        area = ""
        # استخرج حي موجز (جزء قبل الفاصلة الثانية)
        addr = p.get("address","")
        parts = [x.strip() for x in addr.split("،") if x.strip()]
        if len(parts) >= 2:
            area = parts[-2]
        piece = f"- **{name}** — الأوقات (الخميس): {th}؛ السعر: {pr}" + (f"؛ المنطقة: {area}" if area else "")
        lines.append(piece)
    return "\n".join(lines) or "—"
