import re
import math
from collections import Counter

BOILERPLATE_PATTERNS = [
    r"إذا كنت تبحث عن", r"فأنت في المكان الصحيح", r"في هذا المقال(?:\s|$)",
    r"دعنا نتعرّف", r"لا شك\s*أن", r"ختامًا", r"في النهاية", r"وباختصار",
    r"يعد(?:\s|ُ)\s*من أفضل", r"على الإطلاق", r"دعونا نبدأ", r"بدون شك"
]

GENERIC_ADJECTIVES = [
    "لذيذ", "ممتاز", "مميز", "رائع", "مذهل", "خيالي", "جميل", "مبهر", "فريد",
    "خفيف", "ثقيل", "طيب", "بطل", "أسطوري"
]

SENSORY_TERMS = [
    "رائحة", "قوام", "قرمشة", "تفحّم", "عصارة", "حموضة", "حلاوة", "ملوحة",
    "نضج", "طراوة", "حرارة", "دسامة", "بهارات", "نكهة", "تحمير", "تبُّل",
    "قشرة", "قاع", "زيوت", "تحميص", "عجين", "مرق"
]

CULINARY_TERMS = [
    "نابوليتانا", "سان مارزانو", "موزاريلا", "فرن حطب", "تخمير", "سماش",
    "بريسكيت", "أنجوس", "واجو", "V60", "إسبريسو", "لاتيه", "تنور", "فحم",
    "بسمتي", "تتبيل", "تحمير متوسط"
]

EEAT_HINTS = {
    "experience": ["تجربتي", "جرّبت", "زرنا", "تذوّقت", "قمنا بزيارة"],
    "expertise": CULINARY_TERMS,
    "transparency": ["كيف اخترنا", "منهجية", "حدود المنهجية", "آخر تحديث", "نراجع القائمة"],
    "trust": ["قد تختلف", "ننصح بالتحقق", "نقطة للتحسين", "تنويه"]
}

SECTION_HINTS = {"info_gain": ["Pro Tip", "نصيحة", "مقارنة", "تقسيم داخلي", "FAQ", "الأسئلة الشائعة"]}

def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def split_sentences_ar(text: str):
    text = text.replace("؟", "؟.").replace("!", "!.")
    parts = re.split(r"(?<=[\.\!\؟])\s+", text)
    return [s.strip() for s in parts if s.strip()]

def detect_boilerplate(text: str):
    flags = []
    for pat in BOILERPLATE_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            start = max(0, m.start()-30); end = min(len(text), m.end()+30)
            flags.append({"type": "boilerplate","pattern": pat,"excerpt": text[start:end]})
    return flags

def compute_sensory_ratio(text: str):
    tokens = re.findall(r"[\u0600-\u06FFa-zA-Z]+", text)
    if not tokens: return 0.0
    sens_count = sum(1 for t in tokens if t in SENSORY_TERMS or t in CULINARY_TERMS)
    return sens_count / max(1, len(tokens))

def compute_fluff_density(text: str):
    sentences = split_sentences_ar(text)
    if not sentences: return 0.0
    fluff = 0
    for s in sentences:
        has_generic_adj = any(adj in s for adj in GENERIC_ADJECTIVES)
        has_sensory = any(term in s for term in (SENSORY_TERMS+CULINARY_TERMS))
        if has_generic_adj and not has_sensory:
            fluff += 1
    return fluff / max(1, len(sentences))

def sentence_variety(text: str):
    sentences = split_sentences_ar(text)
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if not lengths: return {"mean": 0, "std": 0, "cv": 0}
    mean = sum(lengths)/len(lengths)
    variance = sum((l-mean)**2 for l in lengths) / len(lengths)
    import math
    std = math.sqrt(variance)
    cv = std/mean if mean else 0
    return {"mean": mean, "std": std, "cv": cv, "samples": len(lengths)}

def eeat_indicators(text: str):
    out = {}
    for k, terms in EEAT_HINTS.items():
        out[k] = any(t in text for t in terms)
    return out

def info_gain_indicators(text: str):
    return {"info_gain": any(t in text for t in SECTION_HINTS["info_gain"])}

def repeated_phrases(text: str, n=3, top=7):
    tokens = re.findall(r"[\u0600-\u06FFa-zA-Z]+", text)
    grams = [" ".join(tokens[i:i+n]) for i in range(0, max(0, len(tokens)-n+1))]
    from collections import Counter
    cnt = Counter(grams)
    items = [(g,c) for g,c in cnt.items() if c>=3]
    items.sort(key=lambda x: -x[1])
    return items[:top]

def quality_report(text: str):
    text = normalize_ws(text)
    flags = detect_boilerplate(text)
    sensory_ratio = compute_sensory_ratio(text)
    fluff_density = compute_fluff_density(text)
    variety = sentence_variety(text)
    eeat = eeat_indicators(text)
    ig = info_gain_indicators(text)
    reps = repeated_phrases(text, n=3, top=7)

    base = 60
    base += min(20, sensory_ratio*200)
    base += min(10, variety["cv"]*20)
    base -= min(20, fluff_density*100)
    base -= min(10, len(flags)*2)
    base = max(0, min(100, round(base)))

    eeat_score = 0
    eeat_score += 25 if eeat["experience"] else 0
    eeat_score += 25 if eeat["expertise"] else 0
    eeat_score += 25 if eeat["transparency"] else 0
    eeat_score += 25 if eeat["trust"] else 0

    info_gain_score = 60 if ig["info_gain"] else 35

    return {
        "human_style_score": base,
        "fluff_density": round(fluff_density, 3),
        "sensory_ratio": round(sensory_ratio, 3),
        "sentence_variety": variety,
        "eeat": eeat,
        "eeat_score": eeat_score,
        "info_gain_score": info_gain_score,
        "boilerplate_flags": flags,
        "repeated_phrases": reps,
    }
