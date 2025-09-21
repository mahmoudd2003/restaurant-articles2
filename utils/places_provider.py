# utils/places_provider.py — جلب Google Places (v1) + تنقية + ساعات الخميس + مراجع + حقائق
from __future__ import annotations
import math, re, json, requests, unicodedata
from typing import Any, Dict, List, Optional, Tuple
from utils.logging_setup import get_logger

logger = get_logger("places")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

# الحقول المطلوبة عبر FieldMask (نحاول جلب كل ما نحتاجه في خطوة واحدة)
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.businessStatus",
    "places.currentOpeningHours",
    "places.regularOpeningHours",
    "nextPageToken",
])

PRICE_MAP = {
    "PRICE_LEVEL_FREE": "مجاني",
    "PRICE_LEVEL_INEXPENSIVE": "رخيص",
    "PRICE_LEVEL_MODERATE": "متوسط",
    "PRICE_LEVEL_EXPENSIVE": "مرتفع",
    "PRICE_LEVEL_VERY_EXPENSIVE": "مرتفع جدًا",
}

def _norm(s: str) -> str:
    if not s: return ""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()

def _thursday_from_weekday_descriptions(weekday_desc: List[str]) -> Optional[str]:
    # أمثلة: "Monday: 9:00 AM – 5:00 PM" / "Thursday: 12:00 PM – 2:30 AM"
    for line in weekday_desc or []:
        if re.match(r"^\s*Thursday\s*:", line, flags=re.I):
            return line.split(":", 1)[1].strip()
    # لو لم توجد Thursday بوضوح
    return None

def _extract_thursday(opening_hours: Dict[str, Any]) -> Optional[str]:
    if not opening_hours: 
        return None
    # regularOpeningHours أولى من currentOpeningHours لأننا نريد “الخميس” ثابتًا
    reg = opening_hours.get("regularOpeningHours", {})
    if isinstance(reg, dict):
        rng = _thursday_from_weekday_descriptions(reg.get("weekdayDescriptions") or [])
        if rng:
            return rng
    cur = opening_hours.get("currentOpeningHours", {})
    if isinstance(cur, dict):
        rng = _thursday_from_weekday_descriptions(cur.get("weekdayDescriptions") or [])
        if rng:
            return rng
    return None

def _score_place(name: str, address: str, rating: Optional[float], reviews: Optional[int], query: str) -> float:
    r = float(rating or 0.0)
    n = int(reviews or 0)
    base = r * math.log1p(n)
    # Boost لتطابق العنوان/الاسم مع الكلمات
    q = _norm(query)
    tokens = [t for t in re.split(r"[\s,]+", q) if t]
    boost = 0.0
    nm = _norm(name); ad = _norm(address)
    for t in tokens:
        if t and (t in nm or t in ad):
            boost += 0.4
    return base + boost

def _dedupe(entries: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    seen = {}
    out = []
    dropped = 0
    for e in entries:
        key = (_norm(e.get("name","")), _norm(e.get("phone","") or e.get("website","") or e.get("address","")))
        if key in seen:
            dropped += 1
            continue
        seen[key] = True
        out.append(e)
    return out, dropped

def _price_band_from_enum(enum: Optional[str]) -> Optional[str]:
    if not enum: return None
    return PRICE_MAP.get(enum, None)

def _search(api_key: str, query: str, page_token: Optional[str] = None, page_size: int = 20) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    payload = {
        "textQuery": query,
        "pageSize": page_size,
        # يمكن توجيه المنطقة لاحقًا عبر locationBias أو regionCode
    }
    if page_token:
        payload["pageToken"] = page_token
    r = requests.post(SEARCH_URL, headers=headers, json=payload, timeout=30)
    if r.status_code >= 400:
        logger.error("places.search.error", extra={"status": r.status_code, "body": r.text[:600]})
        r.raise_for_status()
    return r.json()

def get_places_dataset(api_key: str, keyword: str, city: str,
                       min_reviews: int = 50, max_results: int = 40) -> List[Dict[str, Any]]:
    """يرجع قائمة منظّفة مرتبة تحتوي (name, address, phone, website, google_url, rating, reviews_count, price_band, open_now?, thursday_range, place_id)."""
    query = f"{keyword} في {city}"
    logger.info("places.request", extra={"kw": keyword, "city": city, "min_reviews": min_reviews, "max_results": max_results})

    results: List[Dict[str, Any]] = []
    token = None
    fetched = 0
    while True:
        data = _search(api_key, query, page_token=token, page_size=min(20, max_results - fetched))
        places = data.get("places") or []
        for p in places:
            name = (p.get("displayName") or {}).get("text") or ""
            addr = p.get("formattedAddress") or ""
            rating = p.get("rating")
            reviews = p.get("userRatingCount")
            phone = p.get("nationalPhoneNumber")
            website = p.get("websiteUri")
            gmap = p.get("googleMapsUri")
            price_band = _price_band_from_enum(p.get("priceLevel"))
            opening = {
                "currentOpeningHours": p.get("currentOpeningHours"),
                "regularOpeningHours": p.get("regularOpeningHours"),
            }
            thursday_range = _extract_thursday(opening)
            open_now = None
            try:
                open_now = (p.get("currentOpeningHours") or {}).get("openNow", None)
            except Exception:
                open_now = None

            if reviews is not None and reviews < min_reviews:
                continue

            entry = {
                "place_id": p.get("id"),
                "name": name,
                "address": addr,
                "rating": rating,
                "reviews_count": reviews,
                "price_band": price_band,
                "phone": phone,
                "website": website,
                "google_url": gmap,
                "open_now": open_now,
                "thursday_range": thursday_range,
            }
            entry["score"] = _score_place(name, addr, rating, reviews, query)
            results.append(entry)

        fetched += len(places)
        token = data.get("nextPageToken")
        if not token or fetched >= max_results:
            break

    n_raw = len(results)
    results, dropped = _dedupe(results)
    n_clean = len(results)
    logger.info("places.dedupe", extra={"before": n_raw, "after": n_clean, "dropped_dups": dropped})

    # ترتيب نزولي حسب score
    results.sort(key=lambda x: (x.get("score") or 0.0), reverse=True)

    # لوج لساعات الخميس
    for r in results:
        if r.get("thursday_range"):
            logger.debug("places.hours.thursday", extra={"name": r["name"], "thursday": r["thursday_range"]})

    if len(results) < 6:
        logger.warning("places.too_few", extra={"count": len(results)})

    return results[:max_results]

def references_from_places(places: List[Dict[str, Any]]) -> List[str]:
    refs = []
    for p in places or []:
        if p.get("google_url") and p["google_url"] not in refs:
            refs.append(p["google_url"])
        if p.get("website") and p["website"] not in refs:
            refs.append(p["website"])
    return refs

def _fmt_phone(phone: Optional[str]) -> str:
    return phone or "—"

def facts_markdown(places: List[Dict[str, Any]]) -> str:
    """كتلة حقائق موجزة تُمرّر للـLLM (لا تُطبع كما هي). نثبت ساعات الخميس فقط."""
    lines = []
    for p in places or []:
        name = p.get("name","")
        price = p.get("price_band") or "غير محدد"
        thu = p.get("thursday_range") or "—"
        phone = _fmt_phone(p.get("phone"))
        site = p.get("website") or "—"
        # لا نضع روابط صريحة هنا؛ هذه الكتلة مرجعية للموديل فقط
        lines.append(f"- {name}: الخميس: {thu} • السعر: {price} • هاتف: {phone} • موقع: {site}")
    return "\n".join(lines) if lines else "—"
