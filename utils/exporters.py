# utils/exporters.py
from typing import Any
import os, json

def to_docx(article_text: str, filename: str) -> str:
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", filename)
    try:
        from docx import Document
        doc = Document()
        for line in (article_text or "").splitlines():
            doc.add_paragraph(line if line.strip() else "")
        doc.save(path)
        return path
    except Exception:
        # بديل txt إذا ما توفر python-docx
        alt = path.replace(".docx", ".txt")
        with open(alt, "w", encoding="utf-8") as f:
            f.write(article_text or "")
        return alt

def to_json(data: Any, filename: str) -> str:
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
