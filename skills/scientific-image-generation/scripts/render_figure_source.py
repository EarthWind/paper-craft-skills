"""Render a figure source written by the host model into a PNG.

Used by the code-render backend (Claude Code default, Codex fallback when no
image tool is available). Supported sources:

  .svg   rsvg-convert > cairosvg > inkscape > magick > headless Chrome/Chromium
  .html  headless Chrome/Chromium screenshot
  .py    run with this Python; the script must save to the path in $FIGURE_OUT
  .dot   graphviz `dot -Tpng`
  .mmd   mermaid-cli `mmdc`

Prints the converter it used; exits 2 when no converter is available so the
agent can record a blocker instead of faking an image.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"]


def run(cmd, env=None):
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-800:]


def find_chrome():
    for name in CHROME_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


def chrome_screenshot(source, out, width, height):
    chrome = find_chrome()
    if not chrome:
        return False, "no Chrome/Chromium found"
    with tempfile.TemporaryDirectory() as profile:
        return run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--default-background-color=FFFFFFFF",
                f"--window-size={width},{height}",
                f"--screenshot={out}",
                Path(source).resolve().as_uri(),
            ]
        )


def svg_candidates(source, out, width, height):
    if shutil.which("rsvg-convert"):
        yield "rsvg-convert", lambda: run(["rsvg-convert", "-w", str(width), "-h", str(height), "-b", "white", "-o", str(out), str(source)])
    try:
        import cairosvg  # noqa: F401

        def _cairo():
            import cairosvg

            cairosvg.svg2png(url=str(source), write_to=str(out), output_width=width, output_height=height, background_color="white")
            return True, ""

        yield "cairosvg", _cairo
    except ImportError:
        pass
    if shutil.which("inkscape"):
        yield "inkscape", lambda: run(["inkscape", str(source), "--export-type=png", f"--export-filename={out}", "-w", str(width), "-h", str(height), "--export-background=white"])
    if shutil.which("magick"):
        yield "imagemagick", lambda: run(["magick", "-background", "white", "-density", "192", str(source), "-resize", f"{width}x{height}", str(out)])
    if find_chrome():
        yield "headless-chrome", lambda: chrome_screenshot(source, out, width, height)


def render(source, out, width, height):
    source = Path(source)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    attempts = []
    if suffix == ".svg":
        candidates = list(svg_candidates(source, out, width, height))
    elif suffix in {".html", ".htm"}:
        candidates = [("headless-chrome", lambda: chrome_screenshot(source, out, width, height))] if find_chrome() else []
    elif suffix == ".py":
        env = dict(os.environ, FIGURE_OUT=str(out.resolve()), FIGURE_WIDTH=str(width), FIGURE_HEIGHT=str(height))
        candidates = [("python", lambda: run([sys.executable, str(source)], env=env))]
    elif suffix in {".dot", ".gv"}:
        candidates = [("graphviz", lambda: run(["dot", "-Tpng", "-Gdpi=192", str(source), "-o", str(out)]))] if shutil.which("dot") else []
    elif suffix in {".mmd", ".mermaid"}:
        candidates = [("mermaid-cli", lambda: run(["mmdc", "-i", str(source), "-o", str(out), "-w", str(width), "-H", str(height), "-b", "white"]))] if shutil.which("mmdc") else []
    else:
        return None, [f"unsupported source type: {suffix}"]

    for name, attempt in candidates:
        if out.exists():
            out.unlink()
        try:
            ok, message = attempt()
        except Exception as exc:  # converter crashed; try the next one
            ok, message = False, str(exc)
        if ok and out.exists() and out.stat().st_size > 0:
            return name, attempts
        attempts.append(f"{name}: {message or 'no output'}")
    if not candidates:
        attempts.append(f"no converter available for {suffix}")
    return None, attempts


def main():
    parser = argparse.ArgumentParser(description="Render SVG/HTML/Python/Graphviz/Mermaid figure source to PNG.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True, help="PNG output path.")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"BLOCKED: source not found: {args.source}")
        return 2
    used, attempts = render(args.source, args.out, args.width, args.height)
    if not used:
        print("BLOCKED: could not render figure source")
        for line in attempts:
            print("  - " + line)
        return 2
    print(f"OK: rendered with {used} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
