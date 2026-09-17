"""Checks the state the app opens in, before anything is sent.

Covers the things that are easy to break with a stylesheet edit:

  * nothing rendered but the one line disclaimer and the one line opener
  * worksheet panel empty rather than pre-filled
  * palette stays light even when the operating system asks for dark
  * no horizontal scroll at phone width

Writes screenshots to docs/screenshots/ so the README stays current.

Run:  python tests/test_initial_state.py
"""

import asyncio
import pathlib
import sys

from playwright.async_api import async_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "index.html"
SHOTS = ROOT / "docs" / "screenshots"

# No host runtime, so the app falls back to its own key entry path. That is the
# state a first time visitor sees.
NO_RUNTIME = "window.claude = { use: async function () { return null; } };"

VIEWPORTS = [
    ("desktop", 1320, 900, "light"),
    ("mobile", 390, 844, "light"),
    ("desktop-os-dark", 1320, 900, "dark"),
]


async def main():
    failures = []
    errors = []
    SHOTS.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for name, width, height, scheme in VIEWPORTS:
            page = await browser.new_page(
                viewport={"width": width, "height": height}, color_scheme=scheme
            )
            page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
            await page.add_init_script(NO_RUNTIME)
            await page.goto(APP.as_uri())
            await page.wait_for_timeout(700)

            overflow = await page.evaluate(
                "() => document.documentElement.scrollWidth > window.innerWidth + 1"
            )
            ground = await page.evaluate(
                "() => getComputedStyle(document.body).backgroundColor"
            )
            # Light palette keeps every channel high. A dark repaint would drop them.
            light = all(int(v) > 200 for v in ground.strip("rgb()").split(",")[:3])

            messages = await page.locator("#threadInner .msg").count()
            empty = await page.locator("#ws .ws-empty").inner_text()

            print(f"{name:16} overflow={overflow}  bg={ground}  light={light}  messages={messages}")
            failures.append(overflow is False)
            failures.append(light is True)
            failures.append(messages == 1)
            failures.append(empty == "Nothing here yet.")

            await page.screenshot(path=str(SHOTS / f"{name}.png"))
            await page.close()

        await browser.close()

    print(f"errors: {errors or 'none'}")
    failed = failures.count(False) + len(errors)
    print(f"\n{'FAILED' if failed else 'OK'}: {failures.count(True)}/{len(failures)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
