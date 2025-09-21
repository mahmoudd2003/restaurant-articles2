from typing import List, Dict, Any
import re
from collections import Counter
from .openai_client import chat_complete_cached

STOP = set(["في","من","على","عن","إلى","الى","و","أو","هذا","هذه","ذلك","تلك","كما","مع","حتى","ثم","قد","هو","هي","هم","هن","أن","إن","كان","كانت","ما","لا","لم","لن","إذا","اذا","بعد","قبل","بين","أكثر","أفضل","أفضل","مطاعم","مطعم"])

def _simple_candidates(text: str) -> List[str]:
    words = [w for w in re.findall(r"[أ-يA-Za-z0-9\-]+", text or "") if w not in STOP and len(w) > 2]
    cnt = Counter(words)
    return [w for w,_ in cnt.most_common(50)]

def related_keywords(topic: str, target_kw: str, texts: List[str], model: str, temperature: float, max_tokens: int, llm_cacher=None) -> List[str]:
    seed = []
    for t in texts:
        seed += _simple_candidates(t)
    seed = list(dict.fromkeys(seed))[:50]

    user = f"""
أعطني قائمة كلمات/عبارات مرتبطة دلاليًا بموضوع "{topic}" والكلمة المستهدفة "{target_kw}" (عربية غالبًا).
- استخدم أقصى 25 كلمة/عبارة.
- لا تكرر كلمات عامة بلا قيمة.
- إن كانت هناك تسميات أماكن/أحياء/أطباق مشهورة، أدرج بعضها.
بذور محتملة إن أفادت:
{", ".join(seed)}
""".strip()

    messages = [
        {"role":"system","content":"خبير SEO عربي يقترح كلمات مرتبطة ذات منفعة بحثية."},
        {"role":"user","content": user}
    ]
    key = None
    if llm_cacher:
        key = "rkws:" + str(hash(user))
        cached = llm_cacher.get(key)
        if cached:
            return cached
    out = chat_complete_cached(messages, model=model, temperature=temperature, max_tokens=max_tokens)
    kws = []
    for line in out.splitlines():
        s = re.sub(r"^[\-\*\d\.\)\s]+","", line).strip()
        if s and len(s) <= 40:
            kws.append(s)
    kws = list(dict.fromkeys(kws))[:25]
    if llm_cacher and key:
        llm_cacher.set(key, kws)
    return kws
