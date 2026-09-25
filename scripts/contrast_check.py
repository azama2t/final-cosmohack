"""WCAG text contrast check for the v2 UI (frontend v2: text contrast ≥ 4.5:1).

Two checks:
  1) static — the palette of service/frontend_v2/src/styles.css (:root text colours vs surface colours);
  2) live — Playwright opens the running UI in several states and, for every visible element with its own text,
     takes the computed text colour and the first opaque background up the tree; text drawn straight over the map
     (no opaque background) is reported separately (it has a solid label background by design in v2).
     Large text (≥ 24 px, or ≥ 18.66 px bold) needs 3:1, everything else 4.5:1.

Usage:
  .venv\\Scripts\\python.exe scripts\\contrast_check.py --base-url http://127.0.0.1:8070 --out reports\\screens\\v2_final
Exit code 1 if any failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "service" / "frontend_v2" / "src" / "styles.css"


def lum(rgb: tuple[float, float, float]) -> float:
    def f(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b) -> float:
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def hex_rgb(h: str):
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def static_check() -> dict:
    css = CSS.read_text(encoding="utf-8")
    root = re.search(r":root\s*{([^}]*)}", css).group(1)
    var = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", root))
    texts = [k for k in ("text", "text-2", "text-3", "accent", "warn") if k in var]
    surfaces = [k for k in ("bg", "panel", "panel-2", "panel-3") if k in var]
    rows = []
    for t in texts:
        for s in surfaces:
            r = ratio(hex_rgb(var[t]), hex_rgb(var[s]))
            rows.append({"text": t, "surface": s, "ratio": round(r, 2), "ok": r >= 4.5})
    return {"rows": rows, "fails": [r for r in rows if not r["ok"]]}


JS = r"""
() => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const out = [];
  const els = document.querySelectorAll('body *');
  for (const el of els) {
    if (!(el instanceof HTMLElement)) continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    const col = parse(cs.color); if (!col) continue;
    let bg = null, n = el;
    while (n && n !== document.documentElement) {
      const b = parse(getComputedStyle(n).backgroundColor);
      if (b && b.a >= 0.8) { bg = b; break; }
      n = n.parentElement;
    }
    const opacityChain = (() => { let o = 1, x = el; while (x) { o *= +getComputedStyle(x).opacity; x = x.parentElement; } return o; })();
    out.push({ text: el.textContent.trim().slice(0, 60), tag: el.tagName, cls: String(el.className).slice(0, 40),
      color: [col.r, col.g, col.b, col.a * opacityChain], bg: bg ? [bg.r, bg.g, bg.b] : null,
      size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight) || 400, disabled: !!el.closest('button:disabled') });
  }
  return out;
}
"""


def blend(fg, bg):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))


def live_check(base: str, out: Path) -> dict:
    states = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--use-angle=d3d11", "--ignore-gpu-blocklist"])
        pg = br.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto(base + "/", wait_until="domcontentloaded")
        pg.wait_for_function("window.__app && window.__app.sceneReady", timeout=30000)
        pg.wait_for_timeout(3500)
        seq = [("first_screen", None), ("evidence", "finding-row-0"), ("zones", "act-zones"), ("history", "act-history"),
               ("drift", "act-drift"), ("feed", "tab-feed"), ("settings", "layers-menu"), ("review", "act-review")]
        for name, testid in seq:
            if testid:
                if name == "zones" and pg.locator("[data-testid='panel-back']").count():
                    pg.locator("[data-testid='panel-back']").first.click()
                    pg.wait_for_timeout(400)
                loc = pg.locator(f"[data-testid='{testid}']")
                if not loc.count():
                    continue
                loc.first.click()
                pg.wait_for_timeout(2200)
            items = pg.evaluate(JS)
            for it in items:
                it["state"] = name
            states.extend(items)
            if name == "settings":
                pg.locator("[data-testid='layers-menu']").first.click()
        br.close()
    rows, fails, over_map = [], [], []
    for it in states:
        if it["bg"] is None:
            over_map.append({k: it[k] for k in ("state", "text", "cls")})
            continue
        fg = blend(it["color"], it["bg"])
        r = ratio(fg, it["bg"])
        large = it["size"] >= 24 or (it["size"] >= 18.66 and it["weight"] >= 700)
        need = 3.0 if large else 4.5
        row = {"state": it["state"], "text": it["text"], "tag": it["tag"], "cls": it["cls"], "ratio": round(r, 2), "need": need,
               "disabled": it["disabled"]}
        rows.append(row)
        # WCAG 1.4.3 exempts inactive (disabled) controls
        if r < need and not it["disabled"]:
            fails.append(row)
    res = {"n_checked": len(rows), "n_fail": len(fails), "min_ratio": min((r["ratio"] for r in rows), default=None),
           "fails": fails[:60], "text_over_map_without_background": over_map[:30]}
    (out / "contrast.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    ap.add_argument("--out", default="reports/screens/v2_contrast")
    a = ap.parse_args()
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    st = static_check()
    lv = live_check(a.base_url.rstrip("/"), out)
    print(json.dumps({"static_fails": st["fails"], "live": {k: lv[k] for k in ("n_checked", "n_fail", "min_ratio")},
                      "live_fails": lv["fails"][:15], "over_map": len(lv["text_over_map_without_background"])}, ensure_ascii=False, indent=1))
    sys.exit(1 if st["fails"] or lv["n_fail"] else 0)


if __name__ == "__main__":
    main()
