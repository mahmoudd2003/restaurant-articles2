from typing import List, Dict, Any
import re, math

def _count_words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s or "") if w.strip()])

def _avg_sentence_len(s: str) -> float:
    sents = re.split(r"[.!؟?\n]+", s or "")
    sents = [x.strip() for x in sents if x.strip()]
    if not sents: return 0.0
    return sum(_count_words(x) for x in sents) / len(sents)

def _kw_density(text: str, kw: str) -> float:
    if not text or not kw: return 0.0
    total = _count_words(text)
    occ = len(re.findall(re.escape(kw), text, flags=re.IGNORECASE))
    return (occ / max(total,1)) * 100

def _readability(text: str) -> float:
    # تقدير مبسّط لقابلية القراءة بالعربية: أقصر جمل + كلمات أقصر = أسهل
    L = _avg_sentence_len(text)
    avg_word_len = sum(len(w) for w in re.findall(r"\w+", text)) / (len(re.findall(r"\w+", text)) or 1)
    score = max(0.0, 100 - (L*1.2 + avg_word_len*6))
    return round(score, 2)

def quality_report(draft_txt: str, target_kw: str, topic: str, related_kws: List[str], places: List[Dict[str,Any]], desired_words: int) -> Dict[str, Any]:
    wc = _count_words(draft_txt)
    dens = _kw_density(draft_txt, target_kw)
    read = _readability(draft_txt)
    has_h2 = bool(re.search(r"(^|\n)##?\s", draft_txt)) or "##" in draft_txt
    tips = []
    if wc < desired_words*0.8:
        tips.append("النص أقصر من المطلوب—أضف تفاصيل أو أمثلة أو مقارنات.")
    if dens < 0.2:
        tips.append("كثافة الكلمة المستهدفة منخفضة—اذكرها طبيعيًا في العناوين/الافتتاح/الخاتمة.")
    if read < 40:
        tips.append("النص صعب القراءة—قلّل طول الجمل واستبدل التراكيب المعقدة.")
    if not has_h2:
        tips.append("أضف عناوين فرعية H2/H3 واضحة.")
    if places and "جدول" not in draft_txt:
        tips.append("لديك بيانات مطاعم—أضف جدولًا مختصرًا يسهّل المقارنة.")
    # تحقق مرتبط بالكلمات
    missing_related = [k for k in related_kws[:10] if not re.search(re.escape(k), draft_txt, re.IGNORECASE)]
    if missing_related:
        tips.append("أدرج الكلمات المرتبطة المفقودة بصورة طبيعية: " + ", ".join(missing_related[:10]))

    return {
        "word_count": wc,
        "target_kw_density_pct": round(dens, 2),
        "readability_score_0_100": read,
        "has_subheads": has_h2,
        "suggestions": tips
    }

def build_jsonld(topic: str, area: str, draft: str, places: List[Dict[str,Any]], language: str="العربية") -> Dict[str,Any]:
    # Article + Optional LocalBusiness list
    article = {
        "@context":"https://schema.org",
        "@type":"Article",
        "inLanguage":"ar" if "عرب" in language or language=="العربية" else "en",
        "headline": topic or "",
        "articleBody": (draft or "")[:5000]
    }
    if places:
        lbs = []
        for p in places[:20]:
            lbs.append({
                "@type":"LocalBusiness",
                "name": p.get("name"),
                "address": {"addressLocality": area or p.get("vicinity","")},
                "telephone": p.get("formatted_phone_number"),
                "url": p.get("website") or p.get("maps_url"),
                "aggregateRating": {
                    "@type":"AggregateRating",
                    "ratingValue": p.get("rating"),
                    "reviewCount": p.get("user_ratings_total")
                }
            })
        article["about"] = lbs
    return article
