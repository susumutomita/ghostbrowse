"""Command-line entry point. Nothing mutates Ghostty's configuration."""
from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import sys

from . import __version__


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a number") from exc
    if not 0.1 <= number <= 30:
        raise argparse.ArgumentTypeError("Expected a finite number between 0.1 and 30")
    return number


def profile_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", value):
        raise argparse.ArgumentTypeError("Profile names must be 1-48 letters, digits, underscores or hyphens")
    return value


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open an interactive Chromium browser inside your Ghostty pane.")
    parser.add_argument("url", nargs="?", default="about:blank", help="HTTP(S) URL or localhost:port")
    parser.add_argument("--version", action="version", version=f"ghostbrowse {__version__}")
    parser.add_argument("--profile", type=profile_name, default="default", help="Separate persistent profile name (default: default)")
    parser.add_argument("--incognito", action="store_true", help="Discard this browser profile on exit")
    parser.add_argument("--fps", type=positive_float, default=8.0, help="Active refresh target (default: 8)")
    parser.add_argument("--idle-fps", type=positive_float, default=1.0, help="Refresh target after 2 seconds idle (default: 1)")
    parser.add_argument("--zoom", type=positive_float, default=1.0, help="Content scale relative to terminal text (0.5-3, default: 1)")
    parser.add_argument("--retina", type=int, choices=(1, 2), default=2 if sys.platform == "darwin" else 1,
                        help="Screenshot pixel density (macOS default: 2, Linux: 1)")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark", help="Website color-scheme preference")
    parser.add_argument("--browser", type=Path, help="Explicit Chromium executable; bundled Chromium is recommended")
    parser.add_argument("--force-graphics", action="store_true", help="Skip the graphics capability check")
    parser.add_argument("--doctor", action="store_true", help="Print environment diagnostics without starting a browser")
    return parser


def doctor() -> int:
    from playwright.sync_api import sync_playwright
    print(f"Ghostbrowse: {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    try:
        print(f"Playwright: {version('playwright')}")
    except PackageNotFoundError:
        print("Playwright: not installed")
    print(f"Platform: {sys.platform}")
    print(f"TERM_PROGRAM: {os.environ.get('TERM_PROGRAM', '(not set)')}")
    print(f"TERM: {os.environ.get('TERM', '(not set)')}")
    print(f"Interactive stdin/stdout: {sys.stdin.isatty()}/{sys.stdout.isatty()}")
    print(f"Multiplexer: {'yes' if any(os.environ.get(name) for name in ('TMUX', 'STY', 'ZELLIJ')) else 'no'}")
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        print(f"Bundled Chromium: {executable}")
        print(f"Bundled Chromium installed: {executable.is_file()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argument_parser()
    args = parser.parse_args(argv)
    if sys.platform not in ("darwin", "linux"):
        parser.error("This version supports macOS and Linux only")
    if not 0.5 <= args.zoom <= 3:
        parser.error("--zoom must be between 0.5 and 3")
    if args.idle_fps > args.fps:
        parser.error("--idle-fps cannot exceed --fps")
    try:
        from .app import App, AppSettings
        from .browser import BrowserSettings, normalize_url
        from .terminal import Terminal
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}. Run ./install.sh or pip install -r requirements.txt", file=sys.stderr)
        return 2
    if args.doctor:
        return doctor()
    try:
        target = normalize_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("Run directly inside Ghostty, not with redirected stdin/stdout")
    if os.geteuid() == 0:
        parser.error("Do not run a general-purpose web browser as root. Use your normal user account.")
    if any(os.environ.get(name) for name in ("TMUX", "STY", "ZELLIJ")):
        parser.error("Run directly in a Ghostty pane, outside tmux/screen/zellij; passthrough is not implemented")
    if args.browser and (not args.browser.expanduser().is_file() or not os.access(args.browser.expanduser(), os.X_OK)):
        parser.error(f"Browser executable does not exist: {args.browser}")
    data_root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "ghostbrowse"
    profile = None if args.incognito else data_root / "profiles" / args.profile
    browser = BrowserSettings(profile_path=profile, executable=str(args.browser.expanduser()) if args.browser else None,
                              device_scale_factor=args.retina, color_scheme=args.theme, sandbox=True)
    app = App(AppSettings(target, args.fps, args.idle_fps, args.force_graphics), browser, Terminal(args.zoom))
    try:
        return asyncio.run(app.run())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Terminal is already restored by App.run's finally block. Avoid dumping
        # cookies, form input or terminal control bytes from unexpected errors.
        from .protocol import safe_text
        print("ghostbrowse: " + safe_text(str(exc)).split("Call log:", 1)[0][:1600], file=sys.stderr)
        return 1
