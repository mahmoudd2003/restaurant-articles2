# app.py
# -*- coding: utf-8 -*-

import os
import io
import csv
import json
import math
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import streamlit as st

# --- مجلدات أساسية ---
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# --- اللوجينغ (يتطلب utils/logging_setup.py كما أرسلت لك) ---
from utils.logging_setup import (
    init_logging,
    get_logger,
    set_correlation_id,
    with_context,
    log_exception,
)
init_logging(app_name="restoguide", level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger("app")

# --- استيرادات داخلية (عبر جسور utils/*) ---
from utils.content_fetch import fetch_and_extract, configure_http_cache, clear_http_cache
from utils.openai_client import get_client, chat_complete_cached
from utils.llm_cache import LLMCacher
from utils.quality_checks import quality_report
from utils.llm_reviewer import llm_review, llm_fix
from utils.places_provider import search_places  # واجهة Google Places الموحّدة
from utils.wp_client import WPClient  # عميل ووردبريس للنشر

# ============================= مساعدات عامة =============================

def safe_rerun():
    if getattr(st, "rerun", None):
        st.rerun()
    else:
        st.experimental_rerun()

def _has_api_key() -> bool:
    # خصيصًا لمفتاح OpenAI
    try:
        if hasattr(st, "secrets") and st.secrets.get("OPENAI_API_KEY"):
            return True
    except Exception:
        pass
    return bool(os.getenv("OPENAI_API_KEY"))

def slugify(name: str) -> str:
    s = ''.join(c for c in unicodedata.normalize('NFKD', name or "") if not unicodedata.combining(c))
    import re as _re
    s = _re.sub(r'\W+', '-', s).strip('-').lower()
    return s or "item"

PROMPTS_DIR = Path("prompts")

def _read_file_any(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None

def read_prompt(name: str) -> str:
    # يحاول أولاً مجلد prompts/ ثم الجذر الحالي
    txt = _read_file_any(PROMPTS_DIR / name)
    if txt is None:
        txt = _read_file_any(Path(name))
    if txt is None:
        return f"<!-- missing prompt: {name} -->"
    return txt

# ======= قوالب النصوص =======
BASE_TMPL = read_prompt("base.md")
POLISH_TMPL = read_prompt("polish.md")
FAQ_TMPL = read_prompt("faq.md")
METH_TMPL = read_prompt("methodology.md")
CRITERIA_MAP = {
    "بيتزا": read_prompt("criteria_pizza.md"),
    "مندي": read_prompt("criteria_mandy.md"),
    "برجر": read_prompt("criteria_burger.md"),
    "كافيهات": read_prompt("criteria_cafes.md"),
}
GENERAL_CRITERIA = read_prompt("criteria_general.md")

# ============================= واجهة =============================
st.set_page_config(page_title="مولد مقالات المطاعم — Places + E-E-A-T", page_icon="🍽️", layout="wide")
st.title("🍽️ مولد مقالات المطاعم — Google Places + E-E-A-T + FAQ + ووردبريس")

# --- Sidebar: النماذج والإعدادات التحريرية ---
st.sidebar.header("⚙️ الإعدادات العامة")

tone = st.sidebar.selectbox(
    "نغمة الأسلوب",
    ["ناقد ودود", "ناقد صارم", "دليل تحريري محايد", "ناقد صارم | مراجعات الجمهور", "ناقد صارم | تجربة مباشرة + مراجعات"],
    index=0,
    key="tone_select",
)

primary_model = st.sidebar.selectbox("الموديل الأساسي", ["gpt-4.1", "gpt-4o", "gpt-4o-mini"], index=1, key="model_primary")
fallback_model = st.sidebar.selectbox("موديل بديل (Fallback)", ["gpt-4o", "gpt-4o-mini", "gpt-4.1"], index=2, key="model_fallback")
approx_len = st.sidebar.slider("الطول التقريبي (كلمات)", 600, 1800, 1100, step=100, key="approx_len")
include_faq = st.sidebar.checkbox("إضافة FAQ", value=True, key="include_faq")
include_methodology = st.sidebar.checkbox("إضافة منهجية التحرير", value=True, key="include_methodology")
add_human_touch = st.sidebar.checkbox("طبقة لمسات بشرية (Polish)", value=True, key="do_polish")

# --- Sidebar: كلمات إلزامية ---
mandatory_terms_raw = st.sidebar.text_area(
    "كلمات/عبارات إلزامية (سطر لكل عنصر)",
    value="مطاعم عائلية\nجلسات خارجية\nمواقف سيارات",
    height=100,
    key="mandatory_terms",
)
def _normalize_lines(s: str) -> List[str]:
    return [ln.strip() for ln in (s or "").splitlines() if ln.strip()]

mandatory_terms = _normalize_lines(mandatory_terms_raw)

# --- Sidebar: كاش HTTP لجلب الصفحات (requests-cache عبر content_fetch) ---
st.sidebar.markdown("---")
st.sidebar.subheader("🗄️ كاش HTTP (للجلب الخارجي)")
http_cache_enabled = st.sidebar.checkbox("تفعيل كاش HTTP", value=True, key="http_cache_enabled")
http_cache_hours = st.sidebar.slider("مدة الكاش (ساعات)", 1, 72, 24, key="http_cache_hours")
if st.sidebar.button("🧹 مسح كاش HTTP", key="clear_http_cache"):
    try:
        ok = clear_http_cache()
        st.sidebar.success("تم مسح كاش HTTP." if ok else "لا توجد بيانات كاش.")
    except Exception as e:
        st.sidebar.warning(f"تعذّر مسح الكاش: {e}")

try:
    configure_http_cache(enabled=http_cache_enabled, hours=http_cache_hours)
except Exception as e:
    st.sidebar.warning(f"تعذّر تهيئة كاش HTTP: {e}")

# --- Sidebar: كاش LLM ---
st.sidebar.markdown("---")
st.sidebar.subheader("🧠 كاش LLM")
llm_cache_enabled = st.sidebar.checkbox("تفعيل كاش LLM", value=True, key="llm_cache_enabled")
llm_cache_hours = st.sidebar.slider("مدة كاش LLM (ساعات)", 1, 72, 24, key="llm_cache_hours")
if "llm_cacher" not in st.session_state:
    st.session_state["llm_cacher"] = LLMCacher(ttl_hours=llm_cache_hours, enabled=llm_cache_enabled)
else:
    st.session_state["llm_cacher"].configure(enabled=llm_cache_enabled, ttl_hours=llm_cache_hours)

if st.sidebar.button("🧹 مسح كاش LLM", key="clear_llm_cache"):
    ok = st.session_state["llm_cacher"].clear()
    st.sidebar.success("تم مسح كاش LLM." if ok else "تعذّر المسح.")

# --- Sidebar: روابط داخلية (اختياري) ---
st.sidebar.markdown("---")
st.sidebar.subheader("🔗 روابط داخلية (اختياري)")
internal_catalog = st.sidebar.text_area(
    "أدخل عناوين/سلاگز (سطر لكل عنصر)",
    value="أفضل مطاعم الرياض\nأفضل مطاعم إفطار في الرياض\nأفضل مطاعم برجر في جدة",
    height=90,
    key="internal_links_catalog",
)

# ============================= أدوات قائمة الأماكن =============================

def _dedupe_places(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """إزالة تكرارات بناءً على (الهاتف/الموقع/الاسم الموحّد)."""
    seen = set()
    out = []
    for r in rows:
        k = (r.get("phone") or "").strip() or (r.get("website") or "").strip() or slugify(r.get("name") or "")
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out

def _score_place(row: Dict[str, Any], keyword: str) -> float:
    """تقييم ذكي: rating × log(reviews) + Boost للتطابق + open_now boost."""
    rating = float(row.get("rating") or 0.0)
    reviews = max(1, int(row.get("reviews_count") or 1))
    base = rating * math.log(reviews + 1.0)
    name = (row.get("name") or "").lower()
    kw = (keyword or "").lower().strip()
    boost_kw = 0.6 if kw and kw in name else 0.0
    boost_open = 0.3 if row.get("open_now") else 0.0
    return round(base + boost_kw + boost_open, 4)

def _extract_thursday_hours(row: Dict[str, Any]) -> str:
    """
    يُجبِر ساعات العمل لتكون ليوم الخميس فقط (حسب طلبك).
    يعتمد على الحقلين: 'hours_map' (dict) أو 'hours_today' (fallback).
    """
    hours_map = row.get("hours_map") or {}
    thu = None
    # مفاتيح محتملة عربية/إنجليزية
    for key in ["الخميس", "Thursday", "Thu"]:
        if key in hours_map and hours_map[key]:
            thu = hours_map[key]
            break
    if not thu:
        # أحيانًا يأتي hours_today كـ "الخميس: 12:00–2:00"
        ht = (row.get("hours_today") or "").strip()
        if "الخميس" in ht or "Thursday" in ht or "Thu" in ht:
            thu = ht.split(":", 1)[-1].strip()
    return thu or "—"

def _build_references(rows: List[Dict[str, Any]]) -> List[str]:
    """
    يصنع قائمة مراجع [^n] لكل عنصر: يدمج google_url + website (إن وجد).
    """
    refs = []
    for r in rows:
        google_url = r.get("google_url") or r.get("gmaps_url") or "—"
        site = r.get("website") or "—"
        if site and site != "—":
            refs.append(f"{r.get('name','?')}: Google Maps {google_url} — Website {site}")
        else:
            refs.append(f"{r.get('name','?')}: Google Maps {google_url}")
    return refs

def _criteria_normalize(raw):
    """حوّل أي ناتج (list/tuple/dict/str JSON) إلى قائمة نصوص نظيفة بلا undefined."""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
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
                raw = raw[k]
                break
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

def _format_criteria_md(items):
    items = _criteria_normalize(items)
    return "\n".join(f"- {c}" for c in items) or "- —"

# ============================= التبويبات =============================

tab_places, tab_article, tab_qc, tab_publish = st.tabs(
    ["🛰️ مصادر Google", "✍️ توليد المقال", "🧪 فحوصات الجودة", "📝 نشر ووردبريس"]
)

# -------------------- تبويب 1: جلب وتنقية Google Places --------------------
with tab_places:
    st.subheader("🛰️ Google Places (جلب & تنقية)")
    col1, col2, col3, col4 = st.columns([2, 1.2, 1, 1])
    with col1:
        gp_keyword = st.text_input("الكلمة المفتاحية", "مطاعم برجر في الرياض", key="gp_kw")
    with col2:
        gp_city = st.text_input("المدينة", "الرياض", key="gp_city")
    with col3:
        gp_min_reviews = st.number_input("حد أدنى للمراجعات", min_value=0, max_value=5000, value=50, step=10, key="gp_min_reviews")
    with col4:
        btn_fetch = st.button("📥 جلب النتائج", use_container_width=False, key="btn_gp_fetch")

    if btn_fetch:
        corr = set_correlation_id()
        try:
            with with_context(correlation_id=corr, stage="places.fetch"):
                logger.info("places.fetch.start", extra={"correlation_id": corr})
                rows = search_places(keyword=gp_keyword, city=gp_city, min_reviews=int(gp_min_reviews))
                # تنقية وتقييم
                rows = _dedupe_places(rows)
                for r in rows:
                    r["score"] = _score_place(r, gp_keyword)
                    r["hours_thursday"] = _extract_thursday_hours(r)
                rows.sort(key=lambda x: x["score"], reverse=True)
                # راية تحذير إن العدد قليل
                if len(rows) < 6:
                    st.warning("⚠️ القائمة أقل من 6 عناصر — قد تكون ضعيفة للنشر.")
                # عرض النتائج
                st.dataframe(rows, width='stretch')
                # تخزين في الجلسة
                st.session_state["places_results"] = rows
                logger.info("places.fetch.done", extra={"correlation_id": corr, "count": len(rows)})
        except Exception as e:
            log_exception(logger, "places.fetch.error")
            st.error(f"تعذّر الجلب: {e}")

    st.markdown("---")
    btn_accept = st.button("✔️ اعتماد القائمة", key="btn_accept_places", use_container_width=False)
    if btn_accept:
        rows = st.session_state.get("places_results") or []
        if not rows:
            st.warning("لا توجد نتائج لاعتمادها. قم بالجلب أولًا.")
        else:
            # Snapshot نظيف + مراجع
            snap = []
            for r in rows:
                snap.append({
                    "name": r.get("name"),
                    "address": r.get("address"),
                    "phone": r.get("phone"),
                    "website": r.get("website"),
                    "google_url": r.get("google_url") or r.get("gmaps_url"),
                    "rating": r.get("rating"),
                    "reviews_count": r.get("reviews_count"),
                    "price_band": r.get("price_band"),
                    "open_now": r.get("open_now"),
                    # ساعات الخميس ثابتة حسب طلبك
                    "hours_thursday": r.get("hours_thursday") or _extract_thursday_hours(r),
                })
            refs = _build_references(snap)
            st.session_state["places_snapshot"] = snap
            st.session_state["places_references"] = refs
            st.success("تم اعتماد القائمة. سيتم استخدامها كمصدر حقائق للمقال.")
            logger.info("places.accepted")

# -------------------- تبويب 2: توليد المقال --------------------
with tab_article:
    st.subheader("✍️ توليد المقال (سرد + FAQ + مراجع)")

    colA, colB = st.columns([2, 1])
    with colA:
        article_title = st.text_input("عنوان المقال", "أفضل مطاعم برجر في الرياض", key="article_title")
        article_keyword = st.text_input("الكلمة المفتاحية (اختياري)", "مطاعم برجر في الرياض", key="article_kw")

        # اختيار فئة/معايير (اختياري)
        built_in_labels = list(CRITERIA_MAP.keys())
        content_scope = st.selectbox(
            "نطاق المحتوى",
            ["فئة محددة داخل المكان", "شامل بلا فئة", "هجين (تقسيم داخلي)"],
            index=0,
            key="content_scope",
        )

        if content_scope == "فئة محددة داخل المكان":
            category_choice = st.selectbox("الفئة", built_in_labels + ["فئة مخصّصة…"], index=2, key="category_select")
            if category_choice == "فئة مخصّصة…":
                category = st.text_input("اسم الفئة المخصّصة", "مطاعم برجر", key="custom_category_name")
                criteria_block = st.text_area(
                    "معايير الاختيار (اختياري)",
                    value="- جودة اللحم والخبز\n- ثبات مستوى الطهي\n- السعر مقابل القيمة\n- سرعة الخدمة والنظافة",
                    height=120,
                    key="custom_criteria_text",
                )
            else:
                category = category_choice
                criteria_block = CRITERIA_MAP.get(category_choice, GENERAL_CRITERIA)
        else:
            category = "عام"
            criteria_block = GENERAL_CRITERIA

        # دمج Snapshot
        snap = st.session_state.get("places_snapshot") or []
        refs = st.session_state.get("places_references") or []

        st.caption("سيتم تضمين قائمة الأماكن المعتمدة كحقائق مضغوطة + إحالات [^n].")
        st.write(f"عدد العناصر المعتمدة: **{len(snap)}**")

        # تحضير قائمة أسماء (إن أردت إظهارها للمستخدم)
        if snap:
            names_preview = ", ".join([s["name"] for s in snap[:8]]) + ("…" if len(snap) > 8 else "")
            st.info(f"أبرز الأماكن: {names_preview}")

        # ملاحظات يدوية اختيارية
        manual_notes = st.text_area("ملاحظات يدوية تُدمج داخل السرد (اختياري)", height=120, key="article_notes")

    with colB:
        st.subheader("قائمة تدقيق بشرية")
        checks = {
            "sensory": st.checkbox("وصف حسي (رائحة/قوام/حرارة) لمطعم واحد+", key="chk_sensory"),
            "personal": st.checkbox("ملاحظة/تفضيل شخصي", key="chk_personal"),
            "compare": st.checkbox("مقارنة مع زيارة سابقة/مطعم مشابه", key="chk_compare"),
            "critique": st.checkbox("نقطة نقد غير متوقعة", key="chk_critique"),
            "vary": st.checkbox("تنويع أطوال الفقرات", key="chk_vary"),
        }

        include_jsonld = st.checkbox("تضمين JSON-LD (Article + FAQ)", value=True, key="include_jsonld")

    # زر توليد المقال
    if st.button("🚀 توليد المقال", key="btn_generate_article", use_container_width=False):
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
            st.stop()

        if not snap:
            st.warning("لا توجد قائمة أماكن معتمدة. انتقل لتبويب 'مصادر Google' واعتمد قائمة أولًا.")
            st.stop()

        client = get_client()
        cacher = st.session_state.get("llm_cacher")

        # إعدادات نبرة
        if tone == "ناقد صارم | مراجعات الجمهور":
            tone_instructions = ("اكتب كناقد صارم يعتمد أساسًا على مراجعات العملاء المنشورة علنًا. "
                                 "ركّز على الأنماط المتكررة واذكر حدود المنهجية. لا تدّعِ زيارة شخصية. لا تستخدم أرقامًا مبالغًا فيها.")
            tone_selection_line = "اعتمدنا مراجعات موثوقة منشورة علنًا حتى {last_updated}."
            system_tone = "أسلوب ناقد صارم مرتكز على مراجعات الجمهور"
        elif tone == "ناقد صارم | تجربة مباشرة + مراجعات":
            tone_instructions = ("اكتب كناقد صارم يمزج خبرة ميدانية مع مراجعات الجمهور. "
                                 "قدّم الحكم من التجربة المباشرة أولًا ثم قارنه بانطباعات الجمهور. أدرج **نقطة للتحسين** لكل مطعم.")
            tone_selection_line = "مزجنا بين زيارات ميدانية وتجارب فعلية ومراجعات عامة حتى {last_updated}."
            system_tone = "أسلوب ناقد صارم يمزج التجربة المباشرة مع مراجعات الجمهور"
        else:
            tone_instructions = "اكتب بأسلوب متوازن يراعي الدقة والوضوح دون مبالغة."
            tone_selection_line = "اعتمدنا على التجربة المباشرة ومعلومات موثوقة متاحة، مع مراجعة دورية."
            system_tone = tone

        last_updated = datetime.now().strftime("%B %Y")

        # تجهيز حقائق مضغوطة + إحالات
        # نستخدم ساعات الخميس الثابتة كما طلبت، ونبقي التفاصيل موجزة
        facts_lines = []
        for idx, s in enumerate(snap, start=1):
            facts_lines.append(
                f"- {s['name']} — سعر: {s.get('price_band','—')} — ساعات الخميس: {s.get('hours_thursday','—')} — هاتف: {s.get('phone','—')} [^{idx}]"
            )
        facts_block = "\n".join(facts_lines)
        refs_block = "\n".join([f"[^{i+1}]: {r}" for i, r in enumerate(refs)])

        faq_block = FAQ_TMPL if include_faq else "—"
        methodology_block = (METH_TMPL.format(last_updated=last_updated) if include_methodology else "—")

        # دمج الكلمات الإلزامية كنصيحة لاستخدامها طبيعيًا (بدون حشو)
        mandatory_hint = ""
        if mandatory_terms:
            mandatory_hint = "أدرج العبارات التالية بشكل طبيعي غير قسري ضمن السرد متى كان ملائمًا: " + ", ".join(f"“{t}”" for t in mandatory_terms) + "."

        # بناء البرومبت الأساسي (يعتمد base.md لديك)
        base_prompt = BASE_TMPL.format(
            title=article_title,
            keyword=article_keyword,
            content_scope=content_scope,
            category=category,
            restaurants_list=", ".join([s["name"] for s in snap]),
            criteria_block=_format_criteria_md(criteria_block),
            faq_block=faq_block,
            methodology_block=methodology_block,
            tone_label=tone,
            place_context="—",
            protip_hint="—",
            scope_instructions="التزم بالسرد الإنساني مع إحالات دقيقة للمراجع [^n].",
            tone_instructions=tone_instructions + " " + mandatory_hint,
            tone_selection_line=tone_selection_line.replace("{last_updated}", last_updated),
        )

        # نضيف حقائق الأماكن والمراجع كملحق للبرومبت
        base_messages = [
            {"role": "system", "content": f"اكتب المقال بالعربية الفصحى. {system_tone}. طول تقريبي {approx_len} كلمة."},
            {"role": "user", "content":
                base_prompt
                + "\n\n---\n\n"
                + "## حقائق مضغوطة عن الأماكن (للاستشهاد داخل السرد بإحالات [^n]):\n"
                + facts_block
                + "\n\n## المراجع (ضع [^n] عند الاستشهاد):\n"
                + refs_block
            },
        ]

        try:
            article_md = chat_complete_cached(
                client, base_messages,
                max_tokens=2200, temperature=0.7,
                model=primary_model, fallback_model=fallback_model,
                cacher=cacher, cache_extra={"purpose": "article_base", "kw": article_keyword}
            )
        except Exception as e:
            log_exception(logger, "llm.article.error")
            st.error(f"فشل توليد المقال: {e}")
            st.stop()

        # طبقة Polish (اختياري + دمج ملاحظات)
        apply_polish = add_human_touch or any(checks.values())
        if apply_polish or (manual_notes.strip()):
            polish_prompt = POLISH_TMPL.format(article=article_md, user_notes=manual_notes)
            polish_messages = [
                {"role": "system", "content": "أنت محرر عربي محترف، تحافظ على الحقائق وتضيف لمسات بشرية بدون مبالغة وبلا حشو."},
                {"role": "user", "content": polish_prompt},
            ]
            try:
                article_md = chat_complete_cached(
                    client, polish_messages,
                    max_tokens=2400, temperature=0.8,
                    model=primary_model, fallback_model=fallback_model,
                    cacher=cacher, cache_extra={"purpose": "article_polish"}
                )
            except Exception as e:
                st.warning(f"تعذّرت طبقة اللمسات البشرية: {e}")

        # التحقق من الكلمات الإلزامية + محاولة إدماج تلقائي إن لزم
        missing_terms = [t for t in mandatory_terms if t not in article_md]
        if missing_terms:
            st.warning("بعض العبارات الإلزامية لم تظهر في النص وسيتم إدماجها بلطف.")
            try:
                fix_messages = [
                    {"role": "system", "content": "أنت محرر عربي تُدرج عبارات محددة بشكل طبيعي ودقيق في النص بدون إفساد السرد أو إضافة حشو."},
                    {"role": "user", "content": f"أدرج العبارات التالية بشكل طبيعي داخل النص أدناه حيث يكون ملائمًا فقط: {', '.join(missing_terms)}.\n\nالنص:\n{article_md}"}
                ]
                article_md = chat_complete_cached(
                    client, fix_messages,
                    max_tokens=2000, temperature=0.4,
                    model=primary_model, fallback_model=fallback_model,
                    cacher=cacher, cache_extra={"purpose": "article_terms_fix", "missing": missing_terms}
                )
            except Exception:
                pass

        # عنوان ووصف SEO
        try:
            meta_out = chat_complete_cached(
                client,
                [
                    {"role":"system","content":"أنت مختص SEO عربي."},
                    {"role":"user","content": f"صِغ عنوان SEO (≤ 60) ووصف ميتا (≤ 155) بالعربية لمقال بعنوان \"{article_title}\". الكلمة المفتاحية: {article_keyword}.\nTITLE: ...\nDESCRIPTION: ..."}
                ],
                max_tokens=200, temperature=0.6,
                model=primary_model, fallback_model=fallback_model,
                cacher=cacher, cache_extra={"purpose": "article_meta", "title": article_title}
            )
        except Exception:
            meta_out = f"TITLE: {article_title}\nDESCRIPTION: دليل عملي عن {article_keyword}."

        # روابط داخلية مقترحة
        links_catalog = [s.strip() for s in internal_catalog.splitlines() if s.strip()]
        try:
            links_out = chat_complete_cached(
                client,
                [
                    {"role":"system","content":"أنت محرر عربي يقترح روابط داخلية طبيعية."},
                    {"role":"user","content": f"اقترح 3 روابط داخلية مناسبة من هذه القائمة إن أمكن:\n{links_catalog}\nالعنوان: {article_title}\nالكلمة المفتاحية: {article_keyword}\nمقتطف:\n{article_md[:800]}\n- رابط داخلي مقترح: <النص>"}
                ],
                max_tokens=240, temperature=0.5,
                model=primary_model, fallback_model=fallback_model,
                cacher=cacher, cache_extra={"purpose": "article_links"}
            )
        except Exception:
            links_out = "- رابط داخلي مقترح: أفضل مطاعم الرياض\n- رابط داخلي مقترح: دليل مطاعم العائلات في الرياض\n- رابط داخلي مقترح: مقارنة بين الأنماط"

        # إخراج
        st.subheader("📄 المقال الناتج")
        st.markdown(article_md)
        st.session_state['last_article_md'] = article_md

        st.subheader("🔎 Meta (SEO)")
        st.code(meta_out, language="text")

        st.subheader("🔗 روابط داخلية مقترحة")
        st.markdown(links_out)

        # حفظ JSON
        out_json = {
            "title": article_title,
            "keyword": article_keyword,
            "category": category,
            "content_scope": content_scope,
            "places_snapshot": snap,
            "references": refs,
            "last_updated": last_updated,
            "tone": tone,
            "models": {"primary": primary_model, "fallback": fallback_model},
            "include_faq": include_faq,
            "include_methodology": include_methodology,
            "article_markdown": article_md,
            "meta": meta_out,
            "internal_links": links_out,
        }
        st.session_state['last_json'] = json.dumps(out_json, ensure_ascii=False, indent=2)

        # تنزيلات
        cold1, cold2, cold3 = st.columns(3)
        with cold1:
            st.download_button('💾 تنزيل Markdown', data=article_md, file_name='article.md', mime='text/markdown', key="dl_md")
        with cold2:
            from utils.exporters import to_docx, to_json
            st.download_button('📝 تنزيل DOCX', data=to_docx(article_md), file_name='article.docx',
                               mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document', key="dl_docx")
        with cold3:
            st.download_button('🧩 تنزيل JSON', data=st.session_state['last_json'], file_name='article.json', mime='application/json', key="dl_json")

# -------------------- تبويب 3: فحوصات الجودة --------------------
with tab_qc:
    st.subheader("🧪 فحص بشرية وجودة المحتوى")
    qc_text = st.text_area("الصق نص المقال هنا", st.session_state.get("last_article_md",""), height=300, key="qc_text")
    col_q1, col_q2, col_q3 = st.columns(3)
    with col_q1:
        do_fluff = st.checkbox("كشف الحشو والعبارات القالبية", value=True, key="qc_fluff")
    with col_q2:
        do_eeat = st.checkbox("مؤشرات E-E-A-T", value=True, key="qc_eeat")
    with col_q3:
        do_llm_review = st.checkbox("تشخيص مُرشد (LLM)", value=True, key="qc_llm")

    if st.button("🔎 تحليل سريع", key="btn_qc_fast"):
        if not qc_text.strip():
            st.warning("الصق النص أولًا.")
        else:
            rep = quality_report(qc_text)
            st.session_state["qc_report"] = rep
            st.markdown("### بطاقة الدرجات")
            colA, colB, colC = st.columns(3)
            with colA: st.metric("Human-style Score", rep["human_style_score"])
            with colB: st.metric("Sensory Ratio", rep["sensory_ratio"])
            with colC: st.metric("Fluff Density", rep["fluff_density"])
            st.markdown("#### تنوّع الجمل"); st.json(rep["sentence_variety"])
            if do_eeat:
                st.markdown("#### E-E-A-T"); st.json({"presence": rep["eeat"], "score": rep["eeat_score"]})
                st.markdown("#### Information Gain"); st.json({"score": rep["info_gain_score"]})
            if do_fluff:
                st.markdown("#### عبارات قالبية مرصودة")
                boiler = rep.get("boilerplate_flags") or []
                if boiler:
                    for f in boiler:
                        pattern = f.get("pattern", "?")
                        excerpt = f.get("excerpt", "")
                        st.write(f"- **نمط:** `{pattern}` — مقتطف: …{excerpt}…")
                else:
                    st.caption("لا توجد عبارات قالبية ظاهرة.")
                repeats = rep.get("repeated_phrases") or []
                if repeats:
                    st.markdown("#### عبارات متكررة بشكل زائد")
                    for g, c in repeats:
                        st.write(f"- `{g}` × {c}")
            st.success("انتهى التحليل السريع.")
            st.session_state["qc_text"] = qc_text

    if do_llm_review and st.button("🧠 تشخيص مُرشد (LLM)", key="btn_qc_llm"):
        if not qc_text.strip():
            st.warning("الصق النص أولًا.")
        elif not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
        else:
            client = get_client()
            out = llm_review(client, primary_model, fallback_model, qc_text)
            st.markdown("### تقرير المُراجع"); st.markdown(out)
            st.session_state["qc_review_md"] = out

    st.markdown("---")
    st.markdown("#### إصلاح ذكي للأجزاء المعلّمة")
    flagged_block = st.text_area("ألصق المقاطع الضعيفة (سطر لكل مقطع)", height=140, key="qc_flagged")
    if st.button("✍️ أعِد الصياغة للمقاطع المحددة فقط", key="btn_qc_fix"):
        if not flagged_block.strip():
            st.warning("أدخل المقاطع أولًا.")
        elif not qc_text.strip():
            st.warning("لا يوجد نص أساس لإعادة الكتابة.")
        elif not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
        else:
            client = get_client()
            new_text = llm_fix(client, primary_model, fallback_model, qc_text, flagged_block.splitlines())
            st.markdown("### النص بعد الإصلاح"); st.markdown(new_text)
            st.session_state["last_article_md"] = new_text
            st.success("تم الإصلاح الموضعي.")

# -------------------- تبويب 4: نشر ووردبريس --------------------
with tab_publish:
    st.subheader("📝 النشر على ووردبريس (Draft)")
    st.caption("يُنصح بالنشر كمسودة ثم المراجعة التحريرية قبل النشر النهائي.")

    # نقرأ الإعدادات من secrets تلقائيًا (كما اتفقنا)
    wp_url = st.secrets.get("WP_BASE_URL", "") if hasattr(st, "secrets") else os.getenv("WP_BASE_URL", "")
    wp_user = st.secrets.get("WP_USERNAME", "") if hasattr(st, "secrets") else os.getenv("WP_USERNAME", "")
    wp_app_pass = st.secrets.get("WP_APP_PASSWORD", "") if hasattr(st, "secrets") else os.getenv("WP_APP_PASSWORD", "")

    colp1, colp2 = st.columns([2, 1])
    with colp1:
        post_title = st.text_input("عنوان التدوينة", st.session_state.get("article_title", "أفضل مطاعم برجر في الرياض"), key="wp_post_title")
        post_slug = st.text_input("Slug (اختياري)", slugify(post_title), key="wp_post_slug")
    with colp2:
        post_status = st.selectbox("الحالة", ["draft", "publish", "pending"], index=0, key="wp_post_status")

    city_tag = st.text_input("وسم المدينة", "الرياض", key="wp_city_tag")
    category_name = st.text_input("تصنيف رئيسي", "مطاعم", key="wp_category")

    dataset_meta_key = st.text_input("مفتاح ميتا لحفظ Dataset", "places_json", key="wp_meta_key")

    btn_publish = st.button("📤 أنشر كـ Draft", key="btn_publish_wp", use_container_width=False)

    if btn_publish:
        article_md = st.session_state.get("last_article_md", "")
        if not article_md:
            st.warning("لا يوجد محتوى جاهز للنشر. ولّد المقال أولًا.")
        elif not (wp_url and wp_user and wp_app_pass):
            st.error("بيانات ووردبريس غير مكتملة في secrets/env.")
        else:
            # تحضير JSON-LD (اختياري)
            jsonld_blocks = []
            if st.session_state.get("include_jsonld", True):
                # Article
                jsonld_blocks.append({
                    "@context": "https://schema.org",
                    "@type": "Article",
                    "headline": post_title,
                    "datePublished": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "inLanguage": "ar",
                })
                # FAQPage (إن كان FAQ ضمن المقال)
                if include_faq:
                    jsonld_blocks.append({
                        "@context": "https://schema.org",
                        "@type": "FAQPage",
                        "mainEntity": []
                    })
            jsonld_html = ""
            if jsonld_blocks:
                jsonld_html = "<script type='application/ld+json'>\n" + json.dumps(jsonld_blocks, ensure_ascii=False, indent=2) + "\n</script>"

            # محتوى HTML مبسّط (يمكن لاحقًا تحسين التحويل)
            html_content = f"<div class='article-body'>\n{article_md}\n</div>\n{jsonld_html}"

            try:
                client = WPClient(
                    base_url=wp_url,
                    username=wp_user,
                    app_password=wp_app_pass
                )
                result = client.post_or_update(
                    title=post_title,
                    content_html=html_content,
                    slug=post_slug,
                    status=post_status,
                    tags=[city_tag] if city_tag else [],
                    categories=[category_name] if category_name else [],
                    meta={dataset_meta_key: st.session_state.get("last_json", "{}")},
                    find_existing_by="slug"  # يجنب الازدواجية
                )
                st.success("تم إنشاء/تحديث المسودة بنجاح.")
                if result and result.get("link"):
                    st.markdown(f"🔗 رابط المسودة: {result['link']}")
            except Exception as e:
                log_exception(logger, "wp.publish.error")
                st.error(f"فشل النشر: {e}")
