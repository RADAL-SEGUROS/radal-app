#!/usr/bin/env python3
"""Render the social-preview card (og:image) to frontend/public/brand/og-image.png.

WhatsApp and Discord will not render an SVG in a link preview, so the brand marks
in public/brand/ cannot be used directly — this script bakes a 1200x630 PNG card
in the Signal palette instead. Re-run it whenever the wordmark or tagline changes:

    backend/.venv/bin/python frontend/scripts/generate-og-image.py

It reuses the Playwright/Chromium already installed for the expedient PDFs, so it
needs no extra dependency. Network access is required once, for Google Fonts.
"""

from __future__ import annotations

import base64
import pathlib

from playwright.sync_api import sync_playwright

FRONTEND = pathlib.Path(__file__).resolve().parent.parent
BRAND = FRONTEND / "public" / "brand"

# Signal dark-surface tokens (frontend/src/index.css). The card is dark on purpose:
# both WhatsApp and Discord default to a dark chat ground.
PAPER = "#0e0f10"
BRAND_PINE = "#1fa08f"  # --brand on dark
INK = "#f2f3f4"
INK_3 = "#8f949a"

HEADLINE = "Conecta la industria del seguro"
TAGLINE = "Plataforma operativa para corredoras de seguros."
JOURNEY = "Antecedentes · Bases Técnicas · Comparación · Propuesta · Pólizas"


def card_html() -> str:
    mark = (BRAND / "radal-mark-white.svg").read_bytes()
    mark_uri = "data:image/svg+xml;base64," + base64.b64encode(mark).decode()
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap" rel="stylesheet" />
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1200px; height: 630px; overflow: hidden;
    background: {PAPER};
    font-family: Inter, system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .card {{ position: relative; width: 100%; height: 100%; padding: 84px 88px; display: flex; flex-direction: column; }}
  /* Soft pine glow, bottom-right — the only ornament. */
  .glow {{
    position: absolute; right: -180px; bottom: -260px; width: 760px; height: 760px;
    background: radial-gradient(circle, rgba(31,160,143,0.30) 0%, rgba(31,160,143,0.10) 42%, rgba(31,160,143,0) 70%);
  }}
  .rule {{ position: absolute; top: 0; left: 0; right: 0; height: 6px; background: {BRAND_PINE}; }}
  .lockup {{ display: flex; align-items: center; gap: 22px; position: relative; }}
  .lockup img {{ width: 76px; height: 76px; }}
  .wordmark {{ font-family: "Space Grotesk", Inter, sans-serif; font-weight: 600; font-size: 62px; color: {INK}; letter-spacing: -0.02em; }}
  .headline {{
    position: relative; margin-top: auto; font-size: 62px; line-height: 1.1; font-weight: 600;
    color: {INK}; letter-spacing: -0.025em; max-width: 15ch;
  }}
  .tagline {{ position: relative; margin-top: 22px; font-size: 27px; font-weight: 400; color: {INK_3}; letter-spacing: -0.01em; }}
  .journey {{
    position: relative; margin-top: 46px; padding-top: 26px; border-top: 1px solid rgba(242,243,244,0.12);
    font-size: 19px; font-weight: 500; color: {BRAND_PINE}; letter-spacing: 0.02em;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="rule"></div>
    <div class="glow"></div>
    <div class="lockup">
      <img src="{mark_uri}" alt="" />
      <div class="wordmark">Radal.</div>
    </div>
    <div class="headline">{HEADLINE}</div>
    <div class="tagline">{TAGLINE}</div>
    <div class="journey">{JOURNEY}</div>
  </div>
</body>
</html>"""


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    out = BRAND / "og-image.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
        page.set_content(card_html(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out))
        browser.close()
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
