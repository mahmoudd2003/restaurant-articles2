# app.py
# -*- coding: utf-8 -*-

import os, sys, uuid, json, time, hashlib
from typing import List, Dict, Any, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st

# === استيرادات داخلية (موجودة في utils بهذا الباك) ===
from utils.logging_setup import (
    init_logging, get_logger, set_correlation_id, log_exception
)
from utils.content_fetch import fetch_and_extract, configure_http_cache
from utils.openai_client import chat_complete_cached
from utils.exporters import to_docx, to_json
from utils.competitor_analysis import analyze_competitors, extract_gap
from utils.quality_checks import quality_report

# ===========
#  تهيئة عام
# ===========
os.makedirs("data", exist_ok=True)
init_logging(app_name="restoguide", level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger("app")
set_correlation_id(str(uuid.uuid4())[:8])

# =========
#  Helpers
# =========
def parse_urls_block(block: str) -> List[str]:
    urls = []
    for line in (block or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("http://", "https://")):
            urls.append(s)
    return urls

def normalize_json_or_text(s: str) -> Tuple[Optional[Any], str]:
    """
    يحاول قراءة s كـ JSON إذا بدأ بـ { أو [
    (أُصلحت الأقواس: tuple داخل startswith)
    """
    s = (s or "").strip()
    if not s:
        return None, ""
    try:
        if s.startswith(("[", "{")):
            return json.loads(s), ""
    except Exception:
        pass
    return None, s

def hash_messages(messages: List[Dict[str, Any]], model: str) -> str:
    raw = json.dumps({"m": messages, "model": model}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class LLMCacher:
    def __init__(self, ttl_hours: int = 24):
        self.ttl = ttl_hours * 3600
        self.mem: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str):
        row = self.mem.get(key)
        if not row:
            return None
        ts, val = row
        if time.time() - ts > self.ttl:
            self.mem.pop(key, None)
            return None
        return val

    def set(self, key: str, value: Any):
        self.mem[key] = (time.time(), value)

# =========
#  واجهة UI
# =========
st.set_page_config(page_title="RestoGuide", page_icon="🍽️", layout="wide")
st.title("🍽️ مُولّد مقالات المطاعم (RestoGuide)")

st.markdown(
    "هذا التطبيق يُولّد مقالات/أدلة مطاعم بالاعتماد على روابط تُزوّدها، "
    "مع دعم تحليل منافسين وفحص جودة اختياري."
)

# Sidebar — إعدادات عامة
st.sidebar.header("⚙️ الإعدادات")

# كاش HTTP
st.sidebar.subheader("🛰️ كاش HTTP")
http_cache_enabled = st.sidebar.checkbox("تفعيل كاش HTTP", value=True, key="opt_http_cache_enabled")
http_cache_hours   = st.sidebar.slider("مدة (ساعات)", 1, 72, 24, key="opt_http_cache_hours")
configure_http_cache(ttl_hours=int(http_cache_hours), enabled=bool(http_cache_enabled))

st.sidebar.markdown("---")

# كاش LLM
st.sidebar.subheader("🧠 كاش LLM")
llm_cache_enabled = st.sidebar.checkbox("تفعيل كاش LLM", value=True, key="opt_llm_cache_enabled")
llm_cache_hours   = st.sidebar.slider("TTL LLM Cache (ساعات)", 1, 72, 24, key="opt_llm_cache_hours")

if "llm_cacher" not in st.session_state:
    st.session_state["llm_cacher"] = LLMCacher(ttl_hours=int(llm_cache_hours))
else:
    st.session_state["llm_cacher"].ttl = int(llm_cache_hours) * 3600

# إعدادات LLM
st.sidebar.subheader("🤖 نموذج الذكاء")
model_name = st.sidebar.selectbox(
    "النموذج",
    ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
    index=0,
    key="opt_model_name",
)
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.2, 0.1, key="opt_temperature")
max_tokens  = st.sidebar.slider("Max Tokens", 256, 8192, 2048, 64, key="opt_max_tokens")

st.sidebar.markdown("---")
st.sidebar.caption("تأكد من تعيين `OPENAI_API_KEY` في إعدادات التشغيل (Streamlit Cloud Secrets).")

# Main inputs
st.subheader("🧾 بيانات المقال")
col1, col2 = st.columns([2, 1])

with col1:
    topic  = st.text_input("الموضوع/الفئة", key="inp_topic", placeholder="مثال: أفضل مطاعم برجر في الرياض")
    area   = st.text_input("المدينة/النطاق الجغرافي (اختياري)", key="inp_area")
    tone   = st.selectbox("النبرة الكتابية", ["احترافية", "حماسية", "ودّية", "مختصرة", "سياحية"], index=0, key="inp_tone")
    length = st.selectbox("الطول التقريبي", ["قصير", "متوسط", "طويل"], index=1, key="inp_length")

with col2:
    do_comp    = st.checkbox("تشغيل تحليل المنافسين", value=False, key="opt_do_comp")
    do_quality = st.checkbox("تشغيل فحص الجودة", value=True, key="opt_do_quality")
    out_docx   = st.checkbox("توليد ملف DOCX", value=True, key="opt_out_docx")
    out_json   = st.checkbox("توليد ملف JSON", value=True, key="opt_out_json")

st.subheader("🔗 روابط مصادر (واحدة في كل سطر)")
urls_block = st.text_area("ألصق روابط مقالات/قوائم مطاعم", height=120, key="inp_urls_block")
urls = parse_urls_block(urls_block)

st.subheader("📝 ملاحظات/تعليمات إضافية (اختياري)")
notes = st.text_area("اكتب أي تعليمات خاصة ترغب تضمينها في المقال", height=100, key="inp_notes")

generate = st.button("🚀 توليد المقال الآن", type="primary", key="btn_generate")

# ==========================
#       منطق التوليد
# ==========================
def build_prompt(topic: str, area: str, tone: str, length: str, notes: str, sources: List[Dict[str, Any]]):
    sys_prompt = (
        "أنت محرّر محتوى مختص في أدلة المطاعم. اكتب مقالات عربية واضحة، مُحايدة، "
        "متوافقة مع ممارسات السيو، مع عناوين فرعية ونقاط حيث يلزم."
    )

    user_parts = []
    if topic:
        user_parts.append(f"الموضوع: {topic}")
    if area:
        user_parts.append(f"النطاق الجغرافي: {area}")
    user_parts.append(f"النبرة: {tone}")
    user_parts.append(f"الطول: {length}")

    if notes.strip():
        user_parts.append(f"ملاحظات إضافية: {notes.strip()}")

    if sources:
        src_short = []
        for s in sources:
            title = (s.get("title") or "").strip()
            url = s.get("url") or ""
            txt  = (s.get("text") or "")[:1200]
            src_short.append({"title": title, "url": url, "snippet": txt})
        user_parts.append("ملخص للمصادر (عينة):\n" + json.dumps(src_short, ensure_ascii=False, indent=2))

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

def call_llm(messages: List[Dict[str, Any]], model: str) -> str:
    cache_key = hash_messages(messages, model)
    if llm_cache_enabled:
        cached = st.session_state["llm_cacher"].get(cache_key)
        if cached:
            return cached

    resp = chat_complete_cached(messages=messages, model=model,
                                temperature=temperature, max_tokens=max_tokens)
    # متوافق مع openai 1.x (Chat Completions)
    text = ""
    try:
        text = resp.choices[0].message.content  # type: ignore
    except Exception:
        text = str(resp)

    if llm_cache_enabled:
        st.session_state["llm_cacher"].set(cache_key, text)
    return text

def fetch_sources(urls: List[str]) -> List[Dict[str, Any]]:
    if not urls:
        return []
    req_id = str(uuid.uuid4())[:8]
    logger.info("restoguide | %s | app | places.fetch.start", req_id)
    out = []
    for u in urls:
        logger.info("restoguide | - | places | places.request")
        data = fetch_and_extract(u)
        out.append(data)
    # إزالة تكرارات حسب URL
    dedup = {}
    for row in out:
        dedup[row.get("url")] = row
    logger.info("restoguide | - | places | places.dedupe")
    rows = list(dedup.values())
    logger.info("restoguide | - | app | places.fetch.done")
    return rows

def export_outputs(article_text: str, meta: Dict[str, Any]):
    downloads = []
    if out_docx:
        docx_name = f"article_{int(time.time())}.docx"
        path = to_docx(article_text, docx_name)
        downloads.append(("DOCX", path))
    if out_json:
        json_name = f"article_{int(time.time())}.json"
        path = to_json(meta, json_name)
        downloads.append(("JSON", path))
    return downloads

# ==========================
#         التنفيذ
# ==========================
if generate:
    if not topic.strip():
        st.error("يرجى إدخال موضوع/فئة للمقال.")
        st.stop()

    sources = fetch_sources(urls) if urls else []

    msgs = build_prompt(topic=topic, area=area, tone=tone, length=length, notes=notes, sources=sources)

    try:
        article_text = call_llm(msgs, model=model_name)
        logger.info("restoguide | - | app | places.accepted")
    except Exception as e:
        log_exception(e)
        # لو ما فيه API Key — بنرجّع نص بديل بدل ما تتوقف الصفحة
        article_text = (
            "⚠️ لم يتم العثور على `OPENAI_API_KEY` أو فشل الاتصال بـ API.\n\n"
            "— نص تجريبي بديل —\n"
            f"عنوان: {topic}\n"
            f"المنطقة: {area or 'غير محددة'}\n"
            "مقدمة مختصرة…\n\n"
            "• مطعم 1: وصف مختصر.\n"
            "• مطعم 2: وصف مختصر.\n"
            "• مطعم 3: وصف مختصر.\n\n"
            "خاتمة ونصائح للحجز وأوقات الذروة…"
        )

    parsed, plain = normalize_json_or_text(article_text)
    final_text = ""
    meta: Dict[str, Any] = {
        "topic": topic, "area": area, "tone": tone, "length": length, "notes": notes,
        "sources_count": len(sources), "model": model_name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if isinstance(parsed, dict) and parsed.get("article"):
        final_text = str(parsed.get("article"))
        meta["structure"] = "json"
        meta["raw"] = parsed
    else:
        final_text = plain or article_text
        meta["structure"] = "text"

    if do_comp and sources:
        comp = analyze_competitors([s.get("text", "") for s in sources])
        meta["competitor_analysis"] = comp
        meta["content_gaps"] = extract_gap(comp)

    if do_quality:
        meta["quality_report"] = quality_report(final_text)

    st.subheader("✍️ المقال الناتج")
    st.text_area("النص النهائي", value=final_text, height=400, key="out_final_article")

    st.subheader("⬇️ تنزيلات")
    downloads = export_outputs(final_text, meta)
    if not downloads:
        st.info("لم يتم تفعيل أي خيار تنزيل.")
    else:
        for kind, path in downloads:
            st.markdown(f"- **{kind}**: [تحميل الملف]({path})")

st.markdown("---")
st.caption("© 2025 RestoGuide — يعمل بواسطة Streamlit و OpenAI. ")
