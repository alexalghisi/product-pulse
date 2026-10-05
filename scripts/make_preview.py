"""Build docs/preview.gif from the running app and a real MCP session.

Needs the web app (5280) and API (8140) running with the demo data, and a personal key
for the MCP part:

    PULSE_API_KEY=ppk_... python scripts/make_preview.py

UI frames are Playwright screenshots of the real app. The terminal frames render the
real output of scripts/mcp_demo.py, revealed one tool call at a time.
"""

from __future__ import annotations

import html
import io
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("PREVIEW_BASE", "http://127.0.0.1:5280")
WIDTH, HEIGHT = 1200, 760
OUT_WIDTH = 900

Frame = tuple[Image.Image, int]


def shot(page: Page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def settle(page: Page) -> None:
    page.wait_for_function("!document.body.innerText.includes('Running…')")
    page.wait_for_timeout(250)


def ui_frames(page: Page) -> list[Frame]:
    frames: list[Frame] = []
    page.goto(BASE)
    page.get_by_label("Username").fill("demo")
    page.get_by_label("Password").fill("demo")
    page.get_by_role("button", name="Log in").click()
    page.locator("[data-testid=trend-line]").first.wait_for()
    settle(page)
    frames.append((shot(page), 1600))

    box = page.locator(".chart rect[fill=transparent]").first.bounding_box()
    assert box is not None
    for fraction in (0.35, 0.6, 0.82):
        page.mouse.move(box["x"] + box["width"] * fraction, box["y"] + box["height"] / 2)
        page.wait_for_timeout(120)
        frames.append((shot(page), 700))
    page.mouse.move(5, 5)

    page.get_by_label("Breakdown").select_option("$browser")
    settle(page)
    frames.append((shot(page), 1800))

    page.get_by_role("tab", name="Funnel").click()
    page.locator("[data-testid=funnel-bar]").first.wait_for()
    settle(page)
    frames.append((shot(page), 1600))
    page.get_by_label("Breakdown").select_option("$browser")
    settle(page)
    frames.append((shot(page), 1400))
    bar = page.locator("[data-testid=funnel-bar]").nth(5).bounding_box()
    assert bar is not None
    page.mouse.move(bar["x"] + bar["width"] / 2, bar["y"] + 10)
    page.wait_for_timeout(150)
    frames.append((shot(page), 1800))
    page.mouse.move(5, 5)

    page.get_by_role("tab", name="Retention").click()
    page.locator("table.retention").wait_for()
    settle(page)
    frames.append((shot(page), 2000))
    return frames


TERMINAL_CSS = """
body { margin: 0; background: #16161a; }
pre { margin: 0; padding: 28px 32px; color: #d8d8d2; font: 15px/1.5 'DejaVu Sans Mono', monospace;
      white-space: pre-wrap; height: 760px; box-sizing: border-box; }
.prompt { color: #7fd17f; } .call { color: #8ab4f8; } .error { color: #f28b82; }
.title { color: #9a9a92; }
"""


def terminal_html(lines: list[str]) -> str:
    out = []
    for line in lines:
        escaped = html.escape(line)
        if line.startswith("$ "):
            out.append(f'<span class="prompt">{escaped}</span>')
        elif line.startswith("> "):
            out.append(f'<span class="call">{escaped}</span>')
        elif line.strip().startswith("error:"):
            out.append(f'<span class="error">{escaped}</span>')
        elif line.startswith("#"):
            out.append(f'<span class="title">{escaped}</span>')
        else:
            out.append(escaped)
    return f"<style>{TERMINAL_CSS}</style><pre>{chr(10).join(out)}</pre>"


def terminal_frames(page: Page, output: str) -> list[Frame]:
    header = [
        "# an MCP client (stdio) calling product-pulse tools on the demo project",
        "$ PULSE_API_KEY=ppk_... python scripts/mcp_demo.py",
        "",
    ]
    blocks = output.strip().split("\n\n")
    frames: list[Frame] = []
    for count in range(1, len(blocks) + 1):
        lines = header + "\n\n".join(blocks[:count]).splitlines()
        page.set_content(terminal_html(lines))
        frames.append((shot(page), 1500 if count < len(blocks) else 3500))
    return frames


def write_gif(frames: list[Frame], path: Path) -> None:
    images = []
    for image, _ in frames:
        scaled = image.resize(
            (OUT_WIDTH, round(image.height * OUT_WIDTH / image.width)), Image.LANCZOS
        )
        images.append(
            scaled.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        )
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )


def main() -> None:
    output = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "mcp_demo.py")],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    ).stdout
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
        )
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        frames = ui_frames(page) + terminal_frames(page, output)
        browser.close()
    target = ROOT / "docs" / "preview.gif"
    write_gif(frames, target)
    frame_dir = (
        Path(os.environ.get("PREVIEW_FRAMES", "")) if os.environ.get("PREVIEW_FRAMES") else None
    )
    if frame_dir:
        frame_dir.mkdir(parents=True, exist_ok=True)
        for i, (image, _) in enumerate(frames):
            image.save(frame_dir / f"frame-{i:02d}.png")
    print(f"{target}: {len(frames)} frames, {target.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
