from typing import List, Dict, Any
import re

def analyze_competitors(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    # استخراج رؤوس H2/H3 على نحو تقريبي: سطور تبدأ بكلمات مفتاحية
    comp = {}
    for d in docs:
        url = d.get("url")
        text = (d.get("text") or "")
        heads = []
        for line in text.splitlines():
            s = line.strip()
            if len(s) > 0 and (s.endswith(":") or s.startswith(("أفضل","قائمة","أنواع","كيفية","سعر","أسعار","مقارنة","مراجعة"))):
                heads.append(s[:120])
        comp[url] = {"heads": heads[:20], "len": len(text.split())}
    return {"competitors": comp}

def extract_gap(topic: str, docs: List[Dict[str, Any]], competitor_summary: Dict[str, Any]) -> Dict[str, Any]:
    # فجوات مبنية على نقاط شائعة ناقصة
    freq = {}
    for _, entry in competitor_summary.get("competitors", {}).items():
        for h in entry.get("heads", []):
            k = re.sub(r"\s+", " ", h).lower()
            freq[k] = freq.get(k, 0) + 1
    common = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:20]
    return {"common_heads": common, "hint": "غطّ العناصر الشائعة عالية التكرار ولكن بزاوية مختلفة/أعمق."}
