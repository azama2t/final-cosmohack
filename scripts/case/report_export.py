r"""reports/report_final.md → reports/report.docx + reports/report.pdf (без pandoc и новых пакетов).

    .venv\Scripts\python.exe scripts\case\report_export.py

Markdown переводится в HTML простым конвертером (заголовки, абзацы, списки, таблицы, картинки как data-URI, **жирный**,
`код`, ссылки), затем LibreOffice headless (soffice) делает DOCX и PDF. Текст и числа — только из report_final.md,
который собирает scripts/render_docs.py из templates/reports/report_final.md.tmpl и reports/final_numbers.json.
"""
from __future__ import annotations

import base64
import html
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "reports" / "report_final.md"
OUT_DOCX = ROOT / "reports" / "report.docx"
OUT_PDF = ROOT / "reports" / "report.pdf"

CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 18pt; margin: 0 0 8pt 0; }
h2 { font-size: 14pt; margin: 14pt 0 6pt 0; border-bottom: 1px solid #999; }
h3 { font-size: 12pt; margin: 10pt 0 4pt 0; }
table { border-collapse: collapse; margin: 6pt 0; width: 100%; }
th, td { border: 1px solid #888; padding: 2pt 4pt; font-size: 9pt; vertical-align: top; }
th { background: #e8e8e8; }
img { max-width: 16cm; }
code { font-family: Consolas, monospace; font-size: 9pt; }
"""


def _img(src: str, alt: str, base: Path) -> str:
    p = (base / src).resolve()
    if not p.is_file():
        return f"<p><i>[нет картинки: {html.escape(src)}]</i></p>"
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return (f'<p style="text-align:center"><img src="data:{mime};base64,{data}" alt="{html.escape(alt)}" width="{860 if any(q in src for q in ('units_ladder', 'funnel', 'independent', 'search_', 'img/report/', 'prime/')) else 640}"/><br/>'
            f"<i>{html.escape(alt)}</i></p>")


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(md: str, base: Path) -> str:
    out, para, lst, tbl = [], [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if lst:
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in lst) + "</ul>")
            lst.clear()
        if tbl:
            rows = [r for r in tbl if not re.match(r"^\|\s*:?-{2,}", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            def cell(c):  # длинные пути и ключи переносятся (zero-width space после / . _)
                s = inline(c)
                if len(c) <= 25:
                    return s
                # перенос только в тексте, не внутри тегов (иначе ломается </b>, </code>)
                return "".join(p if p.startswith("<") else re.sub(r"(?<=[/._])(?=[A-Za-z0-9])", "​", p)
                               for p in re.split(r"(<[^>]+>)", s))
            fs = "8pt" if len(cells[0]) > 5 else "9pt"
            h = "<tr>" + "".join(f'<th bgcolor="#e8e8e8"><font style="font-size:{fs}">{cell(c)}</font></th>' for c in cells[0]) + "</tr>"
            b = "".join("<tr>" + "".join(f'<td valign="top"><font style="font-size:{fs}">{cell(c)}</font></td>' for c in r) + "</tr>"
                        for r in cells[1:])
            out.append(f'<table border="1" cellspacing="0" cellpadding="3" width="100%">{h}{b}</table>')
            tbl.clear()

    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush()
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", s.strip())
        if m:
            flush()
            out.append(_img(m.group(2), m.group(1), base))
            continue
        if s.strip() == "---":
            flush()
            out.append("<hr/>")
            continue
        if s.lstrip().startswith("|"):
            if (para or lst) and not tbl:
                flush()
            tbl.append(s.strip())
            continue
        if tbl:
            flush()
        m = re.match(r"^\s*[-*]\s+(.*)", s)
        if m:
            if para:
                flush()
            lst.append(m.group(1))
            continue
        if lst and s.startswith("  "):
            lst[-1] += " " + s.strip()
            continue
        para.append(s.strip())
    flush()
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not SRC.is_file():
        print(f"[report_export] нет {SRC.relative_to(ROOT)} — сначала scripts/render_docs.py")
        return 1
    body = md_to_html(SRC.read_text(encoding="utf-8"), SRC.parent)
    doc = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Отчёт</title><style>{CSS}</style></head><body>{body}</body></html>"
    so = shutil.which("soffice") or next((str(p) for p in (Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
                                                         Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe")) if p.exists()), None)
    if not so:
        print("[report_export] LibreOffice не найден")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        h = tmp / "report.html"
        h.write_text(doc, encoding="utf-8")
        prof = f"-env:UserInstallation=file:///{(tmp / 'lo').as_posix()}"
        for target, out in (("odt:writer8", "report.odt"),):
            subprocess.run([so, prof, "--headless", "--infilter=HTML (StarWriter)", "--convert-to", target, "--outdir", str(tmp), str(h)],
                           check=True, capture_output=True, timeout=300)
        odt = tmp / "report.odt"
        subprocess.run([so, prof, "--headless", "--convert-to", "docx:MS Word 2007 XML", "--outdir", str(tmp), str(odt)],
                       check=True, capture_output=True, timeout=300)
        subprocess.run([so, prof, "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(tmp), str(odt)],
                       check=True, capture_output=True, timeout=300)
        shutil.copyfile(tmp / "report.docx", OUT_DOCX)
        shutil.copyfile(tmp / "report.pdf", OUT_PDF)
    print(f"[report_export] -> {OUT_DOCX.relative_to(ROOT)}, {OUT_PDF.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
