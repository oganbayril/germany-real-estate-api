# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.48"]
# ///
"""Regenerate the link-preview image (og:image) from the real demo page.

    uv run scripts/make_og_image.py                 # against the live site
    uv run scripts/make_og_image.py --url http://127.0.0.1:8000/

First run only: ``uv run --with playwright playwright install chromium``.

Fills the form with a realistic example (city-wide Berlin: naming a premium
district would need a price to match, and the model's location effect is
weak), clicks "Estimate price", waits for the model's answer, then re-lays
the same page out as a 1200x630 card with large type (previews are often
shown ~300 px wide) and screenshots it. Prints
the prediction so it can be sanity-checked before the image is committed.
Playwright is deliberately not a project dependency: it's only needed here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "src/realestate/api/static/og-image.png"

# Screenshot-only layout: title + result on the left, the filled form on the
# right. Theme toggle and footer are hidden; nothing here touches the real page.
CARD_CSS = """
html, body { width: 1200px; height: 630px; overflow: hidden; }
body { padding: 0; display: block; }
main {
  max-width: none; width: 1200px; height: 630px; padding: 48px 56px;
  display: grid; grid-template-columns: 1fr 430px; column-gap: 48px;
  grid-template-rows: auto auto 1fr; align-items: start;
}
.theme-toggle, footer, #error { display: none !important; }
h1 { grid-column: 1; grid-row: 1; font-size: 50px; line-height: 1.12; margin: 0 0 18px; }
.subtitle { grid-column: 1; grid-row: 2; font-size: 25px; line-height: 1.4; margin: 0 0 26px; }
#result { grid-column: 1; grid-row: 3; margin: 0; padding: 26px 30px; align-self: end; }
.price { font-size: 76px; line-height: 1.05; }
.price-sub { font-size: 30px; margin-top: 6px; }
.note { font-size: 19px; margin-top: 14px; padding-top: 12px; }
main > .card:not(#result) {
  grid-column: 2; grid-row: 1 / 4; margin: 0; padding: 22px 24px; align-self: center;
}
main > .card:not(#result) label { font-size: 15px; margin: 12px 0 5px; }
main > .card:not(#result) label:first-of-type { margin-top: 0; }
main > .card:not(#result) input,
main > .card:not(#result) select { font-size: 17px; padding: 8px 11px; }
#go { margin-top: 16px; font-size: 18px; padding: 11px; }
"""

SUBTITLE = "An XGBoost model trained on real Immowelt listings, served by a live API."


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://germany-real-estate.duckdns.org/")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630}, color_scheme="light")
        page.goto(args.url)
        # Let /locations fill the dropdowns first so they settle at "— any —".
        page.wait_for_load_state("networkidle")

        page.select_option("#city", "berlin")
        page.fill("#area", "70")
        page.fill("#rooms", "3")
        page.fill("#floor", "2")
        page.select_option("#energy", "C")
        page.click("#go")
        page.wait_for_selector("#result.show", timeout=15_000)

        print("price:   ", page.inner_text("#price"))
        print("per m²:  ", page.inner_text("#priceSub"))
        print("note:    ", page.inner_text("#note"))

        page.add_style_tag(content=CARD_CSS)
        page.eval_on_selector(".subtitle", "(el, t) => { el.textContent = t; }", SUBTITLE)
        page.screenshot(path=str(args.out), clip={"x": 0, "y": 0, "width": 1200, "height": 630})
        browser.close()

    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
