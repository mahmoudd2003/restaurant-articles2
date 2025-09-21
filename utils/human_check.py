import re, math
from typing import Dict

def _sentences(text: str):
    return [s.strip() for s in re.split(r"[.!؟?\n]+", text or "") if s.strip()]

def _variance(nums):
    if not nums: return 0.0
    m = sum(nums)/len(nums)
    return sum((x-m)**2 for x in nums)/len(nums)

def human_likeness_report(text: str, sources_text: str="") -> Dict:
    sents = _sentences(text)
    lens = [len(s.split()) for s in sents]
    var_len = _variance(lens)
    uniq_ratio = len(set(text.split())) / (len(text.split()) or 1)
    rep_ratio = 1 - uniq_ratio
    src_overlap = 0.0
    if sources_text:
        src_tokens = set(sources_text.split())
        overlap = [1 for w in text.split() if w in src_tokens]
        src_overlap = len(overlap) / (len(text.split()) or 1)

    # تقدير مبسّط: تباين الجمل الأعلى + تكرار أقل + تطابق مصادر أقل => بشرية أعلى
    score = 60 + min(20, var_len) - (rep_ratio*30) - (src_overlap*30)
    score = max(0.0, min(100.0, score))

    hints = []
    if rep_ratio > 0.4: hints.append("خفض التكرار بإعادة صياغة الأفكار.")
    if var_len < 10: hints.append("نوّع أطوال الجمل لتبدو طبيعية.")
    if src_overlap > 0.25: hints.append("ابتعد أكثر عن صياغة المصادر المباشرة.")

    return {
        "human_likeness_score_0_100": round(score, 2),
        "sentence_count": len(sents),
        "avg_sentence_words": round(sum(lens)/(len(lens) or 1), 2) if lens else 0,
        "repetition_ratio": round(rep_ratio, 3),
        "source_overlap_ratio": round(src_overlap, 3),
        "suggestions": hints
    }
