# -*- coding: utf-8 -*-
"""
category_criteria.py
Drop-in helper to generate (and remember) selection criteria for any restaurant category.
- Looks up a local catalog (YAML if available, else JSON fallback).
- If the category is new, it builds specialized criteria via heuristics (and optionally LLM).
- Persists the generated criteria for next time.
Usage:
    from category_criteria import get_category_criteria
    criteria = get_category_criteria("شاورما", use_llm=False, catalog_path="data/criteria_catalog.yaml")
"""

from __future__ import annotations
import os, json, threading, re

# Optional YAML support
try:
    import yaml  # type: ignore
except Exception:
    yaml = None

_LOCK = threading.Lock()

DEFAULT_GENERIC = [
    "جودة الطعم وثباته عبر الزيارات",
    "نظافة التحضير وسلامة المكونات",
    "القيمة مقابل السعر وحجم الحصة",
    "سرعة التقديم واحترافية الخدمة",
    "ملاءمة للعوائل وراحة الجلسة",
    "توفّر الطبق المميز ووضوح المنيو"
]

ALIASES = {
    "pizza": ["pizza","بيتزا"],
    "burger": ["burger","برجر","برغر"],
    "mandy": ["mandy","mandi","مندي","مظبي","مضغوط"],
    "cafes": ["cafes","cافيه","كافيهات","مقاهي","كوفي","cafe"],
    "shawarma": ["shawarma","شاورما"],
    "breakfast": ["breakfast","فطور","برنش","brunch"],
    "seafood": ["seafood","بحرية","بحر","سمك","أسماك"],
    "grills": ["grills","مشويات","كباب","kebab","grill","شواء"],
    "dessert": ["dessert","حلويات","بوظة","آيس كريم","ice cream","سويت"],
    "pasta": ["pasta","باستا","مكرونة","italian","إيطالي"],
    "coffee": ["coffee","قهوة","اسبرسو","espresso"],
    "bakery": ["bakery","مخبوزات","مخبز","بريوش","كرواسون"],
    "steakhouse": ["steak","ستيك","لحم مشوي","steakhouse"],
    "mexican": ["mexican","تاكو","برريتو","tex-mex","مكسيكي"],
    "japanese": ["ياباني","sushi","سوشي","رامن","ramen","udon","أودون"],
    "chinese": ["صيني","chinese","dim sum","ديم سوم","نودلز"],
    "thai": ["تايلندي","thai","تام يام","باد تاي"],
    "lebanese": ["لبناني","lebanese","فتوش","تبولة","حمص","مشاوي لبنانية"],
    "turkish": ["تركي","turkish","دونر","كفتة","كنافة تركية"],
}

def _normalize(text: str) -> str:
    if not text: return ""
    t = str(text).strip().lower()
    t = re.sub(r'[\u064B-\u0652]', '', t)  # remove Arabic diacritics
    t = t.replace('ـ','').replace('أ','ا').replace('إ','ا').replace('آ','ا')
    t = t.replace('ى','ي').replace('ؤ','و').replace('ئ','ي').replace('ة','ه')
    t = re.sub(r'\s+', ' ', t)
    return t

def _canonicalize(cat: str) -> str:
    c = _normalize(cat)
    for canon, words in ALIASES.items():
        for w in words:
            if _normalize(w) == c:
                return canon
    return c

def _heuristics_for(cat: str) -> list[str]:
    c = _canonicalize(cat)
    if c in ("pizza",):
        return [
            "عجينة متوازنة (سماكة/قرمشة) وخَبز سليم",
            "صلصة بندورة متوازنة الحموضة",
            "جودة الجبن وتوزيع الإضافات",
            "تنوع الأصناف والأصالة/الابتكار",
            "ثبات الجودة في أوقات الذروة"
        ]
    if c in ("burger",):
        return [
            "جودة اللحم (طازج/درجة التسوية/العصارة)",
            "توازن الخبز/اللحم/الجبن/الصوص",
            "ثبات الحجم والجودة عبر الزيارات",
            "تنوع الخيارات (لحم/دجاج/نباتي)",
            "النظافة وسرعة التقديم"
        ]
    if c in ("mandy",):
        return [
            "نضج الأرز وتوازن البهارات",
            "طراوة اللحم/الدجاج ونكهة التدخين/التحمير",
            "سخاء الحصص وثبات الجودة",
            "نظافة التحضير وتقديم مناسب للعوائل",
            "القيمة مقابل السعر"
        ]
    if c in ("cafes","coffee"):
        return [
            "جودة البن والاستخلاص (إسبريسو/فلتر)",
            "اتساق الحليب للمشروبات بالحليب",
            "تنوع الحلى/المخبوزات ومطابقتها للقهوة",
            "راحة الجلسات وهدوء المكان",
            "سرعة الخدمة وثبات الطعم"
        ]
    if c in ("shawarma",):
        return [
            "جودة اللحم/الدجاج وتتبيل متوازن دون ملوحة مفرطة",
            "درجة الشواء والقرمشة دون جفاف",
            "توازن الخبز/الصلصة/المخللات مع الحشوة",
            "ثبات الحجم/الوزن عبر الطلبات",
            "نظافة التحضير وسرعة التقديم"
        ]
    if c in ("breakfast",):
        return [
            "تنوع الأطباق (بيض/بانكيك/فول/فلافل) وجودة التنفيذ",
            "جودة الخبز والمخبوزات المصاحبة",
            "قهوة/عصائر طازجة مكملة للفطور",
            "خيارات صحية/عائلية ومقاعد مريحة",
            "زمن الانتظار المناسب في الذروة"
        ]
    if c in ("seafood",):
        return [
            "طزاجة المأكولات البحرية وتنوّعها",
            "طرق الطهي دون إفراط في الزيت",
            "توازن التتبيلات والصلصات",
            "نظافة العرض والحفظ البارد",
            "قيمة جيدة للحصص"
        ]
    if c in ("grills",):
        return [
            "جودة اللحم/التتبيل ودرجة الشواء",
            "عصارة وتوازن النكهات دون احتراق",
            "تنوع الأصناف وتجانس الحصص",
            "نظافة التحضير وخدمة سريعة",
            "قيمة مقابل السعر وثبات الجودة"
        ]
    if c in ("dessert", "bakery"):
        return [
            "جودة المكوّنات (زبدة/شوكولاتة/حليب) وطزاجتها",
            "قوام/نعومة/توازن السكر",
            "تنوع الخيارات وابتكار النكهات",
            "ثبات الجودة وتقديم جذاب",
            "قيمة مقابل السعر"
        ]
    if c in ("pasta", "italian"):
        return [
            "سلق الباستا بدرجة مناسبة (ألدنتي)",
            "توازن الصلصات (حمض/ملح/دهن) وجودة المكوّنات",
            "تنوع الأصناف الكلاسيكية والبيتية",
            "ثبات النتيجة عبر الطلبات",
            "قيمة مقابل السعر"
        ]
    if c in ("japanese",):
        return [
            "طزاجة الأسماك وسلامة السلسلة الباردة",
            "تقنيات اللفّ/التقطيع وتوازن الأرز/الخل",
            "تنوع النيغيري/الماكي/الساشيمي",
            "نظافة عالية ومعايير سلامة واضحة",
            "قيمة مقابل السعر"
        ]
    if c in ("chinese",):
        return [
            "توازن الصلصات ودرجة الووك (Wok Hei)",
            "تنوع الأطباق بين نودلز/أرز/دمبلنغ",
            "قوام الخضار واللحم دون مبالغة بالزيت",
            "ثبات الجودة في الذروة",
            "قيمة مقابل السعر"
        ]
    if c in ("thai",):
        return [
            "اتزان الحلو/الحار/الحامض/المالح",
            "طزاجة الأعشاب (ليمون جراس/كافر لايم/ريحان)",
            "تنوع الكاري/النودلز/الأرز",
            "قوام حليب جوز الهند دون انفصال دهني",
            "قيمة مقابل السعر"
        ]
    if c in ("lebanese",):
        return [
            "جودة المشاوي وتوازن التتبيل",
            "طزاجة السلطات (تبولة/فتوش) وتوازن الحموضة",
            "حمص/متبل بقوام ونكهة متسقة",
            "تنوع المقبلات وسخاء الحصص",
            "خدمة سريعة ونظافة"
        ]
    if c in ("turkish",):
        return [
            "جودة اللحوم (دونر/كفتة) وخبز طازج",
            "توازن التوابل والصلصات",
            "تنوع الأطباق (بيـده/اسكندر/كوكورج)",
            "حلويات تركية متقنة (بقلاوة/كونافة)",
            "قيمة مقابل السعر"
        ]
    if c in ("steakhouse",):
        return [
            "جودة القطع ودرجة النضج بدقة",
            "قشرة خارجية (Maillard) دون احتراق",
            "اختيارات الصلصات/الجانبيات المناسبة",
            "ثبات الجودة وخدمة خبيرة بالاستواء",
            "قيمة مقابل السعر"
        ]
    if c in ("mexican",):
        return [
            "جودة التورتيا والصلصات (سالسا/غواكامولي)",
            "توازن الحشوات (بروتين/خضار/جبن)",
            "تنوع الأطباق (تاكو/بوريتو/فاهيتا)",
            "مستوى حار مضبوط قابل للتخصيص",
            "قيمة مقابل السعر"
        ]
    # Fallback
    return DEFAULT_GENERIC

def _load_catalog(path: str) -> dict:
    if yaml and path.lower().endswith(".yaml"):
        if not os.path.exists(path): return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # JSON fallback (if YAML not available)
    if not os.path.exists(path):
        # allow ".json" or default to create json aside
        alt = path if path.lower().endswith(".json") else (os.path.splitext(path)[0] + ".json")
        if not os.path.exists(alt): return {}
        path = alt
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_catalog(path: str, data: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if yaml and path.lower().endswith(".yaml"):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)
        os.replace(tmp, path)
        return
    # JSON fallback
    if not path.lower().endswith(".json"):
        path = os.path.splitext(path)[0] + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_category_criteria(category: str, use_llm: bool = False, catalog_path: str = "data/criteria_catalog.yaml") -> list[str]:
    """
    Returns a list of 5-7 selection criteria for the given category.
    - Looks up a local catalog first.
    - If not found, generates via heuristics (and optionally LLM), then persists.
    """
    key = _canonicalize(category)
    with _LOCK:
        cat = _load_catalog(catalog_path)

        # direct hit
        if key in cat and isinstance(cat[key], list) and cat[key]:
            return cat[key]

        # generate
        crit = _heuristics_for(key)

        if use_llm:
            try:
                from openai import OpenAI  # requires openai>=1.x
                client = OpenAI()  # expects OPENAI_API_KEY env var
                prompt = f"""
اكتب 5-7 معايير تقييم متخصصة لفئة مطاعم "{category}" بالعربية الفصحى.
- كن محدداً (مؤشرات قابلة للملاحظة/القياس).
- لا تذكر الأسعار إلا كمعيار عام للقيمة.
- تجنّب العبارات العامة غير المقاسة.
أعد النتيجة في JSON Array من السلاسل.
"""
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"user","content":prompt}],
                    temperature=0.4,
                )
                txt = resp.choices[0].message.content.strip()
                # parse best-effort
                gen = []
                if txt.startswith("["):
                    import json as _json
                    gen = _json.loads(txt)
                else:
                    gen = [x.strip("-• ").strip() for x in txt.splitlines() if x.strip()]
                # merge + dedupe
                merged, seen = [], set()
                for item in (gen + crit):
                    k = item.strip().lower()
                    if k and k not in seen:
                        merged.append(item.strip())
                        seen.add(k)
                crit = merged[:7]
            except Exception:
                # ignore LLM errors; keep heuristics
                pass

        # persist
        cat[key] = crit
        _save_catalog(catalog_path, cat)
        return crit
