from docx import Document
from docx.shared import Pt
import json

def to_docx(filename: str, title: str, draft: str, extras: dict=None) -> str:
    doc = Document()
    p = doc.add_paragraph()
    run = p.add_run(title or "Article")
    run.bold = True
    run.font.size = Pt(16)
    doc.add_paragraph(draft or "")
    if extras:
        doc.add_paragraph("\n---\nExtras:")
        doc.add_paragraph(json.dumps(extras, ensure_ascii=False, indent=2))
    doc.save(filename)
    return filename

def to_json(filename: str, payload: dict) -> str:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return filename
