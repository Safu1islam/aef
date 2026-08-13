#!/usr/bin/env python3
"""Capture the README screenshots from the example project.

Six images: three views, light and dark. They come from `docs/example/` and
never from a real project — the dashboard renders whatever it is pointed at,
and a screenshot of a live project would publish that project's task titles,
blockers and agent activity into a public README.

    python tools/aef.py --root docs/example dashboard --port 7424 &
    python docs/example/shots.py

UNLIKE THE REST OF tools/, THIS NEEDS A DEPENDENCY. Playwright and a Chromium
build are required, and that is why it lives in docs/ rather than tools/: the
framework's stdlib-only contract covers what users run, and a maintainer
regenerating documentation images is not that. `tools/` has a CI job that fails
the build if a third-party import appears in it; this file is outside that
boundary on purpose.

    pip install playwright

It will use a system Chrome or Edge if Playwright's own browser is not
installed, which is the common case on a machine that just has a browser.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
OUT = HERE / "img"
BASE = os.environ.get("AEF_DASHBOARD", "http://127.0.0.1:7424")

# name, path, expand the tree first
VIEWS = [
    ("tree", "/", True),
    ("progress", "/progress", False),
    ("team", "/team", False),
]

# Tried in order when Playwright's bundled Chromium is absent.
SYSTEM_BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def system_browser() -> str | None:
    for candidate in SYSTEM_BROWSERS:
        if os.path.exists(candidate):
            return candidate
    return None


async def main() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright is not installed. pip install playwright", file=sys.stderr)
        return 2

    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        launch: dict = {}
        try:
            browser = await p.chromium.launch()
        except Exception:
            executable = system_browser()
            if executable is None:
                print("no Chromium available. Run `playwright install chromium`, "
                      "or install Chrome or Edge.", file=sys.stderr)
                return 2
            launch = {"executable_path": executable}
            browser = await p.chromium.launch(**launch)

        for scheme in ("light", "dark"):
            page = await browser.new_page(
                viewport={"width": 1200, "height": 900},
                device_scale_factor=1,     # 1x keeps six images near 1 MB total
                color_scheme=scheme,
            )
            for name, path, expand in VIEWS:
                await page.goto(BASE + path, wait_until="networkidle")
                if expand:
                    # The tree ships collapsed below the first level. A
                    # screenshot of collapsed sections shows nothing about what
                    # the tree is for.
                    await page.click("#expand")
                    await page.wait_for_timeout(300)
                target = OUT / f"{name}-{scheme}.png"
                await page.screenshot(path=str(target), full_page=True)
                print(f"  {target.relative_to(HERE.parent.parent)}")
            await page.close()
        await browser.close()

    print("\nRefresh the session timestamps first if the ages look wrong:")
    print("  python docs/example/refresh.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
