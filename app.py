# app.py
# -*- coding: utf-8 -*-

import os, sys, uuid, json, time, hashlib, math
from typing import List, Dict, Any, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import streamlit as st
import pandas as pd

# === استيرادات داخلية ===
from utils.logging_setup import init_logging, get_logger, set_correlation_id, log_exception
from utils.content_fetch import fetch_and_extract, configure_http_cache
from utils.openai_client import chat_complete_cached
from utils.exporters import to_docx, to_json
from utils.competitor_analysis import analyze_competitors, extract_gap
from utils.quality_checks import quality_report, build_jsonld
from utils.google_places import fetch_places_for_topic
from utils.keywords import related_keywords
from utils.human_check import human_likeness_report

# ========= تهيئة عامة =========
os.makedirs("data", exist_ok=True)
init_logging(app_name="restoguide", level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger("app")
set_correlation_id(str(uuid.uuid4())[:8])

# ========= Helpers =========
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

# ========= واجهة =========
st.set_page_config(page_title="RestoGuide", page_icon="🍽️", layout="wide")
st.title("🍽️ مُولِّد مقالات المطاعم (RestoGuide) — النسخة الكاملة")

st.markdown(
    "- يجلب مصادر من الروابط التي تُدخلها.\n"
    "- **اختياريًا** يجلب قائمة مطاعم من **خرائط Google** ويضمّن البيانات.\n"
    "- يستخرج **الكلمات المرتبطة**، ويقترح عناوين وأسئلة شائعة وصور.\n"
    "- يُجري **تحققًا تقريبيًا من بشرية المحتوى** مع نقاط/تحليل.\n"
    "- فحوصات **جودة/SEO** (H1/H2، طول، كثافة، قابلية القراءة، تكرار، روابط…).\n"
    "- **تحليل منافسين** وفجوات محتوى.\n"
    "- **تصدير** DOCX وJSON، وتضمين **Schema.org JSON-LD**."
)

# Sidebar
st.sidebar.header("⚙️ الإعدادات")

# كاش HTTP
st.sidebar.subheader("🛰️ كاش HTTP")
http_cache_enabled = st.sidebar.checkbox("تفعيل كاش HTTP", value=True, key="opt_http_cache_enabled")
http_cache_hours   = st.sidebar.slider("مدة الكاش (ساعات)", 1, 72, 24, key="opt_http_cache_hours")
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
st.sidebar.caption("أضف `OPENAI_API_KEY` و (اختياريًا) `GOOGLE_MAPS_API_KEY` في Secrets.")

# Main inputs
st.subheader("🧾 بيانات المقال")
c1, c2, c3 = st.columns([2, 1.2, 1])
with c1:
    topic  = st.text_input("الموضوع/الفئة", key="inp_topic", placeholder="مثال: أفضل مطاعم برجر في الرياض")
with c2:
    area   = st.text_input("المدينة/المنطقة (اختياري)", key="inp_area", placeholder="الرياض")
with c3:
    target_kw = st.text_input("الكلمة المستهدفة (SEO)", key="inp_target_kw", placeholder="برجر الرياض الأفضل")

tone = st.selectbox("نبرة الكتابة", ["احترافي", "ودود", "متحمس", "موضوعي"], index=1, key="inp_tone")
lang = st.selectbox("اللغة", ["العربية", "English"], index=0, key="inp_lang")
desired_words = st.slider("الطول المستهدف (كلمات)", 400, 3000, 1200, 50, key="inp_words")

st.markdown("### 🔗 مصادر المحتوى (روابط، سطر لكل رابط)")
urls_block = st.text_area("ألصق الروابط هنا:", key="inp_urls", height=140, placeholder="https://example.com/..")
urls = parse_urls_block(urls_block)

st.markdown("---")

# ====== Tabs ======
tab_sources, tab_places, tab_outline, tab_draft, tab_quality, tab_export = st.tabs(
    ["📥 المصادر", "📍 المطاعم (خرائط قوقل)", "🗂️ المخطط", "✍️ المسودة", "🧪 الجودة/SEO", "📤 التصدير"]
)

# ---------- 📥 المصادر ----------
with tab_sources:
    st.subheader("جلب/استخلاص المحتوى من الروابط")
    if st.button("جلب المحتوى من الروابط", key="btn_fetch_sources"):
        try:
            docs = fetch_and_extract(urls)
            st.session_state["docs"] = docs
            st.success(f"تم جلب {len(docs)} مصدرًا.")
            for d in docs:
                with st.expander(d.get("url", "مصدر"), expanded=False):
                    st.write(d.get("title") or "")
                    st.write(d.get("text")[:1500] + ("..." if len(d.get("text","")) > 1500 else ""))
        except Exception as e:
            log_exception(logger, e)
            st.error(f"فشل الجلب: {e}")

    cka, ckb, ckc = st.columns([1, 1, 1])
    with cka:
        if st.button("الكلمات المرتبطة", key="btn_related_keywords"):
            src_texts = [d.get("text","") for d in st.session_state.get("docs", [])]
            kws = related_keywords(topic=topic, target_kw=target_kw, texts=src_texts, model=model_name,
                                   temperature=temperature, max_tokens=max_tokens,
                                   llm_cacher=st.session_state["llm_cacher"] if llm_cache_enabled else None)
            st.session_state["related_kws"] = kws
            st.success("تم استخراج/اقتراح الكلمات المرتبطة.")
            st.dataframe(pd.DataFrame({"keyword": kws}), use_container_width=True)
    with ckb:
        if st.button("تحليل منافسين", key="btn_competitors"):
            docs = st.session_state.get("docs", [])
            comp = analyze_competitors(docs)
            st.session_state["competitors"] = comp
            st.success("تم تحليل المنافسين.")
            st.json(comp)
    with ckc:
        if st.button("فجوات المحتوى", key="btn_gap"):
            docs = st.session_state.get("docs", [])
            comp = st.session_state.get("competitors", {})
            gaps = extract_gap(topic=topic, docs=docs, competitor_summary=comp)
            st.session_state["gaps"] = gaps
            st.success("تم توليد الفجوات.")
            st.json(gaps)

# ---------- 📍 المطاعم ----------
with tab_places:
    st.subheader("جلب مطاعم من خرائط Google (اختياري)")
    enable_places = st.checkbox("تفعيل جلب المطاعم", value=False, key="opt_enable_places")
    qcol1, qcol2 = st.columns([2,1])
    with qcol1:
        places_query = st.text_input("بحث (مثال: Burger restaurants)", value="مطاعم برجر", key="inp_places_query")
    with qcol2:
        places_limit = st.slider("عدد النتائج", 5, 50, 15, 1, key="inp_places_limit")
    if st.button("جلب المطاعم", key="btn_fetch_places") and enable_places:
        try:
            places = fetch_places_for_topic(query=places_query, area=area, limit=places_limit)
            st.session_state["places"] = places
            st.success(f"تم جلب {len(places)} مطعمًا.")
            if places:
                df = pd.DataFrame(places)
                st.dataframe(df, use_container_width=True)
        except Exception as e:
            log_exception(logger, e)
            st.error(f"تعذَّر الجلب من Places: {e}")

# ---------- 🗂️ المخطط ----------
with tab_outline:
    st.subheader("توليد مخطط المقال")
    include_faq = st.checkbox("تضمين أسئلة شائعة", value=True, key="opt_include_faq")
    include_schema = st.checkbox("تضمين سكيما JSON-LD", value=True, key="opt_include_schema")
    prompt_outline = f"""
اكتب مخططًا شاملًا لمقال عن: "{topic}" في منطقة "{area}" باللغة {lang}.
- نبرة: {tone}. طول مستهدف: {desired_words} كلمة.
- الكلمة المستهدفة: {target_kw}
- إن وُجدت فجوات محتوى، عالجها.
- إن وُجدت قائمة مطاعم (اسم/تقييم/سعر/رابط خرائط)، اقترح أقسامًا تقارن وتشرح.
- عناوين H2/H3 واضحة وغنية بالكلمات المفتاحية بدون حشو.
- خاتمة + CTA.
    """.strip()

    if st.button("توليد المخطط", key="btn_gen_outline"):
        try:
            messages = [
                {"role":"system","content":"أنت خبير تحرير SEO وصياغة مخططات مقالات عربية متينة."},
                {"role":"user","content": prompt_outline}
            ]
            cache_key = hash_messages(messages, model_name)
            if llm_cache_enabled and (cached := st.session_state["llm_cacher"].get(cache_key)):
                outline = cached
            else:
                outline = chat_complete_cached(messages, model=model_name, temperature=temperature, max_tokens=max_tokens)
                if llm_cache_enabled:
                    st.session_state["llm_cacher"].set(cache_key, outline)
            st.session_state["outline"] = outline
            st.success("تم توليد المخطط.")
            st.text_area("المخطط", value=outline, key="out_outline", height=350)
        except Exception as e:
            log_exception(logger, e)
            st.error(f"فشل توليد المخطط: {e}")

# ---------- ✍️ المسودة ----------
with tab_draft:
    st.subheader("توليد المسودة")
    images_prompts = st.checkbox("اقتراح أفكار صور/تعليقات بديلة", value=True, key="opt_img_prompts")
    multi_titles   = st.checkbox("اقتراح 5 عناوين بديلة جذابة", value=True, key="opt_multi_titles")
    include_places = st.checkbox("إدراج جدول مختصر للمطاعم (إن وُجد)", value=True, key="opt_include_places")
    include_kws    = st.checkbox("إبراز الكلمات المرتبطة داخل النص", value=True, key="opt_include_kws")

    if st.button("توليد مسودة المقال", key="btn_gen_draft"):
        try:
            docs_text = "\n\n".join([d.get("text","")[:5000] for d in st.session_state.get("docs", [])])
            places = st.session_state.get("places", [])
            places_block = json.dumps(places, ensure_ascii=False) if places and include_places else ""
            kws = st.session_state.get("related_kws", []) if include_kws else []

            outline = st.session_state.get("outline", "")
            prompt = f"""
اكتب مسودة مقال مكتملة عن "{topic}" في "{area}" باللغة {lang} بنبرة {tone}، بطول يقارب {desired_words} كلمة.
الكلمة المستهدفة: {target_kw}
التزم بالمخطط الآتي إن وُجد:
{outline}

مصادر مختصرة (استلهم منها دون نسخ):
{docs_text[:4000]}

قائمة مطاعم (اختياري قد تكون فارغة JSON):
{places_block}

الكلمات المرتبطة (اختياري):
{kws}

متطلبات:
- مقدمة جذابة، ثم أقسام H2/H3، نقاط معدّدة عند الحاجة.
- لا تنسخ حرفيًا من المصادر، بل أعد الصياغة وركّز على القيمة.
- أدرج إن أمكن فقرة "كيف اخترنا/قيّمنا" ومعايير الاختيار.
- أختم بخلاصة ونداء CTA.
- اللغة طبيعية خالية من الحشو والتكرار.
- أضف (إن أمكن) قسم FAQ موجز.
            """.strip()

            messages = [
                {"role":"system","content":"أنت كاتب محتوى عربي خبير SEO، يكتب نصًا طبيعيًا وبشريًا."},
                {"role":"user","content": prompt}
            ]
            cache_key = hash_messages(messages, model_name)
            if llm_cache_enabled and (cached := st.session_state["llm_cacher"].get(cache_key)):
                draft = cached
            else:
                draft = chat_complete_cached(messages, model=model_name, temperature=temperature, max_tokens= max(1024, max_tokens))
                if llm_cache_enabled:
                    st.session_state["llm_cacher"].set(cache_key, draft)

            # عناوين وصور (اختياري)
            extras = {}
            if multi_titles:
                messages2 = [
                    {"role":"system","content":"Copywriter عربي محترف عناوين SEO جذابة بلا نقر طعمي."},
                    {"role":"user","content": f"اقترح 5 عناوين بديلة لمقال عن: {topic}، نبرة {tone}, لغة {lang}. حد أقصى 60 حرفًا."}
                ]
                extras["titles"] = chat_complete_cached(messages2, model=model_name, temperature=0.5, max_tokens=256)

            if images_prompts:
                messages3 = [
                    {"role":"system","content":"مصمم محتوى يقترح أفكار صور Alt مفيدة للقارئ."},
                    {"role":"user","content": f"أعطني 5 أفكار صور موجزة مع نص Alt مناسب لمقال عن {topic} في {area}."}
                ]
                extras["images"] = chat_complete_cached(messages3, model=model_name, temperature=0.6, max_tokens=256)

            st.session_state["draft"] = draft
            st.session_state["extras"] = extras
            st.success("تم توليد المسودة.")
            st.text_area("المسودة", value=draft, key="out_draft", height=420)

            if extras.get("titles"):
                st.markdown("**عناوين مقترحة:**")
                st.code(extras["titles"])

            if extras.get("images"):
                st.markdown("**أفكار صور/Alt:**")
                st.code(extras["images"])

        except Exception as e:
            log_exception(logger, e)
            st.error(f"فشل توليد المسودة: {e}")

# ---------- 🧪 الجودة/SEO ----------
with tab_quality:
    st.subheader("تقارير الجودة")
    draft_txt = st.session_state.get("draft", "")
    docs = st.session_state.get("docs", [])
    places = st.session_state.get("places", [])
    kws = st.session_state.get("related_kws", [])

    if st.button("تحقق بشرية المحتوى", key="btn_human_check"):
        report = human_likeness_report(draft_txt, sources_text="\n\n".join(d.get("text","") for d in docs))
        st.session_state["human_report"] = report
        st.success("تم توليد تقرير بشرية المحتوى.")
        st.json(report)

    if st.button("فحص جودة/SEO", key="btn_quality"):
        qr = quality_report(
            draft_txt=draft_txt,
            target_kw=target_kw,
            topic=topic,
            related_kws=kws,
            places=places,
            desired_words=desired_words
        )
        st.session_state["quality"] = qr
        st.success("تم فحص الجودة.")
        st.json(qr)

    if st.checkbox("عرض JSON-LD Schema", value=True, key="opt_show_schema"):
        jsonld = build_jsonld(topic=topic, area=area, draft=draft_txt, places=places, language=lang)
        st.code(json.dumps(jsonld, ensure_ascii=False, indent=2))

# ---------- 📤 التصدير ----------
with tab_export:
    st.subheader("تصدير")
    filename = st.text_input("اسم الملف بدون الامتداد", value="article", key="inp_filename")

    cdoc, cjson = st.columns(2)
    with cdoc:
        if st.button("تصدير DOCX", key="btn_export_docx"):
            try:
                path = to_docx(
                    filename=f"data/{filename}.docx",
                    title=topic,
                    draft=st.session_state.get("draft",""),
                    extras=st.session_state.get("extras", {}),
                )
                st.success(f"تم الحفظ: {path}")
                st.download_button("تحميل DOCX", data=open(path, "rb").read(), file_name=os.path.basename(path), key="dl_docx")
            except Exception as e:
                log_exception(logger, e)
                st.error(f"فشل التصدير DOCX: {e}")

    with cjson:
        if st.button("تصدير JSON", key="btn_export_json"):
            try:
                payload = {
                    "topic": topic,
                    "area": area,
                    "target_kw": target_kw,
                    "outline": st.session_state.get("outline",""),
                    "draft": st.session_state.get("draft",""),
                    "extras": st.session_state.get("extras", {}),
                    "places": st.session_state.get("places", []),
                    "related_kws": st.session_state.get("related_kws", []),
                }
                path = to_json(f"data/{filename}.json", payload)
                st.success(f"تم الحفظ: {path}")
                st.download_button("تحميل JSON", data=open(path, "rb").read(), file_name=os.path.basename(path), key="dl_json")
            except Exception as e:
                log_exception(logger, e)
                st.error(f"فشل التصدير JSON: {e}")
