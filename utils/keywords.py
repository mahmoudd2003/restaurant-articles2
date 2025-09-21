# utils/keywords.py
import re
import unicodedata
from typing import List, Tuple, Dict

AR_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")

def strip_diacritics(s: str) -> str:
    # إزالة التشكيل والمدود فقط، مع الإبقاء على الحروف كما هي
    s = unicodedata.normalize("NFKD", s)
    s = AR_DIACRITICS.sub("", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def parse_required_keywords(spec: str) -> List[Tuple[str, int]]:
    """
    تنسيق كل سطر:  كلمة | min=2
    أمثلة:
      مطاعم عائلية
      برجر | min=2
      جلسات خارجية | min=3
    """
    out: List[Tuple[str, int]] = []
    for line in (spec or "").splitlines():
        line = line.strip()
        if not line:
            continue
        kw = line
        m = re.search(r"\|\s*min\s*=\s*(\d+)", line, flags=re.I)
        min_count = 1
        if m:
            min_count = max(1, int(m.group(1)))
            kw = line[: m.start()].strip()
        if kw:
            out.append((kw, min_count))
    return out

def count_occurrences(text: str, phrase: str) -> int:
    """
    عدّ ظهور عبارة داخل النص بشكل حساس للمسافات ولكن غير حساس للتشكيل.
    نستخدم مطابقة تقريبية عبر إزالة التشكيل من الطرفين.
    """
    if not text or not phrase:
        return 0
    t = strip_diacritics(text)
    p = strip_diacritics(phrase)
    # مطابقة "عبارة" وليس كلمة مفردة فقط؛ نسمح بمسافات متعددة
    # نهرب الأحرف الخاصة في العبارة
    p_escaped = re.escape(p)
    # لا نستعمل حدود \b لأنها لا تعمل جيدًا مع العربية؛ نستخدم بحثًا مباشرًا
    return len(re.findall(p_escaped, t, flags=re.IGNORECASE))

def enforce_report(text: str, required: List[Tuple[str, int]]) -> Dict:
    report = {"items": [], "missing": [], "ok": True}
    for kw, need in required:
        have = count_occurrences(text or "", kw)
        item = {"keyword": kw, "min": need, "found": have, "ok": have >= need}
        report["items"].append(item)
        if not item["ok"]:
            report["missing"].append({"keyword": kw, "need": need - have})
    report["ok"] = len(report["missing"]) == 0
    return report

FIX_PROMPT = """أدخل الكلمات/العبارات الآتية داخل النص بشكل طبيعي وغير متكلف، و"بدون" حشو أو تكرار زائد،
واحرص على الحفاظ على المعنى والأسلوب دون تغيير الحقائق. لا تغيّر العناوين أو البُنى الكبيرة،
واستخدم كل عبارة بالعدد الأدنى المطلوب أو أكثر قليلًا إن لزم، لكن تجنّب التكديس.

النص الأصلي:
{orig}

الكلمات المطلوبة وعدد النواقص:
{needs}

أعد النص كاملاً بعد إدماج الكلمات المطلوبة بشكل متناغم، ولا تشرح.
"""
