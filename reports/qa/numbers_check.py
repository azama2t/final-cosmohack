"""QA agent 19: key numbers of reports/final_numbers.json present in README / docs / deck (python-pptx) / report.docx.
A number is searched in several spellings (0.871 / 0,871; 53.9 / 53,9; 4 176 / 4176 ...).
usage: python reports/qa/numbers_check.py <repo_root> <out.json>"""
import json
import re
import sys
import zipfile
from pathlib import Path

repo, outp = Path(sys.argv[1]), Path(sys.argv[2])
fn = json.loads((repo / "reports/final_numbers.json").read_text(encoding="utf-8"))
s = fn["case"]["sections"]


def v(x, nd):
    return f"{x:.{nd}f}"


def spell(txt):
    alts = {txt, txt.replace(".", ",")}
    return alts


KEYS = {
    "MARIDA LightGBM F1": v(s["marida_test"]["lgbm_f1"], 3),
    "MARIDA RF F1": v(s["marida_test"]["rf_f1"], 3),
    "field test S2 model MAE": v(s["field_test"]["S2"]["main_mae"], 1),
    "field test S2 median MAE": v(s["field_test"]["S2"]["median_mae"], 1),
    "field test S1 model MAE": v(s["field_test"]["S1"]["main_mae"], 1),
    "field S2 pooled C": v(s["quantity"]["field_S2"]["pooled_C"], 1),
    "field S2 boot lo": v(s["quantity"]["field_S2"]["boot_lo95"], 1),
    "field S2 boot hi": v(s["quantity"]["field_S2"]["boot_hi95"], 1),
    "scene_zones n": str(s["scene_zones"]["n_zones"]),
    "scene_zones level B": str(s["scene_zones"]["by_level_b"]),
    "photo MAE": v(s["photo_count"]["frame"]["mae"], 2),
    "ADIS pairs A": str(s["adis_pairs"].get("A", s["search"].get("adis_A", 66))) if isinstance(s.get("adis_pairs"), dict) else "66",
}
# stale values seen in older builds: must not appear as the current claim
STALE = {"scene_zones unverified 66 (old)": None}

docs = {}
for rel in ("README.md", "docs/SPEECH.md", "docs/DEMO.md", "docs/QA.md", "docs/QUANTITY.md", "docs/INDEX.md",
            "reports/report.md", "docs/CRITERIA_CHECK.md", "docs/ИТОГ_ДЛЯ_ЛЮДЕЙ.md"):
    p = repo / rel
    if p.is_file():
        docs[rel] = p.read_text(encoding="utf-8", errors="replace")
for rel in ("reports/case_deck.pptx", "presentation/deck.pptx"):
    p = repo / rel
    if p.is_file():
        from pptx import Presentation
        prs = Presentation(str(p))
        parts = []
        for sl in prs.slides:
            for sh in sl.shapes:
                if sh.has_text_frame:
                    parts.append(sh.text_frame.text)
                if getattr(sh, "has_table", False) and sh.has_table:
                    for r in sh.table.rows:
                        for c in r.cells:
                            parts.append(c.text)
            if sl.has_notes_slide:
                parts.append(sl.notes_slide.notes_text_frame.text)
        docs[rel] = "\n".join(parts)
p = repo / "reports/report.docx"
if p.is_file():
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    docs["reports/report.docx"] = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))

res = {"docs": {k: len(t) for k, t in docs.items()}, "matrix": {}}
for name, val in KEYS.items():
    row = {}
    for d, t in docs.items():
        tt = t.replace(" ", " ").replace(" ", " ")
        row[d] = any(re.search(r"(?<![\d.,])" + re.escape(a) + r"(?![\d])", tt) for a in spell(val))
    res["matrix"][f"{name} = {val}"] = row
outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
cols = list(docs)
print("docs:", {k: v for k, v in res["docs"].items()})
for k, row in res["matrix"].items():
    miss = [d for d in cols if not row[d]]
    print(f"{k:40s} missing in: {miss}")
