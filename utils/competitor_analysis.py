# utils/competitor_analysis.py
from typing import List, Dict, Any
import re
from collections import Counter

def analyze_competitors(texts: List[str]) -> Dict[str, Any]:
    words = []
    for t in texts or []:
        tokens = re.findall(r"[ء-يA-Za-z0-9]{3,}", t or "")
        words.extend([w.lower() for w in tokens])
    common = Counter(words).most_common(20)
    keywords = [w for (w, c) in common if not w.isdigit()]
    gaps = []  # مكان لتحديد الفجوات لاحقاً
    return {"summary": "basic keyword frequency", "keywords": keywords[:15], "gaps": gaps}

def extract_gap(analysis: Dict[str, Any]) -> List[str]:
    return analysis.get("gaps", [])
