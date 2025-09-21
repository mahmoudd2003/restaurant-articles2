# utils/quality_checks.py
# تحسينات فحوصات الجودة — عربية وخفيفة بدون تبعيات خارجية
import re
import math
from collections import Counter, defaultdict

# ——— قوائم مساعدة خفيفة ———
AR_STOPWORDS = set("""
في من على إلى عن مع حتى ثم أو أم بل لا لن لم إن أن كان تكون يكون قد قدّ هل ما لا ليس ليسوا التي الذي الذين التي هذا هذه ذلك تلك هناك هنا جدا فقط دون حسب عبر لدى لدى قد حيث كما كذلك بين ضمن وراء أمام قبل بعد منذ طوال خلال ضد عند نحو سوى غير مثل مثلما لأن إذ إذا لكن لكي كي إلا بما بما أنَّ أنّ إنَّ إنما بينما بينما عندما حين إذن إذًا إذْ ربما تقريبًا غالبًا نوعًا ما
""".split())

SENSORY_TERMS = set("""
قوام رائحة نكهة طازج طزاجة حار سخن بارد دسم مقرمش طري متماسك حلو مالح حامض متوازن مدخن محمّر مشوي مطبوخ مطهّي ناعم خشن زبدة زبدي كريمي سائل كثيف عطري عبق عطر غني خفيف صلب طراوة
""".split())

POSITIVE_TERMS = set("""
لذيذ ممتاز رائع متقن متوازن جميل مميز مدهش نظيف سريع لطيف ودود محترف هادئ مريح سلس مثالي طيب فخم
""".split())

NEGATIVE_TERMS = set("""
سيئ ضعيف بارد قاسٍ جاف مزعج مزدحم بطيء متأخر فوضوي مزعج مرّ مالح جدًا حامض جدًا مبالغ فيه مبالغ مرتفع مبالغ السعر بلا نَكهة بلا نكهةٍ بلا طعم ثقيل زيتي دسم جدًا غير متوازن غير مستوي ناقص
""".split())

BOILERPLATE_PATTERNS = [
    r"من\s+أجمل\s+.*مطاعم",
    r"لا\s+تفوت\s+.*تجربة",
    r"يقدم\s+.*قائمة\s+متنوعة\s+.*",
    r"الخيار\s+الأفضل\s+.*",
    r"يعتبر\s+.*من\s+أفضل\s+الخيارات",
    r"يتميز\s+.*بالجودة\s+العالية",
    r"أسعار\s+مناسبة\s+.*لجميع",  # تجنّب العموميات
]

ABSOLUTE_WORDS = set("دائمًا أبدًا كلّ جميع حتمًا مؤكد بلا_شك بلا_منافس الأفضل رقم_1 لا_يقارن لا_يُهزم".replace("_"," ").split())

# ——— أدوات مساعدة ———
def _normalize(s: str) -> str:
    s = s or ""
    return re.sub(r"\s+", " ", s.strip())

def _split_sentences(text: str):
    # تقسيم بسيط للجُمل
    text = _normalize(text)
    parts = re.split(r"(?<=[\.\!\؟\!])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]

def _split_paragraphs(text: str):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras

def _words(text: str):
    # كلمات عربية/إنجليزية/أرقام
    return re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", _normalize(text))

def _ttr(words):
    if not words: return 0.0
    return len(set(w.lower() for w in words)) / max(1, len(words))

def _ngrams(tokens, n):
    return [" ".join(tokens[i:i+n]) for i in range(len(tokens)-n+1)]

def _clip(x, lo, hi): return max(lo, min(hi, x))

def _percent(x, total): return (100.0 * x / total) if total else 0.0

def _extract_excerpt(text, pattern, span, pad=28):
    s, e = span
    s = max(0, s - pad)
    e = min(len(text), e + pad)
    return text[s:e].replace("\n", " ")

# ——— فحوصات ———
def quality_report(text: str) -> dict:
    text = text or ""
    words = _words(text)
    word_count = len(words)
    char_count = len(text)
    sentences = _split_sentences(text)
    sentence_count = len(sentences)
    paragraphs = _split_paragraphs(text)

    # أطوال الجمل والفقرات
    sent_lens = [len(_words(s)) for s in sentences] or [0]
    para_lens = [len(_words(p)) for p in paragraphs] or [0]
    avg_sent = sum(sent_lens)/len(sent_lens)
    avg_para = sum(para_lens)/len(para_lens)
    std_para = (sum((x-avg_para)**2 for x in para_lens)/len(para_lens))**0.5 if para_lens else 0.0
    pct_short_para = _percent(sum(1 for x in para_lens if x < 20), len(para_lens))
    pct_long_para  = _percent(sum(1 for x in para_lens if x > 100), len(para_lens))

    # تنوّع بدايات الجمل
    starts = []
    for s in sentences:
        toks = _words(s)
        if toks:
            starts.append(toks[0].lower())
    start_counts = Counter(starts)
    start_top = start_counts.most_common(5)
    start_hhi = sum((c/len(starts))**2 for _,c in start_top) if starts else 0.0  # تركّز البدايات

    # الحسّية
    sens_hits = sum(1 for w in words if w.lower() in SENSORY_TERMS)
    sensory_ratio = _percent(sens_hits, word_count)

    # تكرارات N-gram
    toks_lower = [w.lower() for w in words if w.lower() not in AR_STOPWORDS]
    bi = Counter(_ngrams(toks_lower, 2))
    tri = Counter(_ngrams(toks_lower, 3))
    repeated = []
    for gram, c in bi.most_common(30):
        if c >= 3 and len(gram.split()) >= 2:
            repeated.append((gram, c))
    for gram, c in tri.most_common(30):
        if c >= 2 and len(gram.split()) >= 3:
            repeated.append((gram, c))
    repeated = sorted(repeated, key=lambda x: (-x[1], -len(x[0])))

    # Boilerplate / عبارات قالبية
    boiler_flags = []
    for pat in BOILERPLATE_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            boiler_flags.append({"pattern": pat, "excerpt": _extract_excerpt(text, pat, m.span())})

    # صيغة المبني للمجهول التقريبية (تم + فعل)
    passive_hits = len(re.findall(r"\bتم\s+\w+", text))
    passive_ratio = _percent(passive_hits, max(1, sentence_count))

    # تنوّع المعجم
    ttr = _ttr(words)

    # عواطف تقريبية
    pos = sum(1 for w in words if w.lower() in POSITIVE_TERMS)
    neg = sum(1 for w in words if w.lower() in NEGATIVE_TERMS)
    sentiment = {
        "positive": pos, "negative": neg,
        "balance": "محايد" if abs(pos-neg)<=2 else ("مائل للإيجاب" if pos>neg else "مائل للانتقاد")
    }

    # كلمات مطلقة (خطر تعميم/تهويل)
    abs_count = sum(1 for w in words if w in ABSOLUTE_WORDS)

    # بنية العناوين (ماركداون)
    h2 = len(re.findall(r"(?m)^\s*##\s+", text))
    h3 = len(re.findall(r"(?m)^\s*###\s+", text))
    faq = 1 if re.search(r"(?im)^\s*##\s*أسئلة\s+شائعة|FAQ", text) else 0

    # إشارات E-E-A-T (تقريبية من الكلمات الدالّة)
    eeat_signals = {
        "experience": 1 if re.search(r"\b(جرّبت|زرنا|تذوّقنا|زيارة|تجربت|من واقع الخبرة)\b", text) else 0,
        "expertise":  1 if re.search(r"\b(تقنية|تحميص|تتبيل|درجات التسوية|معيار|منهجية|قرينة)\b", text) else 0,
        "author":     1 if re.search(r"\b(كاتب|محرر|فريق التحرير|توقيع|منهجية التحرير)\b", text) else 0,
        "trust":      1 if re.search(r"\b(شفافية|مصدر|إفصاح|حدود المنهجية|اعتمدنا)\b", text) else 0,
    }
    eeat_score = sum(eeat_signals.values()) / 4 * 100

    # Information Gain: وجود عناصر تضيف جديدًا (Pro Tip، مقارنات، سلبية صغيرة، تقسيم مكاني…)
    info_gain_points = 0
    if re.search(r"Pro Tip|نصيحة|تلميح|ملاحظة عملية", text, flags=re.I): info_gain_points += 1
    if re.search(r"مقارنة|أقرب\s+إلى|يشبه|يفوق|أقل", text): info_gain_points += 1
    if re.search(r"سلبية\s+صغيرة|نقطة\s+لِلتحسين|يمكن\s+تحسين|ملاحظة\s+سلبية", text): info_gain_points += 1
    if re.search(r"(شمال|جنوب|شرق|غرب|كورنيش|مول|ممشى|حي)\s", text): info_gain_points += 1
    if re.search(r"تجربة\s+.*(سابقة|أخرى)", text): info_gain_points += 1
    info_gain_score = _clip(info_gain_points / 5 * 100, 0, 100)

    # كثافة الحشو/العموميات: نحسبها تقريبًا من (boiler + absolutes + تكرارات مرتفعة) مقابل الكلمات
    fluff_units = len(boiler_flags) + abs_count + sum(c-2 for _, c in repeated if c >= 3)
    fluff_density = _percent(fluff_units, max(1, word_count//50))  # مقياس اصطناعي يزداد مع التكرار والعبارات القالبية

    # "درجة بشرية" مركّبة (0–100)
    # ↑ تزيد مع تنوّع البدايات (HHI منخفض)، حسّية أعلى، TTR أعلى، InfoGain أعلى
    # ↓ تقل مع المبني للمجهول العالي، الحشو العالي، الجمل الطويلة جدًا، تركّز بدايات الجمل
    hhi_penalty = _clip((start_hhi - 0.2) * 200, 0, 40)  # كلما ارتفع تركّز البدايات زادت العقوبة
    passive_penalty = _clip(passive_ratio, 0, 30)
    fluff_penalty = _clip(fluff_density, 0, 30)
    long_sent_penalty = 10 if avg_sent > 28 else 0

    base_score = 50
    base_score += _clip(sensory_ratio/2, 0, 20)         # حسّية (0–20)
    base_score += _clip(ttr*40, 0, 20)                  # تنوّع مفردات (0–20)
    base_score += _clip(info_gain_score/5, 0, 20)       # IG (0–20)
    base_score -= (hhi_penalty + passive_penalty + fluff_penalty + long_sent_penalty)

    human_style_score = _clip(base_score, 0, 100)

    # تجميع المخرجات
    return {
        "char_count": char_count,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "paragraph_count": len(paragraphs),
        "avg_sentence_length": round(avg_sent, 2),
        "paragraph_metrics": {
            "avg_len": round(avg_para, 1),
            "std_len": round(std_para, 1),
            "pct_short_lt20w": round(pct_short_para, 1),
            "pct_long_gt100w": round(pct_long_para, 1),
        },
        "sentence_variety": {
            "start_top": start_top,            # [('أما', 6), ('هذا', 4), ...]
            "start_hhi": round(start_hhi, 3),  # تركّز البدايات (0–1)
        },
        "ttr": round(ttr, 3),
        "sensory_ratio": round(sensory_ratio, 2),
        "passive_ratio": round(passive_ratio, 2),
        "boilerplate_flags": boiler_flags,     # [{pattern, excerpt}, ...]
        "repeated_phrases": repeated[:15],
        "absolutes_count": abs_count,
        "sentiment": sentiment,                # {positive, negative, balance}
        "headings": {"h2": h2, "h3": h3, "faq_sections": faq},
        "eeat": eeat_signals,
        "eeat_score": round(eeat_score, 1),
        "info_gain_score": round(info_gain_score, 1),
        "fluff_density": round(fluff_density, 2),
        "human_style_score": round(human_style_score, 1),
        "tips": _build_tips(
            sensory_ratio, ttr, passive_ratio, fluff_density, start_hhi,
            avg_sent, info_gain_score, eeat_score, abs_count, repeated, boiler_flags
        ),
    }

def _build_tips(sensory_ratio, ttr, passive_ratio, fluff_density, start_hhi,
                avg_sent, info_gain_score, eeat_score, abs_count, repeated, boiler):
    tips = []
    if sensory_ratio < 0.8:
        tips.append("أضِف أوصافًا حسّية دقيقة لطبق أو اثنين (قوام/تحمير/حرارة) لرفع الحسّية.")
    if ttr < 0.35:
        tips.append("نوّع المفردات وتجنّب تكرار نفس الصفات.")
    if passive_ratio > 12:
        tips.append("خفّف صيغة المبني للمجهول؛ فضّل أفعالًا مباشرة (جرّبنا/لاحظنا).")
    if fluff_density > 25:
        tips.append("احذف العموميات وبدّلها بأمثلة محددة قابلة للتحقق.")
    if start_hhi > 0.28:
        tips.append("ابدأ الجمل بطرق مختلفة (حالات/زمن/جار ومجرور/شرط) لكسر الرتابة.")
    if avg_sent > 28:
        tips.append("قسّم الجمل الطويلة (>28 كلمة) إلى جملتين واضحتيْن.")
    if info_gain_score < 60:
        tips.append("أضِف Pro Tip عمليًا أو مقارنة موجزة أو سلبية صغيرة متوازنة لرفع Information Gain.")
    if eeat_score < 50:
        tips.append("أبرز خبرة مباشرة أو منهجية تحرير مختصرة لتعزيز E-E-A-T.")
    if abs_count > 0:
        tips.append("خفّف من الكلمات المطلقة (مثل: دائمًا/الأفضل) واستبدلها بتوصيف قابل للنقاش.")
    if repeated[:1]:
        tips.append("هناك عبارات متكررة؛ راجع أكثر N-grams تكرارًا وخفف تكرارها.")
    if boiler[:1]:
        tips.append("توجد عبارات قالبية عامة؛ استبدلها بتفاصيل ملموسة من التجربة.")
    return tips
