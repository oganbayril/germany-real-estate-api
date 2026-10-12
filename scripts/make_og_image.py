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
weak), clicks "Estimate price" and waits for the model's answer. Then it lays
the same page out wide -- title and result on the left, the filled form on the
right -- keeping the page's own fonts, colours and spacing, grows the title
and price as far as the frame allows, and screenshots at 2x (2400x1260).
Prints the prediction so it can be sanity-checked before committing.
Playwright is deliberately not a project dependency: it's only needed here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "src/realestate/api/static/og-image.png"

WIDTH, HEIGHT, SCALE = 1200, 630, 2

# Screenshot-only layout; nothing here touches the real page. Only the frame
# is new: every font, colour, border, radius and margin comes from the page's
# own stylesheet. The 40px frame padding is the page's own body padding.
WIDE_CSS = """
html, body { width: 1200px; height: 630px; overflow: hidden; }
body { padding: 0; display: block; }
main {
  max-width: none; width: 1200px; height: 630px; padding: 40px;
  display: grid; grid-template-columns: 1fr 470px; column-gap: 40px; align-items: center;
}
.theme-toggle, footer, #error { display: none !important; }
#og-left > .card:last-child, main > .card { margin-bottom: 0; }
.price { white-space: nowrap; }
h1 { text-wrap: balance; }
"""

# Grow title and price together, keeping the page's 1.5rem : 2rem ratio, to
# the largest size where the price fits its card, the title takes at most two
# lines and the left column fits the frame.
FIT_JS = """
() => {
  const main = document.querySelector('main');
  const left = document.createElement('div');
  left.id = 'og-left';
  main.insertBefore(left, main.firstChild);
  for (const sel of ['h1', '.subtitle', '#result']) left.appendChild(document.querySelector(sel));

  const h1 = document.querySelector('h1');
  // Never split "Real-Estate" at its hyphen.
  h1.innerHTML = h1.innerHTML.replace(
    'Real-Estate', '<span style="white-space:nowrap">Real-Estate</span>'
  );
  const price = document.querySelector('.price');
  const room = main.clientHeight - 80;
  const fits = () =>
    price.scrollWidth <= price.clientWidth &&
    h1.offsetHeight <= 3.2 * parseFloat(getComputedStyle(h1).fontSize) &&
    left.offsetHeight <= room;
  let k = 4;
  const apply = () => { h1.style.fontSize = 24 * k + 'px'; price.style.fontSize = 32 * k + 'px'; };
  for (apply(); !fits() && k > 1; k -= 0.02) apply();
  return { title_px: Math.round(24 * k), price_px: Math.round(32 * k) };
}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://germany-real-estate.duckdns.org/")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=SCALE,
            color_scheme="light",
        )
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

        page.add_style_tag(content=WIDE_CSS)
        print("fitted:  ", page.evaluate(FIT_JS))
        page.screenshot(path=str(args.out), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        browser.close()

    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
