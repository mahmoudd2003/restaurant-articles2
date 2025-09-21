# utils/quality_checks.py
from typing import Dict, Any
import re

def _readability(text: str) -> str:
    # تقدير تقريبي جداً
    sentences = max(1, len(re.findall(r"[.!؟…]+", text or "")))
    words     = max(1, len(re.findall(r"\w+", text or "")))
    avg = words / sentences
    if avg < 12: return "سهل"
    if avg < 20: return "متوسط"
    return "صعب"

def quality_report(text: str) -> Dict[str, Any]:
    length = len(text or "")
    notes = []
    if length < 500:
        notes.append("النص قصير نسبياً؛ فكر بزيادة التفاصيل.")
    if "مطعم" not in (text or ""):
        notes.append("أضف ذكرًا صريحًا للمطاعم/الأماكن لضبط السياق.")
    return {"readability": _readability(text or ""), "coverage": "تقديري", "notes": notes}
