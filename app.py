# app.py — نسخة كاملة محدثة (دمج سطحي نظيف مع Google Places + ثبات ساعات الخميس + النشر لووردبريس)
# =====================================================================================================
# يتضمن:
# 1) تبويب 🛰️ Google Places (جلب & تنقية & اعتماد قائمة) — ساعات العمل ثابتة على "الخميس"
# 2) تمرير Snapshot المعتمد إلى مولّد المقال ليُستخدم كوقود حقائق + مراجع (مع كلمات إلزامية + FAQ + JSON-LD)
# 3) تبويب 📝 النشر على ووردبريس (Draft/Publish) مع upsert ومنع التكرار + تصنيفات/وسوم + تضمين JSON-LD
#
# المتطلبات:
# - ضع مفاتيحك في .streamlit/secrets.toml:
#   GOOGLE_API_KEY, OPENAI_API_KEY, WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD
# - تأكد من وجود الوحدات التالية في utils/:
#   content_fetch.py, openai_client.py, exporters.py, competitor_analysis.py, quality_checks.py,
#   llm_reviewer.py, llm_cache.py, keywords.py, references.py, places_provider.py, wp_client.py
# =====================================================================================================

import os
import io
import re
import csv
import json
import unicodedata
from datetime import datetime
from pathlib import Path

import streamlit as st

# --- إعداد مجلد بيانات عام ---
os.makedirs("data", exist_ok=True)

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

# --- إعداد الصفحة ---
st.set_page_config(page_title="مولد مقالات المطاعم (E-E-A-T)", page_icon="🍽️", layout="wide")
st.title("🍽️ مولد مقالات المطاعم — E-E-A-T + Google Places + كلمات إلزامية + مراجع + منافسين + QC + WordPress")

# --- أدوات مساعدة ---
def safe_rerun():
    if getattr(st, "rerun", None):
        st.rerun()  # Streamlit >= 1.30
    else:
        st.experimental_rerun()

def _has_api_key() -> bool:
    try:
        if hasattr(st, "secrets") and "OPENAI_API_KEY" in st.secrets and st.secrets["OPENAI_API_KEY"]:
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

# --- تحميل القوالب ---
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

# --- نصائح المكان ---
PLACE_TEMPLATES = {
    "مول/مجمع": "احجز قبل الذروة بـ20–30 دقيقة، راقب أوقات العروض/النافورة، وتجنّب طوابير المصاعد.",
    "جهة من المدينة (شمال/شرق..)": "الوصول أسهل عبر الطرق الدائرية قبل 7:30م، مواقف الشوارع قد تمتلئ مبكرًا في الويكند.",
    "حيّ محدد": "المشي بعد العشاء خيار لطيف إن توفّرت أرصفة هادئة، انتبه لاختلاف الذروة بين أيام الأسبوع والويكند.",
    "شارع/ممشى": "الجلسات الخارجية ألطف بعد المغرب صيفًا، والبرد الليلي قد يتطلّب مشروبًا ساخنًا شتاءً.",
    "واجهة بحرية/كورنيش": "الهواء أقوى مساءً—اطلب المشروبات سريعًا ويُفضّل المقاعد البعيدة عن التيارات.",
    "فندق/منتجع": "قد ترتفع الأسعار لكن الخدمة أدقّ، احجز باكرًا لأماكن النوافذ/الإطلالات.",
    "مدينة كاملة": "فروع سلسلة واحدة قد تختلف جودتها بين الأحياء، اطلب الطبق الأشهر أولًا لتقييم المستوى."
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
tone = st.sidebar.selectbox(
    "نغمة الأسلوب",
    ["ناقد ودود", "ناقد صارم", "دليل تحريري محايد", "ناقد صارم | مراجعات الجمهور", "ناقد صارم | تجربة مباشرة + مراجعات"]
)
primary_model = st.sidebar.selectbox("اختر الموديل الأساسي", ["gpt-4.1", "gpt-4o", "gpt-4o-mini"], index=0)
fallback_model = st.sidebar.selectbox("موديل بديل (Fallback)", ["gpt-4o", "gpt-4o-mini", "gpt-4.1"], index=1)
include_faq = st.sidebar.checkbox("إضافة قسم FAQ", value=True)
include_methodology = st.sidebar.checkbox("إضافة منهجية التحرير", value=True)
add_human_touch = st.sidebar.checkbox("تفعيل طبقة اللمسات البشرية (Polish)", value=True)
approx_len = st.sidebar.slider("الطول التقريبي (كلمات)", 600, 1800, 1100, step=100)

review_weight = None
if tone in ["ناقد صارم | مراجعات الجمهور", "ناقد صارم | تجربة مباشرة + مراجعات"]:
    default_weight = 85 if tone == "ناقد صارم | مراجعات الجمهور" else 55
    review_weight = st.sidebar.slider("وزن الاعتماد على المراجعات (٪)", 0, 100, default_weight, step=5)

# الكلمات الإلزامية
st.sidebar.markdown("---")
st.sidebar.subheader("🧩 كلمات مرتبطة إلزامية")
kw_help = "اكتب كل كلمة/عبارة بسطر مستقل. لإجبار تكرارها ضع | min=2 مثل: جلسات خارجية | min=2"
required_kw_spec = st.sidebar.text_area("أدخل الكلمات الإلزامية", value="مطاعم عائلية\nجلسات خارجية | min=2", height=120, help=kw_help)
required_list = parse_required_keywords(required_kw_spec)

# روابط داخلية
st.sidebar.markdown("---")
st.sidebar.subheader("🔗 روابط داخلية (اختياري)")
internal_catalog = st.sidebar.text_area(
    "أدخل عناوين/سلاگز مقالاتك (سطر لكل عنصر)",
    "أفضل مطاعم الرياض\nأفضل مطاعم إفطار في الرياض\nأفضل مطاعم بيتزا في جدة"
)

# المراجع الخارجية + المؤلف/المراجع
st.sidebar.markdown("---")
st.sidebar.subheader("📚 المراجع الخارجية")
refs_text = st.sidebar.text_area(
    "روابط مصادر موثوقة (سطر لكل رابط)",
    value="https://goo.gl/maps/\nhttps://www.timeoutdubai.com/\nhttps://www.michelin.com/",
    height=120,
    help="أدخل روابط صفحات/تقارير/أدلة موثوقة للاستشهاد بها."
)
author_name = st.sidebar.text_input("اسم المؤلف/المحرر", value="فريق التحرير")
reviewer_name = st.sidebar.text_input("اسم المراجِع (اختياري)", value="")
last_verified = st.sidebar.text_input("تاريخ آخر تحقق (YYYY-MM-DD)", value=datetime.now().strftime("%Y-%m-%d"))

# كاش HTTP (لجلب الروابط)
st.sidebar.markdown("---")
st.sidebar.subheader("🧠 الكاش (جلب الروابط)")
use_cache = st.sidebar.checkbox("تفعيل كاش HTTP", value=True, help="يُسرّع جلب الصفحات ويقلّل الطلبات الخارجية.")
cache_hours = st.sidebar.slider("مدة كاش HTTP (ساعات)", 1, 72, 24)
if st.sidebar.button("🧹 مسح كاش HTTP"):
    ok = clear_http_cache()
    st.sidebar.success("تم مسح كاش HTTP." if ok else "لا توجد بيانات كاش.")
try:
    configure_http_cache(enabled=use_cache, hours=cache_hours)
except Exception as e:
    st.sidebar.warning(f"تعذّر تهيئة كاش HTTP: {e}")

# كاش LLM
st.sidebar.markdown("---")
st.sidebar.subheader("🧠 كاش الـLLM")
llm_cache_enabled = st.sidebar.checkbox("تفعيل كاش مخرجات LLM", value=True, help="يقلل الوقت والتكلفة أثناء التطوير.")
llm_cache_hours = st.sidebar.slider("مدة كاش LLM (ساعات)", 1, 72, 24)
if "llm_cacher" not in st.session_state:
    st.session_state["llm_cacher"] = LLMCacher(ttl_hours=llm_cache_hours, enabled=llm_cache_enabled)
else:
    st.session_state["llm_cacher"].configure(enabled=llm_cache_enabled, ttl_hours=llm_cache_hours)
if st.sidebar.button("🧹 مسح كاش LLM"):
    ok = st.session_state["llm_cacher"].clear()
    st.sidebar.success("تم مسح كاش LLM." if ok else "لا توجد بيانات كاش.")

# ========================= Tabs =========================
tab_places, tab_article, tab_comp, tab_qc, tab_wp = st.tabs([
    "🛰️ Google Places", "✍️ توليد المقال", "🆚 تحليل المنافسين (روابط يدوية)", "🧪 فحص بشرية وجودة المحتوى", "📝 النشر على ووردبريس"
])

# ------------------ Tab 0: Google Places (جلب & تنقية) ------------------
with tab_places:
    st.subheader("🛰️ جلب & تنقية — Google Places (ساعات الخميس ثابتة)")
    kw = st.text_input("الكلمة المفتاحية", "مطاعم برجر")
    city = st.text_input("المدينة", "الرياض")
    min_reviews = st.slider("الحد الأدنى لعدد المراجعات", 0, 500, 50, step=10, help="فلترة أولية لرفع الجودة")
    max_results = st.slider("الحد الأقصى للنتائج (قبل التنقية)", 10, 100, 40, step=10)
    st.caption("سيتم استخراج **ساعات يوم الخميس** تحديدًا لكل مكان، كما طلبت.")

    colp1, colp2 = st.columns([1,1])
    with colp1:
        do_fetch = st.button("📥 جلب النتائج")
    with colp2:
        do_accept = st.button("✔️ اعتماد القائمة لاستخدامها في المقال")

    if do_fetch:
        if not GOOGLE_API_KEY:
            st.error("لا يوجد GOOGLE_API_KEY داخل secrets. أضِفه إلى .streamlit/secrets.toml")
            st.stop()
        with st.spinner("يجلب من Google Places..."):
            try:
                places = get_places_dataset(GOOGLE_API_KEY, kw, city, min_reviews=min_reviews, max_results=max_results)
                st.session_state["places_raw"] = places
            except Exception as e:
                st.error(f"فشل الجلب: {e}")
                places = []
        if places:
            st.success(f"تم الجلب: {len(places)} عنصرًا بعد التنقية والترتيب.")
            if len(places) < 6:
                st.warning("القائمة أقل من 6 عناصر — قد تكون ضعيفة. جرّب خفض حدّ المراجعات أو توسيع الإستعلام.")
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
            st.markdown("#### حقائق مختصرة (ستُمرَّر للبرومبت — لا تُطبع كما هي):")
            st.markdown(facts_markdown(places))
        else:
            st.info("لا نتائج بعد. أدخل كلمة مفتاحية ومدينة ثم اضغط جلب.")

    if do_accept:
        snap = st.session_state.get("places_raw") or []
        if not snap:
            st.warning("لا توجد قائمة جاهزة — اضغط أولًا (جلب النتائج).")
        else:
            st.session_state["places_snapshot"] = snap
            st.session_state["places_references"] = references_from_places(snap)
            st.success(f"تم اعتماد {len(snap)} عنصرًا. يمكنك الآن الانتقال لتبويب (توليد المقال).")
            st.markdown("**تنبيه:** هذه القائمة ستُستخدم كوقود حقائق للمقال، مع ذكر ساعات الخميس فقط.")

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
        country = st.selectbox("الدولة", ["السعودية", "الإمارات", "أخرى…"], index=0)
        if country in COUNTRIES:
            city_choice = st.selectbox("المدينة", COUNTRIES[country] + ["مدينة مخصّصة…"], index=0)
            city_input = st.text_input("أدخل اسم المدينة", city_choice) if city_choice == "مدينة مخصّصة…" else city_choice
        else:
            country = st.text_input("اسم الدولة", "السعودية")
            city_input = st.text_input("المدينة", "الرياض")

        place_type = st.selectbox("نوع المكان",
            ["مول/مجمع", "جهة من المدينة (شمال/شرق..)", "حيّ محدد", "شارع/ممشى", "واجهة بحرية/كورنيش", "فندق/منتجع", "مدينة كاملة"], index=0)
        place_name = st.text_input("اسم المكان/النطاق", placeholder="مثلًا: دبي مول / شمال الرياض")
        place_rules = st.text_area("قواعد النطاق (اختياري)", placeholder="داخل المول فقط، أو الأحياء: الربيع/الياسمين/المروج…", height=80)
        strict_in_scope = st.checkbox("التزم بالنطاق الجغرافي فقط (صارم)", value=True)

        content_scope = st.radio("نطاق المحتوى", ["فئة محددة داخل المكان", "شامل بلا فئة", "هجين (تقسيم داخلي)"], index=1 if place_type=="مول/مجمع" else 0)

        built_in_labels = list(CRITERIA_MAP.keys())
        category = "عام"
        criteria_block = GENERAL_CRITERIA

        # اختيار الفئة
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
                ta_kwargs = dict(key="custom_criteria_text", height=140)
                if "custom_criteria_text" not in st.session_state:
                    ta_kwargs["value"] = DEFAULT_CRIT_MD
                custom_criteria_text = st.text_area("معايير الاختيار لهذه الفئة (اختياري)", **ta_kwargs)
                category = (st.session_state.get("custom_category_name") or "فئة مخصّصة").strip()
                criteria_block = st.session_state.get("custom_criteria_text") or "اعتمدنا على التجربة، جودة المكونات، تنوع القائمة، وثبات الجودة."
                is_custom_category = True
            else:
                category = category_choice
                criteria_block = CRITERIA_MAP.get(category_choice, GENERAL_CRITERIA)
        else:
            category = "عام"
            criteria_block = GENERAL_CRITERIA

        # زر/خيار جلب/توليد معايير الفئة
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
            if st.button("جلب/توليد معايير الفئة", key="btn_generate_criteria"):
                crit_list = get_category_criteria(effective_category, use_llm=use_llm, catalog_path="data/criteria_catalog.yaml")
                md = _format_criteria_md(crit_list)
                st.session_state["criteria_generated_md_map"].pop(effective_category, None)
                st.session_state["criteria_generated_md_map"][effective_category] = md
                if is_custom_category:
                    st.session_state["pending_custom_criteria_text"] = md
                    safe_rerun()
                else:
                    st.success("تم توليد المعايير وحفظها.")
            if effective_category in st.session_state["criteria_generated_md_map"]:
                st.markdown("**المعايير (تلقائي):**")
                st.markdown(st.session_state["criteria_generated_md_map"][effective_category])

        if is_custom_category:
            criteria_block = st.session_state.get("custom_criteria_text", criteria_block)
        else:
            criteria_block = st.session_state.get("criteria_generated_md_map", {}).get(effective_category, criteria_block)

        restaurants_input = st.text_area("أدخل أسماء المطاعم (سطر لكل مطعم)", "مطعم 1\nمطعم 2\nمطعم 3", height=160)
        st.markdown("**أو** ارفع ملف CSV بأسماء المطاعم (عمود: name)")
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

        manual_notes = st.text_area("ملاحظات يدوية تُدمج داخل التجارب (اختياري)", st.session_state.get("comp_gap_notes",""))

    with col2:
        st.subheader("قائمة تدقيق بشرية")
        checks = {
            "sensory": st.checkbox("أضف وصفًا حسيًا دقيقًا (رائحة/قوام/حرارة) لمطعم واحد على الأقل"),
            "personal": st.checkbox("أدرج ملاحظة شخصية/تفضيل شخصي"),
            "compare": st.checkbox("أضف مقارنة صغيرة مع زيارة سابقة/مطعم مشابه"),
            "critique": st.checkbox("أضف نقدًا غير متوقع (تفصيلة سلبية صغيرة)"),
            "vary": st.checkbox("نوّع أطوال الفقرات لتجنب الرتابة"),
        }

    # ---------- دمج Snapshot من Google Places ----------
    places_snapshot = st.session_state.get("places_snapshot") or []
    use_snapshot = False
    if places_snapshot:
        use_snapshot = st.checkbox("استخدام قائمة Google Places المعتمدة في هذا المقال", value=True,
                                   help="سيتم تمرير حقائق مختصرة (تشمل ساعات الخميس) إلى البرومبت + دمج مراجع Google تلقائيًا.")

    # دمج المراجع: مراجع snapshot + المراجع اليدوية
    snapshot_refs = st.session_state.get("places_references") or []
    manual_refs = normalize_refs(refs_text)
    combined_refs = []
    for u in snapshot_refs + manual_refs:
        if u and u not in combined_refs:
            combined_refs.append(u)
    references_block_combined = build_references_md(combined_refs) if combined_refs else "—"
    citation_map = build_citation_map(combined_refs)

    # حقائق مختصرة لتمريرها للبرومبت (ساعات الخميس ثابتة)
    facts_block = facts_markdown(places_snapshot) if (places_snapshot and use_snapshot) else "—"

    # ---------- توليد المقال ----------
    if st.button("🚀 توليد المقال"):
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
            st.stop()
        client = get_client()

        # إعداد النبرة
        if tone == "ناقد صارم | مراجعات الجمهور":
            tone_instructions = ("اكتب كنّاقد صارم يعتمد أساسًا على مراجعات العملاء المنشورة علنًا. "
                                 "ركّز على الأنماط المتكررة واذكر حدود المنهجية. لا تدّعِ زيارة شخصية. لا تستخدم أرقام.")
            tone_selection_line = "اعتمدنا على مراجعات موثوقة منشورة علنًا حتى {last_updated}، مع التركيز على الأنماط المتكررة."
            system_tone = "أسلوب ناقد صارم مرتكز على مراجعات الجمهور"
        elif tone == "ناقد صارم | تجربة مباشرة + مراجعات":
            tone_instructions = ("اكتب كنّاقد صارم يمزج خبرة ميدانية مع مراجعات الجمهور. "
                                 "قدّم الحكم من التجربة المباشرة أولًا ثم قارنه بانطباعات الجمهور. أدرج **نقطة للتحسين** لكل مطعم.")
            tone_selection_line = "مزجنا بين زيارات ميدانية وتجارب فعلية ومراجعات عامة حتى {last_updated}."
            system_tone = "أسلوب ناقد صارم يمزج التجربة المباشرة مع مراجعات الجمهور"
        else:
            tone_instructions = "اكتب بأسلوب متوازن يراعي الدقة والوضوح دون مبالغة."
            tone_selection_line = "اعتمدنا على التجربة المباشرة ومعلومات موثوقة متاحة، مع مراجعة دورية."
            system_tone = tone

        # نطاق المحتوى
        if content_scope == "فئة محددة داخل المكان":
            scope_instructions = "التزم بالفئة المحددة فقط داخل النطاق الجغرافي."
        elif content_scope == "هجين (تقسيم داخلي)":
            scope_instructions = "قسّم المطاعم إلى أقسام منطقية ووازن التنوع."
        else:
            scope_instructions = "قدّم تشكيلة متنوعة تمثّل المكان."

        protip_hint = build_protip_hint(place_type)
        place_context = build_place_context(place_type, place_name, place_rules, strict_in_scope)

        # FAQ مدعوم بالمصادر (اختياري)
        if include_faq:
            faq_prompt = (
                "اكتب 5–8 أسئلة شائعة وإجابات قصيرة بالعربية.\n"
                "إذا احتجت الاستشهاد بمصدر، استخدم حاشية [^ن] برقم مرجعي من قسم \"المراجع\" أدناه.\n"
                f"المدينة/المكان: {place_name or city_input}\n"
                f"الفئة: {category}\n"
                "موضوعات مقترحة: الحجز/الذروة/الجلسات الخارجية/العائلات/اللباس/المواقف/طرق الدفع.\n"
                "لا تختلق مصادر. إن كانت المعلومة من خبرة تحريرية، اذكر (خبرة تحريرية) بدل الحاشية.\n"
                "(قائمة المراجع ستظهر بعد المقال؛ رقّم الحواشي فقط دون إعادة الروابط.)"
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
        else:
            faq_block = "—"

        # منهجية
        last_updated = datetime.now().strftime("%B %Y")
        methodology_block = METH_TMPL.format(last_updated=last_updated) if include_methodology else "—"

        # كتلة الكلمات الإلزامية داخل البرومبت
        req_md = "\n".join([f"- **{kw}** — حد أدنى: {need} مرّة" for kw, need in required_list]) if required_list else "—"

        # المراجع — دمج snapshot + اليدوي (أُعدّت بالأعلى)
        references_block = references_block_combined

        # بناء البرومبت الأساسي
        base_prompt = BASE_TMPL.format(
            title=article_title, keyword=keyword, content_scope=content_scope, category=category,
            restaurants_list=", ".join(restaurants), criteria_block=criteria_block, faq_block=faq_block,
            methodology_block=methodology_block, tone_label=tone, place_context=place_context,
            protip_hint=protip_hint, scope_instructions=scope_instructions, tone_instructions=tone_instructions,
            tone_selection_line=tone_selection_line.replace("{last_updated}", last_updated),
            required_keywords_block=req_md, approx_len=approx_len,
            references_block=references_block
        )
        # ألحق حقائق Google المختصرة (ساعات الخميس) كي يسترشد بها الموديل — لا تُطبع كما هي
        if use_snapshot and places_snapshot:
            base_prompt += "\n\n## بيانات Google (مختصرة — لا تُطبع كما هي)\n"
            base_prompt += facts_block

        base_messages = [
            {"role": "system",
             "content": (
                 f"اكتب بالعربية الفصحى. {system_tone}. طول تقريبي {approx_len} كلمة."
                 " التزم بالنطاق ولا تختلق حقائق أو مصادر. إن لم تكن متأكدًا من معلومة خارجية، لا تذكرها."
                 " عند الاستشهاد بمصدر خارجي استخدم حاشية [^n] فقط، دون توليد روابط داخل المتن."
             )},
            {"role": "user", "content": base_prompt},
        ]

        # توليد المقال
        try:
            article_md = chat_complete_cached(
                client, base_messages,
                max_tokens=2200, temperature=0.7,
                model=primary_model, fallback_model=fallback_model,
                cacher=st.session_state["llm_cacher"],
                cache_extra={"task":"article_base", "required": required_list, "use_snapshot": use_snapshot}
            )
        except Exception as e:
            st.error(f"فشل التوليد: {e}")
            st.stop()

        # طبقة اللمسات البشرية (اختياري)
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

        # فحص الكلمات الإلزامية + إصلاح تلقائي عند الحاجة
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
                if st.button("✍️ إدماج تلقائي للكلمات الناقصة (دون حشو)"):
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
                            cache_extra={"task":"kw_fix", "needs": kw_report["missing"]}
                        )
                        kw_report = enforce_report(article_md, required_list)
                        st.success("تم إدماج الكلمات المطلوبة.")
                        st.markdown("\n".join(
                            [f"- {'✅' if it['ok'] else '❌'} **{it['keyword']}** — مطلوب {it['min']}, وُجدت {it['found']}"
                             for it in kw_report["items"]]
                        ))
                    except Exception as e:
                        st.warning(f"تعذّر الإدماج التلقائي: {e}")

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

        # روابط داخلية مقترحة
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

        # عرض المقال والنواتج
        st.subheader("📄 المقال الناتج")
        st.markdown(article_md)
        st.session_state['last_article_md'] = article_md

        st.subheader("🔎 Meta (SEO)"); st.code(meta_out, language="text")
        st.subheader("🔗 روابط داخلية مقترحة"); st.markdown(links_out)

        # ===== JSON-LD (Article + FAQPage) =====
        jsonld = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Article",
                    "headline": article_title,
                    "inLanguage": "ar",
                    "keywords": [keyword] if keyword else [],
                    "genre": ["دليل مطاعم", "مراجعات"],
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

        # محاولة استخراج Q/A للـFAQPage
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
        st.session_state["jsonld_str"] = jsonld_str  # مهم: للتبويب الخاص بالنشر
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
        st.session_state['generated_title'] = article_title  # للاستخدام في تبويب النشر

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
    st.subheader("تحليل أول منافسين — روابط يدوية (بدون API)")
    st.markdown("أدخل رابطين للصفحات المتصدّرة. سنجلب المحتوى ونحلّله من زاوية المحتوى وE-E-A-T فقط.")
    query = st.text_input("استعلام البحث", "أفضل مطاعم دبي مول")
    place_scope_desc = st.text_input("وصف النطاق/المكان (اختياري)", "داخل دبي مول فقط")
    url_a = st.text_input("رابط المنافس A", "")
    url_b = st.text_input("رابط المنافس B", "")

    tone_for_analysis = st.selectbox("نبرة التحليل",
        ["ناقد صارم | مراجعات الجمهور", "ناقد صارم | تجربة مباشرة + مراجعات", "دليل تحريري محايد"], index=0)
    reviews_weight_analysis = st.slider("وزن الاعتماد على المراجعات (٪) في التحليل", 0, 100, 60, step=5)

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
                st.success("تم الجلب والتهيئة.")
                st.write("**A:**", page_a.get("title") or url_a, f"({page_a['word_count']} كلمة)")
                st.write("**B:**", page_b.get("title") or url_b, f"({page_b['word_count']} كلمة)")
                st.caption("يمكنك الآن الضغط على زر تنفيذ التحليل.")
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
                    st.text_area("التوصيات المستخرجة (قابلة للتحرير قبل الحقن)", gaps, key="comp_gap_notes", height=160)
                else:
                    st.warning("لم يتم العثور على قسم 'Gap-to-Win'. انسخه يدويًا.")
            except Exception as e:
                st.error(f"تعذّر التحليل: {e}")

# ------------------ Tab 3: QC ------------------
with tab_qc:
    st.subheader("🧪 فحص بشرية وجودة المحتوى")
    qc_text = st.text_area("الصق نص المقال هنا", st.session_state.get("last_article_md",""), height=300)
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
                st.write(f"- جُمل: **{rep['sentence_count']}**")
                st.write(f"- فقرات: **{rep['paragraph_count']}**")
            with colB:
                st.write(f"- متوسط طول الجملة: **{rep['avg_sentence_length']}**")
                st.write(f"- متوسط طول الفقرة: **{rep['paragraph_metrics']['avg_len']}** ± {rep['paragraph_metrics']['std_len']}")
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
                if reps:
                    for g, c in reps:
                        st.write(f"- `{g}` × {c}")
                else:
                    st.caption("لا يوجد تكرار مزعج ملحوظ.")

                st.markdown("#### عبارات قالبية مرصودة")
                boiler = rep.get("boilerplate_flags") or []
                if boiler:
                    for f in boiler:
                        st.write(f"- **نمط:** `{f['pattern']}` — …{f['excerpt']}…")
                else:
                    st.caption("لا توجد عبارات قالبية ظاهرة.")

            st.markdown("#### الميل العاطفي")
            st.json(rep["sentiment"])

            if do_eeat:
                st.markdown("#### عناوين وأقسام")
                st.json(rep["headings"])

            st.markdown("#### توصيات ذكية")
            for tip in rep["tips"]:
                st.write(f"- {tip}")

            st.success("انتهى التحليل السريع.")
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
    st.markdown("#### إصلاح ذكي للأجزاء المعلّمة")
    flagged_block = st.text_area("ألصق الأسطر التي تريد تحسينها (سطر لكل مقطع)", height=140, placeholder="انسخ المقاطع الضعيفة وضعها هنا…")
    if st.button("✍️ أعِد الصياغة للمقاطع المحددة فقط"):
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

# ------------------ Tab 4: Publish to WordPress ------------------
with tab_wp:
    st.subheader("📝 النشر على ووردبريس (Draft/Publish)")
    with st.expander("إعدادات ووردبريس (من secrets.toml)", expanded=False):
        st.code(f"WP_BASE_URL={WP_BASE_URL}\nWP_USERNAME={WP_USERNAME}\n(كلمة التطبيق مخفية)")

    publishable = bool(st.session_state.get("last_article_md"))
    if not publishable:
        st.info("لا يوجد محتوى منشأ بعد. أنشئ المقال من تبويب ✍️ أولًا.")
    else:
        article_title_wp = st.text_input("عنوان ووردبريس", value=st.session_state.get("generated_title") or "مسودة: مقال مطاعم")
        wp_status = st.selectbox("حالة النشر", ["draft", "publish"], index=0)
        city_cat = st.text_input("تصنيف المدينة (Category)", value=st.session_state.get("city_for_wp") or "الرياض")
        type_cat = st.text_input("تصنيف الفئة (Category)", value=st.session_state.get("type_for_wp") or "برجر")
        extra_tags = st.text_input("وسوم (Tags) مفصولة بفواصل", value="مطاعم, عائلات, جلسات خارجية")

        add_jsonld = st.checkbox("إرفاق JSON-LD داخل المحتوى", value=True, help="قد يتطلب صلاحية unfiltered_html")
        add_snapshot_meta = st.checkbox("حفظ places_snapshot كـ meta (إن سمح الخادم) + تعليق مخفي داخل المحتوى", value=True)

        st.markdown("#### معاينة مختصرة")
        st.caption("المحتوى سيُنشر كما هو (Markdown/HTML). يمكنك تنسيقه لاحقًا داخل محرّر ووردبريس.")
        st.text_area("نص المقال", value=st.session_state.get("last_article_md","")[:2000], height=180)

        if st.button("🚀 نشر/تحديث (Upsert)"):
            if not (WP_BASE_URL and WP_USERNAME and WP_APP_PASSWORD):
                st.error("إعدادات ووردبريس غير مكتملة في secrets.toml")
                st.stop()
            try:
                client = WPClient(WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD)
                # حضّر التصنيفات والوسوم
                cat_ids = []
                if city_cat.strip():
                    cid = client.ensure_category(city_cat.strip())
                    if cid: cat_ids.append(cid)
                if type_cat.strip():
                    tid = client.ensure_category(type_cat.strip())
                    if tid: cat_ids.append(tid)

                tag_ids = []
                for t in [x.strip() for x in extra_tags.split(",") if x.strip()]:
                    tg = client.ensure_tag(t)
                    if tg: tag_ids.append(tg)

                # المحتوى: المقال + JSON-LD + تعليق مخفي للبيانات
                article_md = st.session_state.get("last_article_md","")
                content_parts = [article_md]

                jsonld_str = st.session_state.get("jsonld_str")
                if add_jsonld and jsonld_str:
                    content_parts.append(f'<script type="application/ld+json">\n{jsonld_str}\n</script>')

                # snapshot كتعليق مخفي
                places_snapshot = st.session_state.get("places_snapshot", [])
                if add_snapshot_meta and places_snapshot:
                    try:
                        snap_txt = json.dumps(places_snapshot, ensure_ascii=False)
                    except Exception:
                        snap_txt = "[]"
                    content_parts.append(f"<!-- places_json:{snap_txt} -->")

                content_html = "\n\n".join(content_parts)

                # meta (قد تُرفض إن لم تُسجَّل مفاتيح meta في ووردبريس)
                meta = {}
                if add_snapshot_meta and places_snapshot:
                    try:
                        meta["places_json"] = places_snapshot
                    except Exception:
                        pass

                slug = slugify(article_title_wp)
                # excerpt صغير من Meta السابق إن توفر، وإلا من أول سطرين من المقال
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
                st.success(f"تم النشر/التحديث بنجاح. الرابط: {link}")
                st.write(resp)
            except Exception as e:
                st.error(f"فشل النشر: {e}")
