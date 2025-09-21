import os, requests
from typing import List, Dict, Any
from urllib.parse import urlencode

API = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

def _api_key():
    k = os.getenv("GOOGLE_MAPS_API_KEY")
    if not k:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing in Secrets.")
    return k

def fetch_places_for_topic(query: str, area: str="", limit: int=15) -> List[Dict[str, Any]]:
    key = _api_key()
    q = f"{query} {area}".strip()
    params = {"query": q, "key": key, "language": "ar"}
    r = requests.get(API, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])[:limit]
    out = []
    for it in results:
        place_id = it.get("place_id")
        det = requests.get(DETAILS, params={"place_id": place_id, "key": key, "language":"ar", "fields":"name,formatted_address,formatted_phone_number,website,geometry,opening_hours,rating,user_ratings_total,url"}, timeout=30)
        det.raise_for_status()
        d = det.json().get("result", {})
        out.append({
            "name": d.get("name") or it.get("name"),
            "rating": d.get("rating") or it.get("rating"),
            "user_ratings_total": d.get("user_ratings_total") or it.get("user_ratings_total"),
            "address": d.get("formatted_address") or it.get("formatted_address"),
            "formatted_phone_number": d.get("formatted_phone_number"),
            "website": d.get("website"),
            "maps_url": d.get("url"),
        })
    return out
