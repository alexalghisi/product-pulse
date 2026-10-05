"""Screenshot the running web app: logs in as the demo user and captures each insight kind.

    python scripts/screenshot.py OUT_DIR [--base http://127.0.0.1:5280]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def login(page: Page, base: str, user: str, password: str) -> None:
    page.goto(base)
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()
    page.get_by_text("Saved insights").wait_for()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:5280")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium"))
        page = browser.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=1)
        login(page, args.base, args.user, args.password)
        for kind in ("trend", "funnel", "retention"):
            page.goto(f"{args.base}/?kind={kind}")
            page.locator(".chart, table.retention").first.wait_for()
            page.wait_for_timeout(600)
            page.screenshot(path=str(args.out / f"{kind}.png"))
        browser.close()


if __name__ == "__main__":
    main()
