"""Place report ("справка по месту") as a 2-page A4 PDF via matplotlib PdfPages (zones/place/review API).

matplotlib (Agg) + bundled DejaVu Sans -> Cyrillic works without system fonts; no browser needed (fast, ~1-3 s).
"""
from __future__ import annotations

import datetime as dt
import io
import textwrap
from typing import Optional

from . import core, place

STATUS_RU = {"found": "найдено", "clean": "чисто", "no_observation": "нет наблюдения", "no_image": "нет снимка",
             "unreliable": "ненадёжно"}  # UI polish: same date rule as /api/calendar
ACCENT = "#ff6b4a"
INK = "#13263a"
MUTED = "#5b6b7c"


def _wrap(s: str, width: int = 105) -> str:
    return "\n".join(textwrap.fill(p, width) for p in s.split("\n"))


def artifacts_line(st: core.Store, rid: str, date: Optional[str], model: str, zone: Optional[dict]) -> Optional[str]:
    """Artefacts in API: one line about objects excluded as artifacts (seam / wake / ship) on the reference date; None if none."""
    if not date:
        return None
    try:
        n_scene = place.n_artifacts(st, rid, date, model)
    except (core.NotFound, core.BadRequest):
        return None
    if not n_scene:
        return None
    cell = (zone or {}).get("artifacts") or []
    kinds: dict[str, int] = {}
    for a in cell:
        k = core.artifact_ru(a.get("artifact"))
        kinds[k] = kinds.get(k, 0) + 1
    in_cell = (f"в этой ячейке — {len(cell)} ({', '.join(f'{k}: {v}' for k, v in kinds.items())})" if cell
               else "в этой ячейке — 0")
    return (f"Исключено как артефакты на {date}: {n_scene} объект(ов) на снимке, {in_cell}; вероятно шов детекторов "
            "Sentinel-2, кильватер или судно — не входят в индекс и зоны приоритета.")


def build_place_pdf(st: core.Store, rid: str, h3id: str, model: Optional[str] = None, date: Optional[str] = None,
                    version: str = "") -> bytes:
    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure
    from PIL import Image

    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    matplotlib.rcParams["pdf.fonttype"] = 42  # embed TrueType: Cyrillic is selectable/searchable

    info = place.place_info(st, rid, h3id, model)
    model = info["model"]
    hist = info["history"]
    # reference date for "why": explicit, else latest date with a finding, else latest observed
    ref = date or (info["summary"]["last_found"]) or next(
        (h["date"] for h in reversed(hist) if h["status"] in ("found", "clean")), hist[-1]["date"] if hist else None)
    zone = None
    if ref:
        try:
            zone = place.zone_info(st, rid, ref, model, h3id)
        except (core.NotFound, core.BadRequest):
            zone = None
    now = dt.datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d %H:%M UTC%z")
    lon, lat = info["lon"], info["lat"]

    buf = io.BytesIO()
    with PdfPages(buf, metadata={"Title": f"Справка по месту {rid} {h3id}", "Creator": "Макропластик",
                                 "CreationDate": now}) as pdf:
        # ------------------------------------------------ page 1
        fig = Figure(figsize=(8.27, 11.69))
        fig.text(0.06, 0.965, "Справка по месту — плавающий мусор по снимкам Sentinel-2", fontsize=14,
                 weight="bold", color=INK, va="top")
        fig.text(0.06, 0.94, f"{info.get('region_name') or rid}" + (f", {info['country']}" if info.get("country") else ""),
                 fontsize=11, color=INK, va="top")
        fig.text(0.06, 0.918, f"Ячейка H3 (res 8): {h3id}   ·   центр {abs(lat):.5f}°{'N' if lat >= 0 else 'S'}, "
                 f"{abs(lon):.5f}°{'E' if lon >= 0 else 'W'}  ({lat:.5f}, {lon:.5f})\n"
                 f"Модель: {info.get('model_name') or model} (порог {info['threshold']:.2f})",
                 fontsize=8.5, color=MUTED, va="top", linespacing=1.4)
        # KPI row
        s = info["summary"]
        kpis = [("дат снимков", f"{s['n_dates']}"), ("наблюдено", f"{s['n_observed']}"),
                ("с находками", f"{s['n_found']}"), ("пятен всего", f"{s['n_detections_total']}"),
                ("макс. индекс, ‰", "—" if s["max_index"] is None else f"{s['max_index']:.1f}")]
        if zone:
            kpis.append((f"топ на {ref}", "вне топа" if zone["rank"] is None else f"№ {zone['rank']}"))
        w = 0.88 / len(kpis)
        for i, (k, v) in enumerate(kpis):
            x = 0.06 + i * w
            fig.patches.append(matplotlib.patches.FancyBboxPatch(
                (x + 0.003, 0.828), w - 0.012, 0.058, boxstyle="round,pad=0.004", transform=fig.transFigure,
                facecolor="#eef3f8", edgecolor="#d5dee8", linewidth=0.6))
            fig.text(x + 0.012, 0.873, k, fontsize=7.5, color=MUTED, va="top")
            fig.text(x + 0.012, 0.852, v, fontsize=13, weight="bold", color=INK, va="top")
        # Artefacts in API: "why" text first - its height decides the size of the crops, so that nothing runs off the page
        why = (zone or {}).get("why") or {}
        f_lines = (why.get("formula") or place.FORMULA).split(";  ")  # full formula, one part per line
        body = why.get("formula_text") or place.FORMULA_TEXT
        if zone:
            terms = "; ".join(f"{t['label']}: ×{t['contribution']}" if t["name"] in ("agreement", "date_penalty")
                              else f"{t['label']}: {t['value']}" for t in zone["why"]["terms"])
            if zone["why"].get("score_terms") and zone["why"].get("score_mode") != "mult":  # legacy 'sub' shape
                terms += "; " + "; ".join(f"{t['label']}: {t['value']}" for t in zone["why"]["score_terms"]
                                          if t.get("kind") != "info")
            body += f"\n\nНа дату {ref}: {terms}; score = {zone['why']['score']:.1f}.\n{zone['why']['text']}"
            if zone.get("n_confirmed") is not None and "второй моделью" not in zone["why"]["text"]:
                body += (f"\nПодтверждено второй моделью: {zone['n_confirmed']} "
                         "(согласие моделей, не проверка на месте).")
        else:
            body += "\n\nНа выбранную дату у ячейки нет данных для расчёта приоритета."
        art_line = artifacts_line(st, rid, ref, model, zone)
        if art_line:
            body += "\n" + art_line
        body = _wrap(body, 112)
        line_h = 8 * 1.35 / 72 / 11.69  # 8 pt, linespacing 1.35, A4 height in inches
        text_h = 0.012 + 0.035 + 0.025 + 0.0135 * (len(f_lines) - 1) + 0.025 + (body.count("\n") + 1) * line_h
        foot = 0.085  # red footer note starts at 0.06 and goes down
        # crops: up to 4 dates, prefer dates with findings, then latest observed
        pick = [h for h in hist if h["status"] == "found"][-4:]
        rest = [h for h in reversed(hist) if h not in pick and h["status"] != "no_image"]
        pick = sorted(pick + rest[:max(0, 4 - len(pick))], key=lambda h: h["date"])
        n = max(len(pick), 1)
        cols = 2 if n > 1 else 1
        rows = (n + cols - 1) // cols
        size = 0.42 if rows == 1 else 0.36  # width fraction; images are square
        ah = size * 8.27 / 11.69
        top = 0.79
        room = top - foot - text_h - (rows - 1) * 0.03  # height left for the crops
        if rows * ah > room:  # shrink the crops, not the text
            ah = max(room / rows, 0.08)
            size = ah * 11.69 / 8.27
        for i, h in enumerate(pick):
            r, c = divmod(i, cols)
            try:
                png = place.crop_png(st, rid, h["date"], lon, lat, 1500, True, "rgb", model, 420, "auto", h3id)
                img = Image.open(io.BytesIO(png))
            except (core.NotFound, core.BadRequest, OSError):
                img = None
            x0 = 0.5 - cols * size / 2 - (cols - 1) * 0.01 + c * (size + 0.02)
            y0 = top - (r + 1) * ah - r * 0.03
            ax = fig.add_axes([x0, y0, size, ah])
            ax.set_xticks([]); ax.set_yticks([])
            if img is not None:
                ax.imshow(img)
            for sp in ax.spines.values():
                sp.set_edgecolor(ACCENT if h["status"] == "found" else "#c0c9d3")
                sp.set_linewidth(1.4 if h["status"] == "found" else 0.6)
            lab = f"{h['date']} — {STATUS_RU.get(h['status'], h['status'])}"
            if h.get("n_detections"):
                lab += f", пятен: {h['n_detections']}"
            if h.get("index") is not None:
                lab += f", индекс {h['index']:.1f} ‰"
            if size < 0.3:  # shrunk crops: date and status on two lines, so neighbouring titles do not overlap
                lab = lab.replace(" — ", "\n", 1)
            ax.set_title(lab, fontsize=8 if size >= 0.3 else 7, color=INK, pad=3)
        y = top - rows * ah - (rows - 1) * 0.03 - 0.012
        fig.text(0.06, y, "Контур — пятна модели (#ff6b4a), белый шестиугольник — ячейка H3, вырезка 1,5 × 1,5 км; "
                 "RGB по каналам B4/B3/B2 (10 м).", fontsize=7, color=MUTED, va="top")
        # formula + why
        y -= 0.035
        fig.text(0.06, y, "Почему место в списке обследования", fontsize=11, weight="bold", color=INK, va="top")
        fig.text(0.06, y - 0.025, "\n".join(f_lines), fontsize=8.5, family="DejaVu Sans Mono", color=INK, va="top",
                 linespacing=1.3)
        y -= 0.0135 * (len(f_lines) - 1)
        fig.text(0.06, y - 0.05, body, fontsize=8, color=INK, va="top", linespacing=1.35)
        fig.text(0.06, 0.06, _wrap("Индекс по снимку, не масса пластика; согласие моделей ≠ проверка на месте. "
                                    "Приоритет обследования — ранжирование по формуле, не измеренная экологическая "
                                    "опасность.", 100), fontsize=8, color=ACCENT, weight="bold", va="top")
        fig.text(0.94, 0.018, f"стр. 1/2 · сформировано {stamp}", fontsize=7, color=MUTED, ha="right")
        pdf.savefig(fig)

        # ------------------------------------------------ page 2
        fig = Figure(figsize=(8.27, 11.69))
        fig.text(0.06, 0.965, "История наблюдений ячейки (только реальные даты снимков)", fontsize=12, weight="bold",
                 color=INK, va="top")
        head = ["Дата", "Статус", "Индекс, ‰", "Пятен", "Макс. P", "Набл. вода", "Облачн.", "Другие модели", "Качество"]
        rows_t = []
        for h in hist:
            q = h.get("quality") or {}
            other = ", ".join(f"{m}: {v['n_detections']}" for m, v in (h.get("other_models") or {}).items()) or "—"
            rows_t.append([
                h["date"], STATUS_RU.get(h["status"], h["status"]),
                "—" if h.get("index") is None else f"{h['index']:.2f}",
                str(h.get("n_detections") or 0) + (f" ({h['n_confirmed']} подтв.)" if h.get("n_confirmed") else "")
                + (f" +{h['n_artifacts']} искл." if h.get("n_artifacts") else ""),
                "—" if h.get("max_prob") is None else f"{h['max_prob']:.2f}",
                "—" if h.get("observed_frac") is None else f"{h['observed_frac'] * 100:.0f} %",
                "—" if h.get("cloud_frac") is None else f"{h['cloud_frac'] * 100:.1f} %",
                other, "дымка/блик" if (q.get("haze") or q.get("glint_or_haze")) else "норма"])
        n_rows = len(rows_t)
        t_h = min(0.035 * (n_rows + 1), 0.55)
        ax = fig.add_axes([0.06, 0.935 - t_h, 0.88, t_h])
        ax.axis("off")
        tb = ax.table(cellText=rows_t or [["—"] * len(head)], colLabels=head, loc="upper left", cellLoc="center",
                      colWidths=[0.12, 0.13, 0.09, 0.12, 0.08, 0.1, 0.08, 0.16, 0.12])
        tb.auto_set_font_size(False)
        tb.set_fontsize(7.5)
        tb.scale(1, 1.25)
        for (r, c), cell in tb.get_celld().items():
            cell.set_edgecolor("#d5dee8")
            if r == 0:
                cell.set_facecolor("#eef3f8")
                cell.set_text_props(weight="bold", color=INK)
            elif c == 1 and rows_t and rows_t[r - 1][1] == "найдено":
                cell.set_text_props(color=ACCENT, weight="bold")
        y = 0.935 - t_h - 0.03
        fig.text(0.06, y, "Ограничения", fontsize=11, weight="bold", color=INK, va="top")
        lim = "\n".join("• " + textwrap.fill(x, 110, subsequent_indent="  ") for x in info["limitations"])
        fig.text(0.06, y - 0.025, lim, fontsize=8, color=INK, va="top", linespacing=1.4)
        y -= 0.025 + 0.0165 * (lim.count("\n") + 1) + 0.03
        fig.text(0.06, y, "Источники и лицензии", fontsize=11, weight="bold", color=INK, va="top")
        src = "\n".join("• " + textwrap.fill(f"{x.get('name')} — {x.get('url', '')} ({x.get('license', '')})", 110,
                                             subsequent_indent="  ") for x in info["sources"])
        scenes = [h for h in hist if h.get("scene_id")]
        if scenes:
            src += "\n• Сцены: " + textwrap.fill(", ".join(h["scene_id"] for h in scenes), 110,
                                                 subsequent_indent="  ")
        fig.text(0.06, y - 0.025, src, fontsize=7.5, color=INK, va="top", linespacing=1.4)
        fig.text(0.06, 0.045, _wrap(f"Сформировано {stamp} сервисом «Макропластик» {version}; "
                                    f"корень данных: {st.data_kind()}. Координаты для выезда — центр ячейки на стр. 1.", 118), fontsize=7, color=MUTED, va="top")
        fig.text(0.94, 0.018, "стр. 2/2", fontsize=7, color=MUTED, ha="right")
        pdf.savefig(fig)
    return buf.getvalue()
