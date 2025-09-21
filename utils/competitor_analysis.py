from utils.openai_client import chat_complete

ANALYSIS_PROMPT = """أنت محرّر SEO وناقد طعام صارم.
حلّل صفحتين تتصدّران نتائج Google لاستعلام: "{query}"
النطاق المكاني: {place_scope}
النبرة التحليلية: {tone_label} (وزن المراجعات: {reviews_weight}%)

المطلوب إخراجه بصيغة Markdown واضحة:
# تحليل المنافسين (Top 2)
## الصفحة A
- **عنوان الصفحة:** {title_a}
- **نقاط القوة (3–6):**
- **ثغرات/ملاحظات للتحسين (3–6):**
- **E-E-A-T على مستوى الصفحة:** (Experience / Expertise / Author-Transparency / Trust) مع أمثلة قصيرة من النص.
- **مطابقة القصد Intent:** ماذا يتوقع الباحث وهل لبّته الصفحة؟
- **تفسير اختيار Google (محتوى فقط):** 3 أسباب مرجّحة.
- **تقييم موجز (0–5):**
  - Intent Match:
  - Depth & Information Gain:
  - Structure & Readability:
  - Practicality:
  - Freshness & Maintenance:
  - E-E-A-T:

## الصفحة B
- **عنوان الصفحة:** {title_b}
- **نقاط القوة (3–6):**
- **ثغرات/ملاحظات للتحسين (3–6):**
- **E-E-A-T على مستوى الصفحة:** (Experience / Expertise / Author-Transparency / Trust) مع أمثلة قصيرة من النص.
- **مطابقة القصد Intent:** ماذا يتوقع الباحث وهل لبّته الصفحة؟
- **تفسير اختيار Google (محتوى فقط):** 3 أسباب مرجّحة.
- **تقييم موجز (0–5):**
  - Intent Match:
  - Depth & Information Gain:
  - Structure & Readability:
  - Practicality:
  - Freshness & Maintenance:
  - E-E-A-T:

## مقارنة مباشرة
- من الأقوى في كل محور؟ ولماذا؟
- عناصر Information Gain المفقودة لديهما.

## Gap-to-Win (قابلة للحقن)
- 5–8 توصيات عملية محددة لإدخالها في مقالك القادم لتتفوّق عليهما.
- يجب أن تكون مستقلة عن سلطة النطاق/الروابط، وتركّز على المحتوى فقط.
"""

def build_prompt(page_a: dict, page_b: dict, query: str, place_scope: str, tone_label: str, reviews_weight: int) -> str:
    ta = page_a.get("title") or page_a.get("url")
    tb = page_b.get("title") or page_b.get("url")
    snippet_a = (page_a.get("text","")[:3000])
    snippet_b = (page_b.get("text","")[:3000])
    return ANALYSIS_PROMPT.format(
        query=query, place_scope=place_scope, tone_label=tone_label, reviews_weight=reviews_weight, title_a=ta, title_b=tb
    ) + f"\n\n\n[مقتطفات الصفحة A]\n{snippet_a}\n\n[مقتطفات الصفحة B]\n{snippet_b}\n"

def analyze_competitors(client, model, fallback_model, page_a, page_b, query, place_scope, tone_label, reviews_weight=60):
    msgs = [
        {"role": "system", "content": "أنت محرر عربي خبير SEO وE-E-A-T، تحلل المحتوى فقط دون ذكر سلطة النطاق أو الروابط."},
        {"role": "user", "content": build_prompt(page_a, page_b, query, place_scope, tone_label, reviews_weight)},
    ]
    return chat_complete(client, msgs, model=model, fallback_model=fallback_model, temperature=0.4, max_tokens=2200)

def extract_gap_points(analysis_md: str) -> str:
    import re
    m = re.search(r"##\s*Gap-to-Win[^\n]*\n(.+)", analysis_md, flags=re.DOTALL|re.IGNORECASE)
    if not m:
        return ""
    block = m.group(1)
    lines = []
    for line in block.splitlines():
        if line.strip().startswith(("#","##")):
            break
        if line.strip().startswith(("-", "*", "•")):
            lines.append(line.strip("-*• ").strip())
    return "\n".join(lines[:12])
