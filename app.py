# app.py — النسخة الكاملة (Logs مُحسّنة + Google Places + توليد + QC + منافسين + WordPress)
# ==========================================================================================
# المتطلبات (في .streamlit/secrets.toml):
#   OPENAI_API_KEY, GOOGLE_API_KEY, WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD
# الموديولات المطلوبة في utils/:
#   content_fetch.py, openai_client.py, exporters.py, competitor_analysis.py, quality_checks.py,
#   llm_reviewer.py, llm_cache.py, keywords.py, references.py, places_provider.py, wp_client.py, logging_setup.py
# ==========================================================================================

import os, io, re, csv, json, unicodedata
from datetime import datetime
from pathlib import Path
import streamlit as st

# إعداد مجلد بيانات
os.makedirs("data", exist_ok=True)

# --- اللوجينغ ---
from utils.logging_setup import (
    init_logging, get_logger, set_correlation_id, with_context, log_exception, set_level, tail
)
init_logging(app_name="restoguide", level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger("app")

# --- استيرادات داخلية ---
from utils.content_fetch import fetch_and_extract, configure_http_cache, clear_http_cache
try:
    from category_criteria import get_category_criteria
except ImportError:
    from modules.category_criteria import get_category_criteria

from utils.openai_client import get_client, chat_complete_cached
from utils.exporters import to_docx, to_json
from utils.competitor_analysis import analyze_competitors, extract_gap_points
from utils.quality_checks import quality_report
from utils.llm_reviewer import llm_review, llm_fix
from utils.llm_cache import LLMCacher
from utils.keywords import parse_required_keywords, enforce_report, FIX_PROMPT
from utils.references import normalize_refs, build_references_md, build_citation_map
from utils.places_provider import get_places_dataset, references_from_places, facts_markdown
from utils.wp_client import WPClient

# --- صفحة ---
st.set_page_config(page_title="مولد مقالات المطاعم", page_icon="🍽️", layout="wide")
st.title("🍽️ مولد مقالات المطاعم — E-E-A-T + Google Places + مراجع + WordPress + Logs")

def safe_rerun():
    if getattr(st, "rerun", None):
        st.rerun()
    else:
        st.experimental_rerun()

def _has_api_key() -> bool:
    try:
        if hasattr(st, "secrets") and st.secrets.get("OPENAI_API_KEY"):
            return True
    except Exception:
        pass
    return bool(os.getenv("OPENAI_API_KEY"))

def slugify(name: str) -> str:
    s = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))
    s = re.sub(r'\W+', '-', s).strip('-').lower()
    s = re.sub(r'-{2,}', '-', s)
    return s or "post"

def get_secret(key, default=""):
    try:
        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)

GOOGLE_API_KEY = get_secret("GOOGLE_API_KEY")
WP_BASE_URL    = get_secret("WP_BASE_URL", "")
WP_USERNAME    = get_secret("WP_USERNAME", "")
WP_APP_PASSWORD= get_secret("WP_APP_PASSWORD", "")

PROMPTS_DIR = Path("prompts")
def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")

# --- القوالب ---
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

# --- نصائح مكان ---
PLACE_TEMPLATES = {
    "مول/مجمع": "احجز قبل الذروة بـ20–30 دقيقة، راقب أوقات العروض/النافورة، وتجنّب طوابير المصاعد.",
    "جهة من المدينة (شمال/شرق..)": "الوصول أسهل عبر الطرق الدائرية قبل 7:30م، ومواقف الشوارع تمتلئ مبكرًا بالويكند.",
    "حيّ محدد": "المشي بعد العشاء خيار لطيف إن توفّرت أرصفة هادئة، والذروة تختلف بين الأيام.",
    "شارع/ممشى": "الجلسات الخارجية ألطف بعد المغرب صيفًا، والبرد الليلي قد يتطلّب مشروبًا ساخنًا شتاءً.",
    "واجهة بحرية/كورنيش": "الهواء أقوى مساءً—اطلب المشروبات سريعًا ويُفضّل المقاعد البعيدة عن التيارات.",
    "فندق/منتجع": "قد ترتفع الأسعار لكن الخدمة أدقّ، احجز باكرًا لأماكن النوافذ/الإطلالات.",
    "مدينة كاملة": "فروع سلسلة واحدة قد تختلف جودتها بين الأحياء، ابدأ بالطبق الأشهر لتقييم المستوى."
}
def build_protip_hint(place_type: str) -> str:
    return PLACE_TEMPLATES.get(place_type or "", "قدّم نصيحة عملية مرتبطة بالمكان والذروة وسهولة الوصول.")
def build_place_context(place_type: str, place_name: str, place_rules: str, strict: bool) -> str:
    scope = "صارم (التزم داخل النطاق فقط)" if strict else "مرن (الأولوية داخل النطاق)"
    return f"""سياق المكان:
- النوع: {place_type or "غير محدد"}
- الاسم: {place_name or "غير محدد"}
- قواعد النطاق: {place_rules or "—"}
- صرامة الالتزام بالنطاق: {scope}"""

# ========================= Sidebar =========================
st.sidebar.header("⚙️ الإعدادات العامة")
tone = st.sidebar.selectbox("نغمة الأسلوب",
    ["ناقد ودود","ناقد صارم","دليل تحريري محايد","ناقد صارم | مراجعات الجمهور","ناقد صارم | تجربة مباشرة + مراجعات"]
)
primary_model = st.sidebar.selectbox("الموديل الأساسي", ["gpt-4.1","gpt-4o","gpt-4o-mini"], index=0)
fallback_model = st.sidebar.selectbox("موديل بديل", ["gpt-4o","gpt-4o-mini","gpt-4.1"], index=1)
include_faq = st.sidebar.checkbox("إضافة FAQ", value=True)
include_methodology = st.sidebar.checkbox("إضافة منهجية التحرير", value=True)
add_human_touch = st.sidebar.checkbox("تفعيل Polish", value=True)
approx_len = st.sidebar.slider("الطول التقريبي (كلمات)", 600, 1800, 1100, step=100)

review_weight = None
if tone in ["ناقد صارم | مراجعات الجمهور","ناقد صارم | تجربة مباشرة + مراجعات"]:
    default_weight = 85 if tone == "ناقد صارم | مراجعات الجمهور" else 55
    review_weight = st.sidebar.slider("وزن الاعتماد على المراجعات (٪)", 0, 100, default_weight, step=5)

# كلمات إلزامية
st.sidebar.markdown("---")
st.sidebar.subheader("🧩 كلمات إلزامية")
kw_help = "سطر لكل كلمة/عبارة. لإجبار تكرارها: | min=2 مثل: جلسات خارجية | min=2"
required_kw_spec = st.sidebar.text_area("الكلمات", "مطاعم عائلية\nجلسات خارجية | min=2", height=110, help=kw_help)
required_list = parse_required_keywords(required_kw_spec)

# روابط داخلية
st.sidebar.markdown("---")
st.sidebar.subheader("🔗 روابط داخلية")
internal_catalog = st.sidebar.text_area(
    "عناوين/سلاگز (سطر لكل عنصر)",
    "أفضل مطاعم الرياض\nأفضل مطاعم إفطار في الرياض\nأفضل مطاعم بيتزا في جدة"
)

# مراجع + معلومات E-E-A-T
st.sidebar.markdown("---")
st.sidebar.subheader("📚 المراجع الخارجية")
refs_text = st.sidebar.text_area(
    "روابط (سطر لكل رابط)",
    "https://goo.gl/maps/\nhttps://www.timeoutdubai.com/\nhttps://www.michelin.com/",
    height=110
)
author_name = st.sidebar.text_input("اسم المؤلف", value="فريق التحرير")
reviewer_name = st.sidebar.text_input("اسم المراجع (اختياري)", value="")
last_verified = st.sidebar.text_input("آخر تحقق (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))

# كاش HTTP
st.sidebar.markdown("---")
st.sidebar.subheader("🧠 كاش HTTP (جلب صفحات)")
use_cache = st.sidebar.checkbox("تفعيل", value=True)
cache_hours = st.sidebar.slider("مدة (ساعات)", 1, 72, 24)
if st.sidebar.button("🧹 مسح كاش HTTP"):
    ok = clear_http_cache()
    st.sidebar.success("تم المسح." if ok else "لا توجد بيانات كاش.")
try:
    configure_http_cache(enabled=use_cache, hours=cache_hours)
except Exception as e:
    st.sidebar.warning(f"تعذّر تهيئة الكاش: {e}")

# كاش LLM
st.sidebar.markdown("---")
st.sidebar.subheader("🧠 كاش LLM")
llm_cache_enabled = st.sidebar.checkbox("تفعيل كاش LLM", value=True)
llm_cache_hours = st.sidebar.slider("مدة (ساعات)", 1, 72, 24)
if "llm_cacher" not in st.session_state:
    st.session_state["llm_cacher"] = LLMCacher(ttl_hours=llm_cache_hours, enabled=llm_cache_enabled)
else:
    st.session_state["llm_cacher"].configure(enabled=llm_cache_enabled, ttl_hours=llm_cache_hours)
if st.sidebar.button("🧹 مسح كاش LLM"):
    ok = st.session_state["llm_cacher"].clear()
    st.sidebar.success("تم المسح." if ok else "لا توجد بيانات.")

# Logs
st.sidebar.markdown("---")
st.sidebar.subheader("📜 Logs")
lvl = st.sidebar.selectbox("المستوى", ["DEBUG","INFO","WARNING","ERROR"], index=1)
set_level(lvl)
with st.sidebar.expander("آخر 300 سطر"):
    st.code(tail("logs/app.jsonl", 300), language="json")

# ========================= Tabs =========================
tab_places, tab_article, tab_comp, tab_qc, tab_wp = st.tabs([
    "🛰️ Google Places", "✍️ توليد المقال", "🆚 المنافسون", "🧪 فحوص الجودة", "📝 النشر"
])

# ------------------ Tab 0: Google Places ------------------
with tab_places:
    st.subheader("🛰️ جلب & تنقية — Google Places (ساعات الخميس ثابتة)")
    kw = st.text_input("الكلمة المفتاحية", "مطاعم برجر")
    city = st.text_input("المدينة", "الرياض")
    min_reviews = st.slider("حد أدنى للمراجعات", 0, 500, 50, step=10)
    max_results = st.slider("حد أقصى للنتائج", 10, 100, 40, step=10)
    st.caption("نستخرج ساعات **الخميس** فقط لكل مكان (ثابت كما طلبت).")

    colp1, colp2 = st.columns([1,1])
    with colp1:
        do_fetch = st.button("📥 جلب النتائج")
    with colp2:
        do_accept = st.button("✔️ اعتماد القائمة")

    if do_fetch:
        if not GOOGLE_API_KEY:
            st.error("لا يوجد GOOGLE_API_KEY في secrets.")
            logger.error("missing_google_api_key")
            st.stop()

        set_correlation_id()
        with with_context(action="places_fetch", keyword=kw, city=city, min_reviews=min_reviews, max_results=max_results):
            logger.info("places.fetch.start")
            try:
                with st.spinner("يجلب من Google Places..."):
                    places = get_places_dataset(GOOGLE_API_KEY, kw, city, min_reviews=min_reviews, max_results=max_results)
                    st.session_state["places_raw"] = places
                logger.info("places.fetch.done", extra={"count": len(places)})
            except Exception as e:
                log_exception(logger, "places.fetch.failed")
                st.error(f"فشل الجلب: {e}")
                places = []

        if places:
            if len(places) < 6:
                st.warning("القائمة أقل من 6 عناصر — قد تكون ضعيفة. جرّب خفض حدّ المراجعات أو توسيع الاستعلام.")
            import pandas as pd
            df = pd.DataFrame([{
                "name": p["name"],
                "rating": p.get("rating"),
                "reviews": p.get("reviews_count"),
                "price": p.get("price_band"),
                "الأوقات (الخميس)": p.get("thursday_range"),
                "phone": p.get("phone"),
                "website": p.get("website"),
                "google_url": p.get("google_url"),
            } for p in places])
            st.dataframe(df, use_container_width=True)
            st.markdown("#### حقائق مختصرة (تُمرَّر للبرومبت ولا تُطبع كما هي)")
            st.markdown(facts_markdown(places))

    if do_accept:
        snap = st.session_state.get("places_raw") or []
        if not snap:
            st.warning("لا قائمة جاهزة — اضغط (جلب النتائج) أولًا.")
        else:
            st.session_state["places_snapshot"] = snap
            st.session_state["places_references"] = references_from_places(snap)
            st.success(f"تم اعتماد {len(snap)} عنصرًا — انتقل لتوليد المقال.")
            logger.info("places.accepted", extra={"count": len(snap)})

# ------------------ Tab 1: Article Generation ------------------
with tab_article:
    col1, col2 = st.columns([2,1])
    with col1:
        article_title = st.text_input("عنوان المقال", "أفضل مطاعم في الرياض")
        keyword = st.text_input("الكلمة المفتاحية (اختياري)", "مطاعم في الرياض")

        COUNTRIES = {
            "السعودية": ["الرياض","جدة","مكة","المدينة المنورة","الدمام","الخبر","الظهران","الطائف","أبها","خميس مشيط","جازان","نجران","تبوك","بريدة","عنيزة","الهفوف","الأحساء","الجبيل","القطيف","ينبع","حائل"],
            "الإمارات": ["دبي","أبوظبي","الشارقة","عجمان","رأس الخيمة","الفجيرة","أم القيوين","العين"]
        }
        country = st.selectbox("الدولة", ["السعودية","الإمارات","أخرى…"], index=0)
        if country in COUNTRIES:
            city_choice = st.selectbox("المدينة", COUNTRIES[country] + ["مدينة مخصّصة…"], index=0)
            city_input = st.text_input("أدخل اسم المدينة", city_choice) if city_choice == "مدينة مخصّصة…" else city_choice
        else:
            country = st.text_input("اسم الدولة", "السعودية")
            city_input = st.text_input("المدينة", "الرياض")

        place_type = st.selectbox("نوع المكان",
            ["مول/مجمع","جهة من المدينة (شمال/شرق..)","حيّ محدد","شارع/ممشى","واجهة بحرية/كورنيش","فندق/منتجع","مدينة كاملة"], index=0)
        place_name = st.text_input("اسم المكان/النطاق", placeholder="مثلًا: دبي مول / شمال الرياض")
        place_rules = st.text_area("قواعد النطاق (اختياري)", height=70, placeholder="داخل المول فقط، أو أحياء معيّنة…")
        strict_in_scope = st.checkbox("التزم بالنطاق الجغرافي فقط (صارم)", value=True)

        content_scope = st.radio("نطاق المحتوى", ["فئة محددة داخل المكان","شامل بلا فئة","هجين (تقسيم داخلي)"], index=1 if place_type=="مول/مجمع" else 0)

        built_in_labels = list(CRITERIA_MAP.keys())
        category = "عام"
        criteria_block = GENERAL_CRITERIA

        is_custom_category = False
        if content_scope == "فئة محددة داخل المكان":
            category_choice = st.selectbox("الفئة", built_in_labels + ["فئة مخصّصة…"])
            if category_choice == "فئة مخصّصة…":
                if "pending_custom_criteria_text" in st.session_state:
                    st.session_state["custom_criteria_text"] = st.session_state.pop("pending_custom_criteria_text")
                custom_category_name = st.text_input("اسم الفئة المخصّصة", "مطاعم لبنانية", key="custom_category_name")
                DEFAULT_CRIT_MD = (
                    "- **التجربة المباشرة:** زيارات متعدّدة وتجربة أطباق أساسية.\n"
                    "- **المكوّنات:** جودة وطزاجة.\n"
                    "- **الأصالة/الطريقة:** التتبيل/الشوي/الفرن ومدى قرب النكهة من الأصل.\n"
                    "- **الأجواء:** ملاءمة العائلات/الأصدقاء.\n"
                    "- **ثبات الجودة:** عبر أوقات/زيارات مختلفة."
                )
                ta_kwargs = dict(key="custom_criteria_text", height=120)
                if "custom_criteria_text" not in st.session_state:
                    ta_kwargs["value"] = DEFAULT_CRIT_MD
                custom_criteria_text = st.text_area("معايير الاختيار (اختياري)", **ta_kwargs)
                category = (st.session_state.get("custom_category_name") or "فئة مخصّصة").strip()
                criteria_block = st.session_state.get("custom_criteria_text") or "اعتمدنا على التجربة، جودة المكونات، تنوع القائمة، وثبات الجودة."
                is_custom_category = True
            else:
                category = category_choice
                criteria_block = CRITERIA_MAP.get(category_choice, GENERAL_CRITERIA)
        else:
            category = "عام"
            criteria_block = GENERAL_CRITERIA

        # توليد/جلب معايير الفئة
        def _normalize_criteria(raw):
            if raw is None: return []
            if isinstance(raw, str):
                s = raw.strip()
                import json as _json
                if s.startswith(("[","{"])):
                    try: raw = _json.loads(s)
                    except Exception:
                        lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
                        return [ln for ln in lines if ln and ln.lower()!="undefined"]
                else:
                    lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
                    return [ln for ln in lines if ln and ln.lower()!="undefined"]
            if isinstance(raw, dict):
                for k in ("criteria","bullets","items","list"):
                    if k in raw: raw = raw[k]; break
                else:
                    vals = list(raw.values())
                    raw = vals if all(isinstance(v,str) for v in vals) else list(raw.keys())
            if isinstance(raw, (list,tuple)):
                out=[]
                for x in raw:
                    if isinstance(x,str): t=x.strip().strip(",").strip('"').strip("'")
                    elif isinstance(x,dict) and "text" in x: t=str(x["text"]).strip()
                    else: t=str(x).strip()
                    if t and t.lower()!="undefined": out.append(t)
                return out
            return [str(raw)]
        def _format_criteria_md(items):
            items = _normalize_criteria(items)
            return "\n".join(f"- {c}" for c in items) or "- —"

        effective_category = (category or "عام").strip()
        if "criteria_generated_md_map" not in st.session_state:
            st.session_state["criteria_generated_md_map"] = {}

        with st.expander("📋 معايير الاختيار لهذه الفئة (تلقائي/يدوي)", expanded=False):
            st.caption(f"الفئة الحالية: **{effective_category}**")
            use_llm = st.checkbox("تعزيز بالـ LLM (اختياري)", value=False, key="crit_llm")
            if st.button("جلب/توليد المعايير", key="btn_generate_criteria"):
                crit_list = get_category_criteria(effective_category, use_llm=use_llm, catalog_path="data/criteria_catalog.yaml")
                md = _format_criteria_md(crit_list)
                st.session_state["criteria_generated_md_map"].pop(effective_category, None)
                st.session_state["criteria_generated_md_map"][effective_category] = md
                if is_custom_category:
                    st.session_state["pending_custom_criteria_text"] = md
                    safe_rerun()
                else:
                    st.success("تم توليد المعايير.")
            if effective_category in st.session_state["criteria_generated_md_map"]:
                st.markdown("**المعايير (تلقائي):**")
                st.markdown(st.session_state["criteria_generated_md_map"][effective_category])

        if is_custom_category:
            criteria_block = st.session_state.get("custom_criteria_text", criteria_block)
        else:
            criteria_block = st.session_state.get("criteria_generated_md_map", {}).get(effective_category, criteria_block)

        restaurants_input = st.text_area("أدخل أسماء المطاعم (سطر لكل مطعم)", "مطعم 1\nمطعم 2\nمطعم 3", height=140)
        st.markdown("**أو** ارفع CSV بأسماء المطاعم (عمود name)")
        csv_file = st.file_uploader("رفع CSV (اختياري)", type=["csv"])

        def _normalize_name(s: str) -> str:
            return " ".join((s or "").strip().split())
        def _merge_unique(a: list, b: list) -> list:
            seen, out = set(), []
            for x in a + b:
                x2 = _normalize_name(x)
                if x2 and x2 not in seen:
                    seen.add(x2); out.append(x2)
            return out

        typed_restaurants = [r.strip() for r in restaurants_input.splitlines() if r.strip()]
        uploaded_restaurants = []
        if csv_file:
            try:
                text = csv_file.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                for row in reader:
                    name = row.get("name") or row.get("اسم") or ""
                    if name.strip():
                        uploaded_restaurants.append(name.strip())
            except Exception as e:
                st.warning(f"تعذّر قراءة CSV: {e}")
        restaurants = _merge_unique(typed_restaurants, uploaded_restaurants)

        manual_notes = st.text_area("ملاحظات تُدمج داخل التجارب (اختياري)", st.session_state.get("comp_gap_notes",""))

    with col2:
        st.subheader("قائمة تدقيق بشرية")
        checks = {
            "sensory":  st.checkbox("وصف حسي دقيق (مطعم واحد على الأقل)"),
            "personal": st.checkbox("ملاحظة شخصية/تفضيل"),
            "compare":  st.checkbox("مقارنة مع زيارة سابقة/مطعم مشابه"),
            "critique": st.checkbox("نقد غير متوقّع صغير"),
            "vary":     st.checkbox("تنوّع أطوال الفقرات"),
        }

    # Snapshot Google Places
    places_snapshot = st.session_state.get("places_snapshot") or []
    use_snapshot = False
    if places_snapshot:
        use_snapshot = st.checkbox("استخدام قائمة Google Places المعتمدة", value=True,
                                   help="يتم تمرير حقائق مختصرة (تشمل ساعات الخميس) للبرومبت + دمج مراجع Google تلقائيًا.")
    snapshot_refs = st.session_state.get("places_references") or []
    manual_refs = normalize_refs(refs_text)
    combined_refs = []
    for u in snapshot_refs + manual_refs:
        if u and u not in combined_refs:
            combined_refs.append(u)
    references_block_combined = build_references_md(combined_refs) if combined_refs else "—"
    citation_map = build_citation_map(combined_refs)

    facts_block = facts_markdown(places_snapshot) if (places_snapshot and use_snapshot) else "—"

    if st.button("🚀 توليد المقال"):
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
            st.stop()

        client = get_client()

        if tone == "ناقد صارم | مراجعات الجمهور":
            tone_instructions = ("اكتب كنّاقد صارم يعتمد أساسًا على مراجعات العملاء المنشورة علنًا. "
                                 "ركّز على الأنماط المتكررة واذكر حدود المنهجية. لا تدّعِ زيارة شخصية. لا تستخدم أرقام.")
            tone_selection_line = "اعتمدنا على مراجعات موثوقة منشورة علنًا حتى {last_updated}."
            system_tone = "أسلوب ناقد صارم مرتكز على مراجعات الجمهور"
        elif tone == "ناقد صارم | تجربة مباشرة + مراجعات":
            tone_instructions = ("اكتب كنّاقد صارم يمزج خبرة ميدانية مع مراجعات الجمهور. "
                                 "قدّم الحكم من التجربة المباشرة أولًا ثم قارنه بانطباعات الجمهور. أدرج **نقطة للتحسين** لكل مطعم.")
            tone_selection_line = "مزجنا بين زيارات ميدانية وتجارب عامة حتى {last_updated}."
            system_tone = "أسلوب ناقد صارم يمزج التجربة المباشرة مع مراجعات الجمهور"
        else:
            tone_instructions = "اكتب بأسلوب متوازن يراعي الدقة والوضوح دون مبالغة."
            tone_selection_line = "اعتمدنا على التجربة المباشرة ومعلومات موثوقة، مع مراجعة دورية."
            system_tone = tone

        if content_scope == "فئة محددة داخل المكان":
            scope_instructions = "التزم بالفئة المحددة فقط داخل النطاق الجغرافي."
        elif content_scope == "هجين (تقسيم داخلي)":
            scope_instructions = "قسّم المطاعم إلى أقسام منطقية ووازن التنوع."
        else:
            scope_instructions = "قدّم تشكيلة متنوعة تمثّل المكان."

        protip_hint = build_protip_hint(place_type)
        place_context = build_place_context(place_type, place_name, place_rules, strict_in_scope)
        last_updated = datetime.now().strftime("%B %Y")
        faq_block = "—"
        if include_faq:
            faq_prompt = (
                "اكتب 5–8 أسئلة شائعة وإجابات قصيرة بالعربية.\n"
                "عند الاستشهاد بمصدر خارجي استخدم حاشية [^n] برقم مرجعي.\n"
                f"المدينة: {place_name or city_input} — الفئة: {category}\n"
                "مواضيع: الحجز/الذروة/الجلسات/العائلات/اللباس/المواقف/الدفع.\n"
                "إن كانت المعلومة من خبرة تحريرية، اذكر (خبرة تحريرية) بدل الحاشية."
            )
            faq_messages = [
                {"role": "system", "content": "أنت محرر عربي محترف يكتب FAQ موجزًا ومدعومًا بالمراجع دون اختلاق."},
                {"role": "user", "content": faq_prompt}
            ]
            try:
                faq_block = chat_complete_cached(
                    client, faq_messages,
                    model=primary_model, fallback_model=fallback_model,
                    temperature=0.4, max_tokens=700,
                    cacher=st.session_state["llm_cacher"],
                    cache_extra={"task":"faq_sources","city":place_name or city_input}
                )
            except Exception:
                faq_block = FAQ_TMPL.format(category=category, city=place_name or city_input)

        methodology_block = METH_TMPL.format(last_updated=last_updated) if include_methodology else "—"
        req_md = "\n".join([f"- **{kw}** — حد أدنى: {need} مرّة" for kw, need in required_list]) if required_list else "—"
        references_block = references_block_combined

        base_prompt = BASE_TMPL.format(
            title=article_title, keyword=keyword, content_scope=content_scope, category=category,
            restaurants_list=", ".join(restaurants), criteria_block=criteria_block, faq_block=faq_block,
            methodology_block=methodology_block, tone_label=tone, place_context=place_context,
            protip_hint=protip_hint, scope_instructions=scope_instructions, tone_instructions=tone_instructions,
            tone_selection_line=tone_selection_line.replace("{last_updated}", last_updated),
            required_keywords_block=req_md, approx_len=approx_len,
            references_block=references_block
        )
        if use_snapshot and places_snapshot:
            base_prompt += "\n\n## بيانات Google (مختصرة — لا تُطبع كما هي)\n" + facts_block

        set_correlation_id()
        with with_context(action="article_generate", title=article_title, city=city_input, use_snapshot=bool(places_snapshot)):
            logger.info("article.generate.start")
            try:
                base_messages = [
                    {"role": "system",
                     "content": (
                         f"اكتب بالعربية الفصحى. {system_tone}. طول تقريبي {approx_len} كلمة."
                         " التزم بالنطاق ولا تختلق حقائق أو مصادر. عند الاستشهاد بمصدر خارجي استخدم حاشية [^n] فقط."
                     )},
                    {"role": "user", "content": base_prompt},
                ]
                article_md = chat_complete_cached(
                    client, base_messages,
                    max_tokens=2200, temperature=0.7,
                    model=primary_model, fallback_model=fallback_model,
                    cacher=st.session_state["llm_cacher"],
                    cache_extra={"task":"article_base","required": required_list,"use_snapshot": use_snapshot}
                )
                logger.info("article.generate.done", extra={"chars": len(article_md)})
            except Exception as e:
                log_exception(logger, "article.generate.failed")
                st.error(f"فشل التوليد: {e}")
                st.stop()

        apply_polish = add_human_touch or any(checks.values())
        merged_user_notes = (st.session_state.get("comp_gap_notes","") + "\n" + (manual_notes or "")).strip()
        if apply_polish or merged_user_notes:
            polish_prompt = POLISH_TMPL.format(article=article_md, user_notes=merged_user_notes)
            polish_messages = [
                {"role": "system", "content": "أنت محرر عربي محترف، تحافظ على الحقائق وتضيف لمسات بشرية بدون مبالغة."},
                {"role": "user", "content": polish_prompt},
            ]
            try:
                article_md = chat_complete_cached(
                    client, polish_messages,
                    max_tokens=2400, temperature=0.8,
                    model=primary_model, fallback_model=fallback_model,
                    cacher=st.session_state["llm_cacher"],
                    cache_extra={"task":"polish"}
                )
            except Exception as e:
                st.warning(f"طبقة اللمسات البشرية تعذّرت: {e}")

        # التزام الكلمات الإلزامية
        kw_report = enforce_report(article_md, required_list)
        st.subheader("🧩 التزام الكلمات الإلزامية")
        if not required_list:
            st.caption("لم تُحدّد كلمات إلزامية.")
        else:
            rows = []
            for item in kw_report["items"]:
                status = "✅" if item["ok"] else "❌"
                rows.append(f"- {status} **{item['keyword']}** — مطلوب {item['min']}, وُجدت {item['found']}")
            st.markdown("\n".join(rows))

            if not kw_report["ok"]:
                needs_lines = "\n".join([f"- {m['keyword']}: نحتاج +{m['need']}" for m in kw_report["missing"]])
                if st.button("✍️ إدماج تلقائي للكلمات الناقصة"):
                    fix_msgs = [
                        {"role": "system", "content": "أنت محرر عربي دقيق تُدخل كلمات مطلوبة بنعومة وبدون حشو."},
                        {"role": "user", "content": FIX_PROMPT.format(orig=article_md[:12000], needs=needs_lines)}
                    ]
                    try:
                        article_md = chat_complete_cached(
                            client, fix_msgs,
                            max_tokens=2400, temperature=0.5,
                            model=primary_model, fallback_model=fallback_model,
                            cacher=st.session_state["llm_cacher"],
                            cache_extra={"task":"kw_fix","needs": kw_report["missing"]}
                        )
                        kw_report = enforce_report(article_md, required_list)
                        st.success("تم إدماج الكلمات.")
                        st.markdown("\n".join(
                            [f"- {'✅' if it['ok'] else '❌'} **{it['keyword']}** — مطلوب {it['min']}, وُجدت {it['found']}"
                             for it in kw_report["items"]]
                        ))
                    except Exception as e:
                        st.warning(f"تعذّر الإدماج: {e}")

        # Meta
        meta_prompt = f"صِغ عنوان SEO (≤ 60) ووصف ميتا (≤ 155) بالعربية لمقال بعنوان \"{article_title}\". الكلمة المفتاحية: {keyword}.\nTITLE: ...\nDESCRIPTION: ..."
        try:
            meta_out = chat_complete_cached(
                client,
                [{"role":"system","content":"أنت مختص SEO عربي."},{"role":"user","content": meta_prompt}],
                max_tokens=200, temperature=0.6,
                model=primary_model, fallback_model=fallback_model,
                cacher=st.session_state["llm_cacher"],
                cache_extra={"task":"meta"}
            )
        except Exception:
            meta_out = f"TITLE: {article_title}\nDESCRIPTION: دليل عملي عن {keyword}."

        # روابط داخلية
        links_catalog = [s.strip() for s in internal_catalog.splitlines() if s.strip()]
        links_prompt = (
            f"اقترح 3 روابط داخلية مناسبة من هذه القائمة إن أمكن:\n{links_catalog}\n"
            f"العنوان: {article_title}\nالنطاق: {content_scope}\nالفئة: {category}\n"
            f"المدينة/المكان: {place_name or city_input}\nمقتطف:\n{article_md[:800]}\n"
            "- رابط داخلي مقترح: <النص>\n- رابط داخلي مقترح: <النص>\n- رابط داخلي مقترح: <النص>"
        )
        try:
            links_out = chat_complete_cached(
                client,
                [{"role":"system","content":"أنت محرر عربي يقترح روابط داخلية طبيعية."},{"role":"user","content": links_prompt}],
                max_tokens=240, temperature=0.5,
                model=primary_model, fallback_model=fallback_model,
                cacher=st.session_state["llm_cacher"],
                cache_extra={"task":"internal_links"}
            )
        except Exception:
            links_out = "- رابط داخلي مقترح: أفضل مطاعم الرياض\n- رابط داخلي مقترح: دليل مطاعم العائلات في الرياض\n- رابط داخلي مقترح: مقارنة بين الأنماط"

        # عرض
        st.subheader("📄 المقال الناتج")
        st.markdown(article_md)
        st.session_state['last_article_md'] = article_md
        st.session_state['generated_title'] = article_title

        st.subheader("🔎 Meta (SEO)"); st.code(meta_out, language="text")
        st.subheader("🔗 روابط داخلية"); st.markdown(links_out)

        # JSON-LD
        jsonld = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Article",
                    "headline": article_title,
                    "inLanguage": "ar",
                    "keywords": [keyword] if keyword else [],
                    "genre": ["دليل مطاعم","مراجعات"],
                    "articleBody": article_md[:5000],
                    "datePublished": datetime.now().strftime("%Y-%m-%d"),
                    "dateModified": datetime.now().strftime("%Y-%m-%d"),
                    "author": {"@type": "Person", "name": author_name} if author_name else {"@type":"Organization","name":"فريق التحرير"},
                    **({"reviewedBy": {"@type": "Person", "name": reviewer_name}} if reviewer_name else {}),
                    "isAccessibleForFree": True,
                    "mainEntityOfPage": {"@type": "WebPage", "name": article_title},
                    "citation": list(citation_map.values()),
                }
            ]
        }
        # FAQPage إن توفّر
        faq_pairs = []
        try:
            import re as _re
            blocks = _re.findall(r"-\s*\*\*(.+?)\*\*\s*\n([^\n].*?)(?=\n- \*\*|\Z)", faq_block, flags=_re.DOTALL)
            for q, a in blocks:
                q = q.strip(); a = a.strip()
                if q and a:
                    faq_pairs.append({"question": q, "answer": a})
        except Exception:
            pass
        if faq_pairs:
            jsonld["@graph"].append({
                "@type": "FAQPage",
                "inLanguage": "ar",
                "mainEntity": [
                    {"@type": "Question", "name": qa["question"], "acceptedAnswer": {"@type":"Answer","text": qa["answer"]}}
                    for qa in faq_pairs[:12]
                ]
            })

        jsonld_str = json.dumps(jsonld, ensure_ascii=False, indent=2)
        st.session_state["jsonld_str"] = jsonld_str
        st.subheader("🧾 JSON-LD")
        st.code(jsonld_str, language="json")
        st.download_button("⬇️ تنزيل JSON-LD", data=jsonld_str, file_name=f"{slugify(article_title)}.json", mime="application/ld+json")

        # حفظ JSON تلخيصي
        json_obj = {"title": article_title, "keyword": keyword, "category": category,
            "country": country, "city": city_input,
            "place": {"type": place_type, "name": place_name, "rules": place_rules, "strict": strict_in_scope},
            "content_scope": content_scope, "restaurants": restaurants, "last_updated": datetime.now().strftime("%B %Y"),
            "tone": tone, "reviews_weight": review_weight, "models": {"primary": primary_model, "fallback": fallback_model},
            "include_faq": include_faq, "include_methodology": include_methodology,
            "article_markdown": article_md, "meta": meta_out, "internal_links": links_out,
            "references": list(citation_map.values()), "author": author_name, "reviewer": reviewer_name, "last_verified": last_verified,
            "places_snapshot": st.session_state.get("places_snapshot", [])
        }
        st.session_state['last_json'] = to_json(json_obj)

    with col2:
        colA, colB, colC = st.columns(3)
        with colA:
            md_data = st.session_state.get('last_article_md', '')
            st.download_button('💾 تنزيل Markdown', data=md_data, file_name='article.md', mime='text/markdown')
        with colB:
            md_data = st.session_state.get('last_article_md', '')
            st.download_button('📝 تنزيل DOCX', data=to_docx(md_data), file_name='article.docx', mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        with colC:
            json_data = st.session_state.get('last_json', '{}')
            st.download_button('🧩 تنزيل JSON', data=json_data, file_name='article.json', mime='application/json')

# ------------------ Tab 2: Competitor Analysis ------------------
with tab_comp:
    st.subheader("🆚 تحليل أول منافسين — روابط يدوية")
    st.caption("نحلّل المحتوى من زاوية المحتوى وE-E-A-T فقط.")
    query = st.text_input("استعلام البحث", "أفضل مطاعم دبي مول")
    place_scope_desc = st.text_input("وصف النطاق/المكان (اختياري)", "داخل دبي مول فقط")
    url_a = st.text_input("رابط المنافس A", "")
    url_b = st.text_input("رابط المنافس B", "")

    tone_for_analysis = st.selectbox("نبرة التحليل",
        ["ناقد صارم | مراجعات الجمهور", "ناقد صارم | تجربة مباشرة + مراجعات", "دليل تحريري محايد"], index=0)
    reviews_weight_analysis = st.slider("وزن الاعتماد على المراجعات (٪)", 0, 100, 60, step=5)

    colx, coly = st.columns(2)
    with colx: fetch_btn = st.button("📥 جلب المحتوى")
    with coly: analyze_btn = st.button("🧠 تنفيذ التحليل")

    if fetch_btn:
        if not url_a or not url_b:
            st.warning("أدخل رابطين أولًا.")
        else:
            try:
                with st.spinner("جلب الصفحة A..."):
                    page_a = fetch_and_extract(url_a)
                with st.spinner("جلب الصفحة B..."):
                    page_b = fetch_and_extract(url_b)
                st.session_state["comp_pages"] = {"A": page_a, "B": page_b}
                st.success("تم الجلب.")
                st.write("**A:**", page_a.get("title") or url_a, f"({page_a['word_count']} كلمة)")
                st.write("**B:**", page_b.get("title") or url_b, f"({page_b['word_count']} كلمة)")
            except Exception as e:
                st.error(f"تعذّر الجلب: {e}")

    if analyze_btn:
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
            st.stop()
        pages = st.session_state.get("comp_pages")
        if not pages:
            st.warning("الرجاء جلب المحتوى أولًا.")
        else:
            client = get_client()
            try:
                with st.spinner("يشغّل التحليل..."):
                    analysis_md = analyze_competitors(
                        client, primary_model, fallback_model,
                        pages["A"], pages["B"],
                        query, place_scope_desc or "—",
                        tone_for_analysis, reviews_weight_analysis
                    )
                st.session_state["comp_analysis_md"] = analysis_md
                st.subheader("📊 تقرير التحليل"); st.markdown(analysis_md)
                gaps = extract_gap_points(analysis_md)
                if gaps:
                    st.info("تم استخراج توصيات Gap-to-Win — يمكنك حقنها في برومبت المقال.")
                    st.text_area("التوصيات المستخرجة", gaps, key="comp_gap_notes", height=150)
                else:
                    st.warning("لم يتم العثور على قسم 'Gap-to-Win'.")
            except Exception as e:
                st.error(f"تعذّر التحليل: {e}")

# ------------------ Tab 3: QC ------------------
with tab_qc:
    st.subheader("🧪 فحوص الجودة")
    qc_text = st.text_area("الصق نص المقال هنا", st.session_state.get("last_article_md",""), height=260)
    col_q1, col_q2, col_q3 = st.columns(3)
    with col_q1:
        do_fluff = st.checkbox("كشف الحشو والعبارات القالبية", value=True)
    with col_q2:
        do_eeat = st.checkbox("مؤشرات E-E-A-T", value=True)
    with col_q3:
        do_llm_review = st.checkbox("تشخيص مُرشد (LLM)", value=True)

    if st.button("🔎 تحليل سريع"):
        if not qc_text.strip():
            st.warning("الصق النص أولًا.")
        else:
            rep = quality_report(qc_text)
            st.session_state["qc_report"] = rep
            st.markdown("### بطاقة الدرجات")
            c1, c2, c3, c4 = st.columns(4)
            with c1: st.metric("Human-style", rep["human_style_score"])
            with c2: st.metric("Sensory %", rep["sensory_ratio"])
            with c3: st.metric("TTR", rep["ttr"])
            with c4: st.metric("Passive %", rep["passive_ratio"])

            st.markdown("#### بنية النص")
            colA, colB, colC = st.columns(3)
            with colA:
                st.write(f"- كلمات: **{rep['word_count']}**")
                st.write(f"- جمل: **{rep['sentence_count']}**")
                st.write(f"- فقرات: **{rep['paragraph_count']}**")
            with colB:
                st.write(f"- متوسط طول الجملة: **{rep['avg_sentence_length']}**")
                st.write(f"- طول الفقرة: **{rep['paragraph_metrics']['avg_len']}** ± {rep['paragraph_metrics']['std_len']}")
            with colC:
                st.write(f"- فقرات قصيرة(<20): **{rep['paragraph_metrics']['pct_short_lt20w']}%**")
                st.write(f"- فقرات طويلة(>100): **{rep['paragraph_metrics']['pct_long_gt100w']}%**")

            st.markdown("#### تنوّع بدايات الجمل")
            st.json({"top_starts": rep["sentence_variety"]["start_top"], "start_hhi": rep["sentence_variety"]["start_hhi"]})

            st.markdown("#### E-E-A-T & Information Gain")
            m1, m2, m3 = st.columns(3)
            with m1: st.metric("E-E-A-T", rep["eeat_score"])
            with m2: st.metric("Info Gain", rep["info_gain_score"])
            with m3: st.metric("Fluff Density", rep["fluff_density"])
            st.json(rep["eeat"])

            if do_fluff:
                st.markdown("#### تكرار العبارات (N-grams)")
                reps = rep.get("repeated_phrases") or []
                for g, c in reps:
                    st.write(f"- `{g}` × {c}")
                st.markdown("#### عبارات قالبية مرصودة")
                for f in rep.get("boilerplate_flags") or []:
                    st.write(f"- **نمط:** `{f['pattern']}` — …{f['excerpt']}…")

            st.markdown("#### الميل العاطفي")
            st.json(rep["sentiment"])

            if do_eeat:
                st.markdown("#### العناوين والأقسام")
                st.json(rep["headings"])

            st.markdown("#### توصيات ذكية")
            for tip in rep["tips"]:
                st.write(f"- {tip}")

            st.success("انتهى التحليل.")
            st.session_state["qc_text"] = qc_text

    if do_llm_review and st.button("🧠 تشخيص مُرشد (LLM)"):
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
    st.markdown("#### إصلاح ذكي لأجزاء معيّنة")
    flagged_block = st.text_area("ألصق المقاطع الضعيفة (سطر لكل مقطع)", height=120)
    if st.button("✍️ إعادة صياغة المقاطع المحددة"):
        if not flagged_block.strip():
            st.warning("أدخل المقاطع أولًا.")
        elif not qc_text.strip():
            st.warning("لا يوجد نص أساس.")
        elif not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
        else:
            client = get_client()
            new_text = llm_fix(client, primary_model, fallback_model, qc_text, flagged_block.splitlines())
            st.markdown("### النص بعد الإصلاح"); st.markdown(new_text)
            st.session_state["last_article_md"] = new_text
            st.success("تم الإصلاح.")

# ------------------ Tab 4: Publish to WordPress ------------------
with tab_wp:
    st.subheader("📝 النشر على ووردبريس (Draft/Publish)")
    with st.expander("إعدادات ووردبريس (secrets.toml)", expanded=False):
        st.code(f"WP_BASE_URL={WP_BASE_URL}\nWP_USERNAME={WP_USERNAME}\n(كلمة التطبيق مخفية)")

    publishable = bool(st.session_state.get("last_article_md"))
    if not publishable:
        st.info("لا يوجد محتوى منشأ بعد. أنشئ المقال من تبويب ✍️ أولًا.")
    else:
        article_title_wp = st.text_input("عنوان ووردبريس", value=st.session_state.get("generated_title") or "مسودة: مقال مطاعم")
        wp_status = st.selectbox("حالة النشر", ["draft","publish"], index=0)
        city_cat = st.text_input("تصنيف المدينة (Category)", value=st.session_state.get("city_for_wp") or "الرياض")
        type_cat = st.text_input("تصنيف الفئة (Category)", value=st.session_state.get("type_for_wp") or "برجر")
        extra_tags = st.text_input("وسوم (Tags)", value="مطاعم, عائلات, جلسات خارجية")
        add_jsonld = st.checkbox("إرفاق JSON-LD داخل المحتوى", value=True)
        add_snapshot_meta = st.checkbox("حفظ places_snapshot كـ meta + تعليق مخفي", value=True)

        st.markdown("#### معاينة مختصرة")
        st.text_area("نص المقال", value=st.session_state.get("last_article_md","")[:1800], height=160)

        if st.button("🚀 نشر/تحديث (Upsert)"):
            if not (WP_BASE_URL and WP_USERNAME and WP_APP_PASSWORD):
                st.error("إعدادات ووردبريس غير مكتملة في secrets.toml")
                st.stop()
            try:
                set_correlation_id()
                with with_context(action="wp_upsert", status=wp_status, city=city_cat, type=type_cat):
                    logger.info("wp.upsert.start")
                    client = WPClient(WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD)

                    cat_ids = []
                    if city_cat.strip():
                        cid = client.ensure_category(city_cat.strip()); 
                        if cid: cat_ids.append(cid)
                    if type_cat.strip():
                        tid = client.ensure_category(type_cat.strip()); 
                        if tid: cat_ids.append(tid)

                    tag_ids = []
                    for t in [x.strip() for x in extra_tags.split(",") if x.strip()]:
                        tg = client.ensure_tag(t)
                        if tg: tag_ids.append(tg)

                    article_md = st.session_state.get("last_article_md","")
                    content_parts = [article_md]

                    jsonld_str = st.session_state.get("jsonld_str")
                    if add_jsonld and jsonld_str:
                        content_parts.append(f'<script type="application/ld+json">\n{jsonld_str}\n</script>')

                    places_snapshot = st.session_state.get("places_snapshot", [])
                    if add_snapshot_meta and places_snapshot:
                        try:
                            snap_txt = json.dumps(places_snapshot, ensure_ascii=False)
                        except Exception:
                            snap_txt = "[]"
                        content_parts.append(f"<!-- places_json:{snap_txt} -->")

                    content_html = "\n\n".join(content_parts)
                    meta = {}
                    if add_snapshot_meta and places_snapshot:
                        try:
                            meta["places_json"] = places_snapshot
                        except Exception:
                            pass

                    slug = slugify(article_title_wp)
                    # excerpt من الميتا السابق أو بداية النص
                    meta_out = st.session_state.get("last_json", "{}")
                    try:
                        meta_obj = json.loads(meta_out)
                        excerpt = (meta_obj.get("meta") or "").replace("TITLE:", "").replace("DESCRIPTION:", "").strip()
                        excerpt = excerpt.splitlines()[-1][:155] if excerpt else article_md[:155]
                    except Exception:
                        excerpt = article_md[:155]

                    resp = client.upsert_post(
                        title=article_title_wp or "مسودة بدون عنوان",
                        slug=slug,
                        content_html=content_html,
                        status=wp_status,
                        categories=cat_ids or None,
                        tags=tag_ids or None,
                        excerpt=excerpt,
                        meta=meta or None
                    )
                    link = resp.get("link") or "(no link)"
                    logger.info("wp.upsert.done", extra={"post_id": resp.get("id"), "link": link})
                    st.success(f"تم النشر/التحديث بنجاح. الرابط: {link}")
                    st.write(resp)
            except Exception as e:
                log_exception(logger, "wp.upsert.failed")
                st.error(f"فشل النشر: {e}")
