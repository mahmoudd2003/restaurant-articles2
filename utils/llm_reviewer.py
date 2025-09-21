from utils.openai_client import chat_complete

REVIEW_PROMPT = """أنت محرر جودة عربي. شخّص النص التالي بحثًا عن:
- عبارات قالبية/حشو.
- ضعف الحسّية (غياب وصف رائحة/قوام/تحمير…).
- تكرار الصياغة بين الجمل/المطاعم.
- ضعف E-E-A-T (خبرة مباشرة، مصطلحات تقنية، شفافية/منهجية، ثقة).
- نقص Information Gain (Pro Tips مكانية، مقارنات، تقسيم منطقي).

أخرج تقريرًا ماركداون يحتوي:
## تشخيص موجز
- نقاط القوة (3–5)
- أبرز المشكلات (3–7)

## أسطر/مقاطع تتطلب تحسينًا
- اقتبس السطر (مختصر)، اذكر السبب، ثم أعطِ **بديلًا مقترحًا** أكثر حسّيّة وتخصيصًا.

## توصيات تحرير
- 5–10 توصيات عملية محددة.
"""

FIX_PROMPT = """أعد كتابة المقاطع المعلّمة فقط بأسلوب عربي طبيعي وحسّي، وبدون روابط أو أسعار، مع الحد الأدنى من التعديل.
حافظ على الحقائق ولا تغيّر نطاق المقال. لو تعذّر التحسين، اترك السطر كما هو.

النص الأصلي:
{orig}

المقاطع المطلوب تحسينها (بين علامات <<< >>>):
{flag_block}

أعد النص الكامل بعد التحسين، لا تشرح.
"""

def llm_review(client, model, fallback_model, text: str):
    msgs = [
        {"role": "system", "content": "أنت محرر جودة عربي، خبير E-E-A-T."},
        {"role": "user", "content": REVIEW_PROMPT + "\n\n" + text[:8000]},
    ]
    return chat_complete(client, msgs, model=model, fallback_model=fallback_model, temperature=0.4, max_tokens=1800)

def llm_fix(client, model, fallback_model, text: str, flagged_lines: list):
    block = "\n".join([f"<<<{ln}>>>" for ln in flagged_lines if ln.strip()])[:6000]
    msgs = [
        {"role": "system", "content": "أنت محرر عربي يعيد صياغة المقاطع المحددة فقط بدقة."},
        {"role": "user", "content": FIX_PROMPT.format(orig=text[:8000], flag_block=block)},
    ]
    return chat_complete(client, msgs, model=model, fallback_model=fallback_model, temperature=0.5, max_tokens=2400)
