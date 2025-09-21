# ضمان مجلد التخزين
import os
os.makedirs("data", exist_ok=True)

# الاستيراد (حسب مكان الملف عندك)
try:
    from category_criteria import get_category_criteria
except ImportError:
    from modules.category_criteria import get_category_criteria  # لو نقلته داخل utils/modules

import io, csv, unicodedata, json
from datetime import datetime
from pathlib import Path

import streamlit as st

from utils.openai_client import get_client, chat_complete
from utils.exporters import to_docx, to_json
from utils.content_fetch import fetch_and_extract
from utils.competitor_analysis import analyze_competitors, extract_gap_points
from utils.quality_checks import quality_report
from utils.llm_reviewer import llm_review, llm_fix

# --- rerun آمن لنسخ ستريملت المختلفة ---
def safe_rerun():
    if getattr(st, "rerun", None):
        st.rerun()  # Streamlit >= 1.30
    else:
        st.experimental_rerun()  # الإصدارات الأقدم

st.set_page_config(page_title="مولد مقالات المطاعم (E-E-A-T)", page_icon="🍽️", layout="wide")
st.title("🍽️ مولد مقالات المطاعم — E-E-A-T + Human Touch + منافسين + فحص بشرية")

PROMPTS_DIR = Path("prompts")
def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")

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

def _has_api_key() -> bool:
    try:
        if hasattr(st, "secrets") and "OPENAI_API_KEY" in st.secrets and st.secrets["OPENAI_API_KEY"]:
            return True
    except Exception:
        pass
    return bool(os.getenv("OPENAI_API_KEY"))

def slugify(name: str) -> str:
    s = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c))
    import re as _re
    s = _re.sub(r'\W+', '_', s).strip('_').lower()
    return s or "custom"

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

# Sidebar
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

st.sidebar.markdown("---")
st.sidebar.subheader("🔗 روابط داخلية (اختياري)")
internal_catalog = st.sidebar.text_area(
    "أدخل عناوين/سلاگز مقالاتك (سطر لكل عنصر)",
    "أفضل مطاعم الرياض\nأفضل مطاعم إفطار في الرياض\nأفضل مطاعم بيتزا في جدة"
)

# Tabs
tab_article, tab_comp, tab_qc = st.tabs(["✍️ توليد المقال", "🆚 تحليل المنافسين (روابط يدوية)", "🧪 فحص بشرية وجودة المحتوى"])

# ------------------ Tab 1: Article Generation ------------------
with tab_article:
    col1, col2 = st.columns([2,1])
    with col1:
        article_title = st.text_input("عنوان المقال", "أفضل مطاعم في الرياض")
        keyword = st.text_input("الكلمة المفتاحية (اختياري)", "مطاعم في الرياض")

        COUNTRIES = {"السعودية": ["الرياض","جدة","مكة","المدينة المنورة","الدمام","الخبر","الظهران","الطائف","أبها","خميس مشيط","جازان","نجران","تبوك","بريدة","عنيزة","الهفوف","الأحساء","الجبيل","القطيف","ينبع","حائل"],
                     "الإمارات": ["دبي","أبوظبي","الشارقة","عجمان","رأس الخيمة","الفجيرة","أم القيوين","العين"]}
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

        # ---------------- تحديد الفئة وبناء النص الأولي + علامة هل هي مخصّصة ----------------
        is_custom_category = False
        if content_scope == "فئة محددة داخل المكان":
            category_choice = st.selectbox("الفئة", built_in_labels + ["فئة مخصّصة…"])

            if category_choice == "فئة مخصّصة…":
                # حقن القيمة المعلّقة (إن وُجدت) قبل إنشاء Text Area
                if "pending_custom_criteria_text" in st.session_state:
                    st.session_state["custom_criteria_text"] = st.session_state.pop("pending_custom_criteria_text")

                custom_category_name = st.text_input("اسم الفئة المخصّصة", "مطاعم لبنانية", key="custom_category_name")

                # لا نمرّر value إذا كان المفتاح موجودًا؛ فقط أول تشغيل
                DEFAULT_CRIT_MD = (
                    "- **التجربة المباشرة:** زيارات متعدّدة وتجربة أطباق أساسية ومعروفة في المطبخ.\n"
                    "- **المكوّنات:** جودة اللحوم/الأسماك/الأجبان والخضروات الطازجة.\n"
                    "- **طرق الطهي والأصالة:** التتبيل والتحمير/الشوي/الفرن ومدى اقتراب النكهة من الأصل.\n"
                    "- **الأجواء والملاءمة:** جلسات عائلية/أصدقاء، مستوى الضجيج وراحة الجلسات.\n"
                    "- **ثبات الجودة:** ملاحظة التماسك في الطعم والخدمة عبر زيارات وأوقات مختلفة."
                )
                ta_kwargs = dict(key="custom_criteria_text", height=140)
                if "custom_criteria_text" not in st.session_state:
                    ta_kwargs["value"] = DEFAULT_CRIT_MD

                custom_criteria_text = st.text_area(
                    "معايير الاختيار لهذه الفئة (يدوي — اختياري، سيتم استبدالها تلقائيًا عند الضغط على زر الجلب)",
                    **ta_kwargs
                )

                category = (st.session_state.get("custom_category_name") or "فئة مخصّصة").strip()
                criteria_block = st.session_state.get("custom_criteria_text") or "اعتمدنا على التجربة المباشرة، جودة المكونات، تنوع القائمة، وثبات الجودة."
                is_custom_category = True
            else:
                category = category_choice
                criteria_block = CRITERIA_MAP.get(category_choice, GENERAL_CRITERIA)
                is_custom_category = False
        else:
            category = "عام"
            criteria_block = GENERAL_CRITERIA
            is_custom_category = False
        # ---------------------------------------------------------------------

        # ---------- دوال تطبيع العرض + زر/خيار جلب/توليد معايير الفئة ----------
        def _normalize_criteria(raw):
            """حوّل أي ناتج (list/tuple/dict/str JSON) إلى قائمة نصوص نظيفة بلا undefined."""
            if raw is None:
                return []
            # لو نص قد يكون JSON
            if isinstance(raw, str):
                s = raw.strip()
                if s.startswith(("[", "{")):
                    try:
                        raw = json.loads(s)
                    except Exception:
                        lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
                        return [ln for ln in lines if ln and ln.lower() != "undefined"]
                else:
                    lines = [ln.strip(" -•\t").strip() for ln in s.splitlines() if ln.strip()]
                    return [ln for ln in lines if ln and ln.lower() != "undefined"]
            # لو dict: جرّب مفاتيح شائعة أو خذ القيم/المفاتيح
            if isinstance(raw, dict):
                for k in ("criteria", "bullets", "items", "list"):
                    if k in raw:
                        raw = raw[k]
                        break
                else:
                    vals = list(raw.values())
                    raw = vals if all(isinstance(v, str) for v in vals) else list(raw.keys())
            # اعتبرها قائمة
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
            items = _normalize_criteria(items)
            return "\n".join(f"- {c}" for c in items) or "- —"

        effective_category = (category or "عام").strip()
        if "criteria_generated_md_map" not in st.session_state:
            st.session_state["criteria_generated_md_map"] = {}

        with st.expander("📋 معايير الاختيار لهذه الفئة (تلقائي/يدوي)", expanded=False):
            st.caption(f"الفئة الحالية: **{effective_category}**")
            use_llm = st.checkbox("تعزيز بالـ LLM (اختياري)", value=False, key="crit_llm",
                                  help="يتطلب OPENAI_API_KEY إن فعّلته، وإلا تُستخدم Heuristics.")
            if st.button("جلب/توليد معايير الفئة", key="btn_generate_criteria"):
                crit_list = get_category_criteria(
                    effective_category,
                    use_llm=use_llm,
                    catalog_path="data/criteria_catalog.yaml"
                )
                md = _format_criteria_md(crit_list)
                # نظّف أي قيمة قديمة مخزنة
                st.session_state["criteria_generated_md_map"].pop(effective_category, None)
                st.session_state["criteria_generated_md_map"][effective_category] = md

                if is_custom_category:
                    # لا نلمس مفتاح الويجت مباشرة؛ نحفظ قيمة معلّقة ثم rerun
                    st.session_state["pending_custom_criteria_text"] = md
                    safe_rerun()
                else:
                    st.success("تم توليد المعايير وحفظها.")

            # (اختياري) عرض آخر توليد محفوظ لهذه الفئة
            if effective_category in st.session_state["criteria_generated_md_map"]:
                st.markdown("**المعايير (تلقائي):**")
                st.markdown(st.session_state["criteria_generated_md_map"][effective_category])
        # ---------- /انتهى ----------

        # مصدر criteria_block النهائي
        if is_custom_category:
            criteria_block = st.session_state.get("custom_criteria_text", criteria_block)
        else:
            criteria_block = st.session_state.get("criteria_generated_md_map", {}).get(effective_category, criteria_block)

        restaurants_input = st.text_area("أدخل أسماء المطاعم (سطر لكل مطعم)", "مطعم 1\nمطعم 2\nمطعم 3", height=160)
        st.markdown("**أو** ارفع ملف CSV بأسماء المطاعم (عمود: name)")
        csv_file = st.file_uploader("رفع CSV (اختياري)", type=["csv"], help="عمود name مطلوب؛ عمود note اختياري.")

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

    if st.button("🚀 توليد المقال"):
        if not _has_api_key():
            st.error("لا يوجد OPENAI_API_KEY.")
            st.stop()
        client = get_client()

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

        if content_scope == "فئة محددة داخل المكان":
            scope_instructions = "التزم بالفئة المحددة فقط داخل النطاق الجغرافي."
        elif content_scope == "هجين (تقسيم داخلي)":
            scope_instructions = "قسّم المطاعم إلى أقسام منطقية ووازن التنوع."
        else:
            scope_instructions = "قدّم تشكيلة متنوعة تمثّل المكان."

        protip_hint = build_protip_hint(place_type)
        place_context = build_place_context(place_type, place_name, place_rules, strict_in_scope)

        faq_block = FAQ_TMPL.format(category=category, city=place_name or city_input) if include_faq else "—"
        last_updated = datetime.now().strftime("%B %Y")
        methodology_block = METH_TMPL.format(last_updated=last_updated) if include_methodology else "—"

        base_prompt = BASE_TMPL.format(
            title=article_title, keyword=keyword, content_scope=content_scope, category=category,
            restaurants_list=", ".join(restaurants), criteria_block=criteria_block, faq_block=faq_block,
            methodology_block=methodology_block, tone_label=tone, place_context=place_context,
            protip_hint=protip_hint, scope_instructions=scope_instructions, tone_instructions=tone_instructions,
            tone_selection_line=tone_selection_line.replace("{last_updated}", last_updated)
        )
        base_messages = [
            {"role": "system", "content": f"اكتب المقال بالعربية الفصحى. {system_tone}. طول تقريبي {approx_len} كلمة."},
            {"role": "user", "content": base_prompt},
        ]
        try:
            article_md = chat_complete(client, base_messages, max_tokens=2200, temperature=0.7, model=primary_model, fallback_model=fallback_model)
        except Exception as e:
            st.error(f"فشل التوليد: {e}")
            st.stop()

        apply_polish = add_human_touch or any(checks.values())
        merged_user_notes = (st.session_state.get("comp_gap_notes","") + "\n" + (manual_notes or "")).strip()
        if apply_polish or merged_user_notes:
            polish_prompt = read_prompt("polish.md").format(article=article_md, user_notes=merged_user_notes)
            polish_messages = [
                {"role": "system", "content": "أنت محرر عربي محترف، تحافظ على الحقائق وتضيف لمسات بشرية بدون مبالغة."},
                {"role": "user", "content": polish_prompt},
            ]
            try:
                article_md = chat_complete(client, polish_messages, max_tokens=2400, temperature=0.8, model=primary_model, fallback_model=fallback_model)
            except Exception as e:
                st.warning(f"طبقة اللمسات البشرية تعذّرت: {e}")

        meta_prompt = f"صِغ عنوان SEO (≤ 60) ووصف ميتا (≤ 155) بالعربية لمقال بعنوان \"{article_title}\". الكلمة المفتاحية: {keyword}.\nTITLE: ...\nDESCRIPTION: ..."
        try:
            meta_out = chat_complete(client, [{"role":"system","content":"أنت مختص SEO عربي."},{"role":"user","content": meta_prompt}], max_tokens=200, temperature=0.6, model=primary_model, fallback_model=fallback_model)
        except Exception:
            meta_out = f"TITLE: {article_title}\nDESCRIPTION: دليل عملي عن {keyword}."

        links_catalog = [s.strip() for s in internal_catalog.splitlines() if s.strip()]
        links_prompt = f"اقترح 3 روابط داخلية مناسبة من هذه القائمة إن أمكن:\n{links_catalog}\nالعنوان: {article_title}\nالنطاق: {content_scope}\nالفئة: {category}\nالمدينة/المكان: {place_name or city_input}\nمقتطف:\n{article_md[:800]}\n- رابط داخلي مقترح: <النص>\n- رابط داخلي مقترح: <النص>\n- رابط داخلي مقترح: <النص>"
        try:
            links_out = chat_complete(client, [{"role":"system","content":"أنت محرر عربي يقترح روابط داخلية طبيعية."},{"role":"user","content": links_prompt}], max_tokens=240, temperature=0.5, model=primary_model, fallback_model=fallback_model)
        except Exception:
            links_out = "- رابط داخلي مقترح: أفضل مطاعم الرياض\n- رابط داخلي مقترح: دليل مطاعم العائلات في الرياض\n- رابط داخلي مقترح: مقارنة بين الأنماط"

        st.subheader("📄 المقال الناتج")
        st.markdown(article_md)
        st.session_state['last_article_md'] = article_md

        st.subheader("🔎 Meta (SEO)"); st.code(meta_out, language="text")
        st.subheader("🔗 روابط داخلية مقترحة"); st.markdown(links_out)

        json_obj = {"title": article_title, "keyword": keyword, "category": category,
            "country": country, "city": city_input, "place": {"type": place_type, "name": place_name, "rules": place_rules, "strict": strict_in_scope},
            "content_scope": content_scope, "restaurants": restaurants, "last_updated": datetime.now().strftime("%B %Y"),
            "tone": tone, "reviews_weight": review_weight, "models": {"primary": primary_model, "fallback": fallback_model},
            "include_faq": include_faq, "include_methodology": include_methodology,
            "article_markdown": article_md, "meta": meta_out, "internal_links": links_out}
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
                    analysis_md = analyze_competitors(client, primary_model, fallback_model, pages["A"], pages["B"], query, place_scope_desc or "—", tone_for_analysis, reviews_weight_analysis)
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
