# app.py (نسخة مستقلة ومُصلحة)
# -*- coding: utf-8 -*-
# --- تثبيت مسار المشروع على sys.path لحل مشاكل الاستيراد الداخلية ---
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# ---------------------------------------------------------------------

import os, json, math, logging, hashlib, time, unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

# =============== إعداد عام ===============
st.set_page_config(page_title="مولد مقالات المطاعم", page_icon="🍽️", layout="wide")
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | app | %(message)s",
)
logger = logging.getLogger("app")

# =============== أدوات مساعدة ===============
def slugify(name: str) -> str:
    s = ''.join(c for c in unicodedata.normalize('NFKD', name or "") if not unicodedata.combining(c))
    import re as _re
    s = _re.sub(r'\W+', '-', s).strip('-').lower()
    return s or "item"

def _normalize_lines(s: str) -> List[str]:
    return [ln.strip() for ln in (s or "").splitlines() if ln.strip()]

# —— كاش LLM بسيط داخل الجلسة —— 
class SimpleLLMCache:
    def __init__(self, enabled=True, ttl_hours=24):
        self.enabled = enabled
        self.ttl = ttl_hours * 3600
        if "llm_cache_store" not in st.session_state:
            st.session_state["llm_cache_store"] = {}

    def _key(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def get(self, payload: Dict[str, Any]) -> Optional[str]:
        if not self.enabled: return None
        key = self._key(payload)
        item = st.session_state["llm_cache_store"].get(key)
        if not item: return None
        ts, val = item
        if time.time() - ts > self.ttl:
            st.session_state["llm_cache_store"].pop(key, None)
            return None
        return val

    def set(self, payload: Dict[str, Any], value: str):
        if not self.enabled: return
        key = self._key(payload)
        st.session_state["llm_cache_store"][key] = (time.time(), value)

# —— تهيئة الكاش —— 
if "llm_cache" not in st.session_state:
    st.session_state["llm_cache"] = SimpleLLMCache(enabled=True, ttl_hours=24)

# —— OpenAI عميل مبسط —— 
def _has_api_key() -> bool:
    try:
        return bool((hasattr(st, "secrets") and st.secrets.get("OPENAI_API_KEY")) or os.getenv("OPENAI_API_KEY"))
    except Exception:
        return bool(os.getenv("OPENAI_API_KEY"))

def _get_openai_client():
    # لا نُنشئ dependency على وحدات خارجية
    from openai import OpenAI
    api_key = (st.secrets.get("OPENAI_API_KEY") if hasattr(st, "secrets") else None) or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key)

def chat_complete_cached(messages: List[Dict[str, str]], model: str, max_tokens=2000, temperature=0.7) -> str:
    payload = {"messages": messages, "model": model, "max_tokens": max_tokens, "temperature": temperature}
    cache = st.session_state["llm_cache"]
    hit = cache.get(payload)
    if hit is not None:
        return hit
    client = _get_openai_client()
    # نستخدم واجهة Chat القديمة المتوافقة مع openai>=1.0
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = resp.choices[0].message.content.strip()
    cache.set(payload, text)
    return text

# —— استبدال use_container_width —— 
TABLE_WIDTH = 'stretch'  # أو 'content'

# =============== قوالب برومبتات مختصرة (مدمجة) ===============
BASE_TMPL = """# {title}
> الكلمة المفتاحية: {keyword}
> الأسلوب: {tone_label}
> ملاحظة: التزم بإحالات [^n] عند الاستشهاد بالمراجع.

## لماذا هذه القائمة؟
{tone_selection_line}

## المعايير
{criteria_block}

## الأماكن المرشحة
(استخدم الإحالات [^n] عند ذكر بيانات من القائمة أدناه.)

## المنهجية
{methodology_block}

## ملاحظات
{protip_hint}
"""

POLISH_TMPL = """أعد تحرير النص التالي لتحسين السلاسة، تقليل الحشو، والحفاظ على المعنى والحقائق. إذا وُجدت ملاحظات كاتب، دمجها بلطف.
[ملاحظات الكاتب]: {user_notes}

[النص]:
{article}
"""

FAQ_TMPL = """### الأسئلة الشائعة
- ما أفضل وقت للزيارة؟
- هل تتوفر جلسات خارجية؟
- هل المكان مناسب للعائلات؟
"""

GENERAL_CRITERIA = """- جودة المكونات
- الاتساق عبر الزيارات
- السعر مقابل القيمة
- النظافة وسرعة الخدمة
"""

# =============== وظائف بيانات الأماكن (بديل مبسط) ===============
def _dedupe_places(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for r in rows:
        k = (r.get("phone") or "").strip() or (r.get("website") or "").strip() or slugify(r.get("name") or "")
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out

def _score_place(row: Dict[str, Any], keyword: str) -> float:
    rating = float(row.get("rating") or 0.0)
    reviews = max(1, int(row.get("reviews_count") or 1))
    base = rating * math.log(reviews + 1.0)
    name = (row.get("name") or "").lower()
    kw = (keyword or "").lower().strip()
    boost_kw = 0.6 if kw and kw in name else 0.0
    boost_open = 0.3 if row.get("open_now") else 0.0
    return round(base + boost_kw + boost_open, 4)

def _extract_thursday_hours(row: Dict[str, Any]) -> str:
    hours_map = row.get("hours_map") or {}
    for key in ["الخميس", "Thursday", "Thu"]:
        if key in hours_map and hours_map[key]:
            return hours_map[key]
    ht = (row.get("hours_today") or "").strip()
    if "الخميس" in ht or "Thursday" in ht or "Thu" in ht:
        return ht.split(":", 1)[-1].strip()
    return "—"

def _build_references(rows: List[Dict[str, Any]]) -> List[str]:
    refs = []
    for r in rows:
        google_url = r.get("google_url") or r.get("gmaps_url") or "—"
        site = r.get("website") or "—"
        if site and site != "—":
            refs.append(f"{r.get('name','?')}: Google Maps {google_url} — Website {site}")
        else:
            refs.append(f"{r.get('name','?')}: Google Maps {google_url}")
    return refs

def search_places(keyword: str, city: str, min_reviews: int = 50) -> List[Dict[str, Any]]:
    """
    بديل مبسّط: يرجّع بيانات افتراضية إذا لم يتوفر لديك موفّر خارجي.
    يمكنك لاحقًا استبداله بدمج Google Places API.
    """
    logger.info("places.request")
    sample = [
        {"name": "Burger Craft", "address": f"{city} - الحي الشمالي", "phone": "0500000001",
         "website": "", "google_url": "https://maps.google.com/?q=Burger+Craft",
         "rating": 4.4, "reviews_count": 310, "price_band": "متوسط", "open_now": True,
         "hours_map": {"Thursday": "12:00–02:00"}},
        {"name": "Smash House", "address": f"{city} - الوسطى", "phone": "0500000002",
         "website": "https://smash.example", "google_url": "https://maps.google.com/?q=Smash+House",
         "rating": 4.2, "reviews_count": 190, "price_band": "اقتصادي", "open_now": False,
         "hours_map": {"الخميس": "13:00–01:00"}},
        {"name": "Flame & Bun", "address": f"{city} - الغربية", "phone": "0500000003",
         "website": "", "google_url": "https://maps.google.com/?q=Flame+%26+Bun",
         "rating": 4.6, "reviews_count": 520, "price_band": "مرتفع", "open_now": True,
         "hours_map": {"Thu": "14:00–03:00"}},
    ]
    rows = [r for r in sample if int(r["reviews_count"]) >= min_reviews]
    logger.info("places.dedupe")
    return _dedupe_places(rows)

# =============== دوال معايير الاختيار ===============
def _criteria_normalize(raw) -> List[str]:
    """
    إصلاح كامل لخطأ الأقواس: لا نستعمل startswith(tuple).
    نتعامل مع أنواع مختلفة ونرمي "undefined".
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        # إذا كانت JSON-like: اعتمد فحص الحرف الأول فقط
        if s and s[0] in "[{":
            try:
                raw = json.loads(s)
            except Exception:
                lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
                return [ln for ln in lines if ln and ln.lower() != "undefined"]
        else:
            lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
            return [ln for ln in lines if ln and ln.lower() != "undefined"]
    if isinstance(raw, dict):
        for k in ("criteria", "bullets", "items", "list"):
            if k in raw:
                raw = raw[k]; break
        else:
            vals = list(raw.values())
            raw = vals if all(isinstance(v, str) for v in vals) else list(raw.keys())
    if isinstance(raw, (list, tuple)):
        out = []
        for x in raw:
            if isinstance(x, str):
                t = x.strip().strip(",").strip('"').strip("'")
            elif isinstance(x, dict) and "text" in x:
                t = str(x["text"]).strip()
            else:
                t = str(x).strip()
            if t and t.lower() != "undefined":
                out.append(t)
        return out
    return [str(raw)]

def _format_criteria_md(items) -> str:
    items = _criteria_normalize(items)
    return "\n".join(f"- {c}" for c in items) or "- —"

# =============== واجهة التطبيق ===============
st.title("🍽️ مولّد مقالات المطاعم—(نسخة مستقلة)")

# ——— الشريط الجانبي: إعدادات عامة ———
st.sidebar.header("⚙️ الإعدادات")
tone = st.sidebar.selectbox("نغمة الأسلوب",
    ["ناقد ودود","ناقد صارم","دليل تحريري محايد"], index=0, key="tone_select")
primary_model = st.sidebar.selectbox("الموديل", ["gpt-4.1","gpt-4o","gpt-4o-mini"], index=1, key="model_primary")
approx_len = st.sidebar.slider("الطول التقريبي (كلمات)", 600, 1800, 1100, step=100, key="approx_len_slider")
include_faq = st.sidebar.checkbox("إضافة FAQ", value=True, key="include_faq_chk")
include_methodology = st.sidebar.checkbox("إضافة منهجية التحرير", value=True, key="include_methodology_chk")

st.sidebar.markdown("---")
st.sidebar.subheader("🧠 كاش LLM")
llm_cache_enabled = st.sidebar.checkbox("تفعيل الكاش", value=True, key="llm_cache_enabled_chk")
llm_cache_hours = st.sidebar.slider("مدة الكاش (ساعات)", 1, 72, 24, key="llm_cache_hours_slider")
st.session_state["llm_cache"].enabled = llm_cache_enabled
st.session_state["llm_cache"].ttl = llm_cache_hours * 3600

st.sidebar.markdown("---")
st.sidebar.subheader("🔗 كلمات إلزامية")
mandatory_terms = _normalize_lines(st.sidebar.text_area(
    "عبارات إلزامية (سطر لكل عنصر)",
    value="مطاعم عائلية\nجلسات خارجية\nمواقف سيارات",
    height=100,
    key="mandatory_terms_ta",
))

# =============== تبويبات ===============
tab_places, tab_article, tab_qc = st.tabs(["🛰️ أماكن", "✍️ مقال", "🧪 جودة"])

# ---------- تبويب الأماكن ----------
with tab_places:
    st.subheader("🛰️ جلب وتنقية أماكن (بديل مبسّط)")
    col1, col2, col3, col4 = st.columns([2, 1.2, 1, 1])
    with col1:
        gp_keyword = st.text_input("الكلمة المفتاحية", "مطاعم برجر في الرياض", key="gp_kw_in")
    with col2:
        gp_city = st.text_input("المدينة", "الرياض", key="gp_city_in")
    with col3:
        gp_min_reviews = st.number_input("حد أدنى للمراجعات", min_value=0, max_value=5000, value=50, step=10, key="gp_min_reviews_num")
    with col4:
        btn_fetch = st.button("📥 جلب النتائج", key="btn_gp_fetch")

    if btn_fetch:
        rows = search_places(keyword=gp_keyword, city=gp_city, min_reviews=int(gp_min_reviews))
        for r in rows:
            r["score"] = _score_place(r, gp_keyword)
            r["hours_thursday"] = _extract_thursday_hours(r)
        rows.sort(key=lambda x: x["score"], reverse=True)
        if len(rows) < 6:
            st.warning("⚠️ القائمة أقل من 6 عناصر — قد تكون ضعيفة للنشر.")
        st.dataframe(rows, width=TABLE_WIDTH)
        st.session_state["places_results"] = rows

    st.markdown("---")
    if st.button("✔️ اعتماد القائمة", key="btn_accept_places"):
        rows = st.session_state.get("places_results") or []
        if not rows:
            st.warning("لا توجد نتائج لاعتمادها.")
        else:
            snap = []
            for r in rows:
                snap.append({
                    "name": r.get("name"), "address": r.get("address"),
                    "phone": r.get("phone"), "website": r.get("website"),
                    "google_url": r.get("google_url") or r.get("gmaps_url"),
                    "rating": r.get("rating"), "reviews_count": r.get("reviews_count"),
                    "price_band": r.get("price_band"), "open_now": r.get("open_now"),
                    "hours_thursday": r.get("hours_thursday") or _extract_thursday_hours(r),
                })
            refs = _build_references(snap)
            st.session_state["places_snapshot"] = snap
            st.session_state["places_references"] = refs
            st.success("تم الاعتماد.")

# ---------- تبويب المقال ----------
with tab_article:
    st.subheader("✍️ توليد المقال")
    colA, colB = st.columns([2, 1])
    with colA:
        article_title = st.text_input("عنوان المقال", "أفضل مطاعم برجر في الرياض", key="article_title_in")
        article_keyword = st.text_input("الكلمة المفتاحية (اختياري)", "مطاعم برجر في الرياض", key="article_kw_in_tab2")
        criteria_block = st.text_area("معايير الاختيار", value=GENERAL_CRITERIA, height=120, key="criteria_ta")
        manual_notes = st.text_area("ملاحظات يدوية (اختياري)", height=120, key="notes_ta")
    with colB:
        include_jsonld = st.checkbox("تضمين JSON-LD", value=True, key="include_jsonld_chk")
        st.caption("تأكد من إضافة مفتاح OpenAI في secrets أو env.")

    snap = st.session_state.get("places_snapshot") or []
    refs = st.session_state.get("places_references") or []
    st.write(f"عدد العناصر المعتمدة: **{len(snap)}**")
    if snap:
        names_preview = ", ".join([s["name"] for s in snap[:8]]) + ("…" if len(snap) > 8 else "")
        st.info(f"أبرز الأماكن: {names_preview}")

    if st.button("🚀 توليد المقال", key="btn_generate_article"):
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY."); st.stop()
        if not snap:
            st.warning("لا توجد قائمة أماكن معتمدة."); st.stop()

        tone_selection_line = "اعتمدنا على معلومات موثوقة ومراجعات حتى {last_updated}.".format(
            last_updated=datetime.now().strftime("%B %Y")
        )
        facts_lines = []
        for idx, s in enumerate(snap, start=1):
            facts_lines.append(
                f"- {s['name']} — سعر: {s.get('price_band','—')} — ساعات الخميس: {s.get('hours_thursday','—')} — هاتف: {s.get('phone','—')} [^{idx}]"
            )
        facts_block = "\n".join(facts_lines)
        refs_block = "\n".join([f"[^{i+1}]: {r}" for i, r in enumerate(refs)])

        mandatory_hint = ""
        if mandatory_terms:
            mandatory_hint = "أدرج العبارات التالية بشكل طبيعي: " + ", ".join(f"“{t}”" for t in mandatory_terms) + "."

        base_prompt = BASE_TMPL.format(
            title=article_title,
            keyword=article_keyword,
            tone_label=st.session_state["tone_select"],
            tone_selection_line=tone_selection_line,
            criteria_block=_format_criteria_md(criteria_block),
            methodology_block=("سياسة تحريرية مختصرة." if include_methodology else "—"),
            protip_hint=mandatory_hint or "—",
        )

        system_tone = f"اكتب بالعربية الفصحى، أسلوب {st.session_state['tone_select']}, طول تقريبي {approx_len} كلمة."
        messages = [
            {"role": "system", "content": system_tone},
            {"role": "user", "content":
                base_prompt
                + "\n\n---\n\n"
                + "## حقائق مضغوطة عن الأماكن (للاستشهاد بإحالات [^n]):\n"
                + facts_block
                + "\n\n## المراجع (ضع [^n] عند الاستشهاد):\n"
                + refs_block
            },
        ]
        try:
            article_md = chat_complete_cached(messages, model=primary_model, max_tokens=2200, temperature=0.7)
        except Exception as e:
            st.error(f"فشل توليد المقال: {e}"); st.stop()

        # طبقة Polish + دمج الملاحظات
        if manual_notes.strip():
            try:
                article_md = chat_complete_cached(
                    [
                        {"role":"system","content":"أنت محرر عربي محترف، تحافظ على الحقائق وتقلل الحشو."},
                        {"role":"user","content": POLISH_TMPL.format(article=article_md, user_notes=manual_notes)}
                    ],
                    model=primary_model, max_tokens=2200, temperature=0.6
                )
            except Exception as e:
                st.warning(f"تعذّرت طبقة التحرير: {e}")

        # إدراج العبارات الإلزامية إذا غابت
        missing_terms = [t for t in mandatory_terms if t not in article_md]
        if missing_terms:
            try:
                article_md = chat_complete_cached(
                    [
                        {"role":"system","content":"أنت محرر عربي تُدرج عبارات محددة بشكل طبيعي بدون حشو."},
                        {"role":"user","content": f"أدرج العبارات التالية فقط حيث تلائم السياق: {', '.join(missing_terms)}.\n\nالنص:\n{article_md}"}
                    ],
                    model=primary_model, max_tokens=2000, temperature=0.4
                )
            except Exception:
                pass

        # SEO Title/Desc
        try:
            meta_out = chat_complete_cached(
                [
                    {"role":"system","content":"مختص SEO عربي."},
                    {"role":"user","content": f"صِغ عنوان SEO (≤60) ووصف ميتا (≤155) لمقال بعنوان \"{article_title}\".\nTITLE: ...\nDESCRIPTION: ..."}
                ],
                model=primary_model, max_tokens=180, temperature=0.5
            )
        except Exception:
            meta_out = f"TITLE: {article_title}\nDESCRIPTION: دليل عملي عن {article_keyword}."

        st.subheader("📄 المقال")
        st.markdown(article_md)
        st.session_state['last_article_md'] = article_md

        if include_faq:
            st.markdown(FAQ_TMPL)

        st.subheader("🔎 Meta (SEO)")
        st.code(meta_out, language="text")

        # تنزيلات
        from docx import Document
        def to_docx(md_text: str) -> bytes:
            doc = Document()
            for line in md_text.splitlines():
                doc.add_paragraph(line)
            import io
            buf = io.BytesIO()
            doc.save(buf)
            return buf.getvalue()

        col1, col2 = st.columns(2)
        with col1:
            st.download_button('💾 تنزيل Markdown', data=article_md, file_name='article.md', mime='text/markdown', key="dl_md_btn")
        with col2:
            st.download_button('📝 تنزيل DOCX', data=to_docx(article_md),
                               file_name='article.docx',
                               mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                               key="dl_docx_btn")

# ---------- تبويب الجودة ----------
with tab_qc:
    st.subheader("🧪 فحص سريع")
    qc_text = st.text_area("الصق نص المقال هنا", st.session_state.get("last_article_md",""), height=260, key="qc_text_ta")
    if st.button("تحليل", key="btn_qc_analyze"):
        if not qc_text.strip():
            st.warning("الصق النص أولًا.")
        else:
            # فحص مبسّط: كثافة الحشو + تنوع أطوال الجمل
            filler = ["بشكل كبير","حقًا","للغاية","نوعًا ما","تمامًا","جدًا جدًا"]
            words = qc_text.split()
            fluff_hits = sum(qc_text.count(f) for f in filler)
            avg_len = sum(len(s.split()) for s in qc_text.split(".")) / max(1, len(qc_text.split(".")))
            res = {
                "length_words": len(words),
                "fluff_hits": fluff_hits,
                "avg_sentence_len": round(avg_len, 2),
                "hint": "خفّف العبارات العامة وادمج تفاصيل حسية أكثر."
            }
            st.json(res)
            st.success("انتهى التحليل.")
