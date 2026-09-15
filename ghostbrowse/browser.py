"""A real, local Chromium instance. No proxy service and no exposed CDP port."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Dialog, Download, Error, Page, Playwright, async_playwright

from .protocol import Event

HOME_HTML = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ghostbrowse</title><style>
:root {color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#111315;color:#eee;
font:16px/1.6 system-ui,sans-serif}main{max-width:780px;margin:9vh auto;padding:32px}
small{color:#83d7c1;letter-spacing:.18em;font-size:11px}h1{font-size:clamp(32px,6vw,60px);
line-height:1.12;letter-spacing:-.055em;margin:24px 0}p{color:#acb3b8;max-width:540px}
nav{display:flex;gap:12px;flex-wrap:wrap;margin-top:36px}a{color:#f5f6f7;text-decoration:none;
border:1px solid #33383d;padding:12px 18px;border-radius:8px}a:hover{background:#24292d}
kbd{background:#282d32;padding:3px 7px;border-radius:4px;color:#ddd}footer{font-size:13px;
margin-top:52px;color:#777f86}hr{border:0;border-top:1px solid #282d32;margin-top:42px}
</style><main><small>GHOSTBROWSE / LOCAL CHROMIUM</small>
<h1>Your browser.<br>Inside your terminal.</h1><p>Open a URL with <kbd>Ctrl L</kbd>.
Click, scroll and type without leaving this pane.</p>
<nav><a href="http://localhost:3000">localhost:3000</a><a href="http://localhost:5173">localhost:5173</a>
<a href="https://ghostty.org/docs">Ghostty docs</a></nav><hr>
<footer>Ctrl T &nbsp; new tab &nbsp; / &nbsp; Ctrl N, P &nbsp; switch tabs<br>
Ctrl B &nbsp; copy selection &nbsp; / &nbsp; Ctrl Q &nbsp; return to shell</footer></main></html>"""


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value or value in ("about:blank", "ghostbrowse:home"):
        return "about:blank"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("URLs must not contain control characters")
    if value.startswith("//"):
        value = "https:" + value
    # Recognize host:port before scheme detection, including bracketed IPv6.
    local = re.match(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])(?::\d+)?(?:[/#?]|$)", value, re.I)
    host_port = re.match(r"^[\w.-]+:\d+(?:[/#?]|$)", value)
    if "://" not in value and (local or host_port):
        value = "http://" + value
    elif not re.match(r"^[a-zA-Z][a-zA-Z\d+.-]*:", value):
        if any(char.isspace() for char in value):
            raise ValueError("Enter a URL, not a search phrase")
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("The URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in the URL are not supported")
    try:
        port = parsed.port
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("The URL has an invalid hostname or port") from exc
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = hostname + (f":{port}" if port is not None else "")
    return urlunsplit((parsed.scheme, authority, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                       quote(parsed.query, safe="%=&?/:@!$'()*+,;~-._"), quote(parsed.fragment, safe="%/?=:~-._")))


@dataclass(frozen=True)
class BrowserSettings:
    profile_path: Path | None = None
    executable: str | None = None
    device_scale_factor: float = 1.0
    color_scheme: str = "dark"
    locale: str = "ja-JP"
    sandbox: bool = True


class Browser:
    def __init__(self, settings: BrowserSettings, notify: Callable[[str], None] | None = None) -> None:
        self.settings = settings
        self.notify = notify or (lambda _: None)
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.pages: list[Page] = []
        self.index = 0
        self.titles: dict[Page, str] = {}
        self.tasks: set[asyncio.Task] = set()
        self.closing = False
        self.viewport = {"width": 900, "height": 540}
        self.pressed_buttons: set[str] = set()
        self.pressed_clicks: dict[str, int] = {}
        self.last_click: tuple[float, float, float, int] = (0, 0, 0, 0)
        self.pending_dialog: Dialog | None = None

    @property
    def page(self) -> Page:
        if not self.pages:
            raise RuntimeError("There is no open browser tab")
        return self.pages[self.index % len(self.pages)]

    def spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() and not self.closing:
            self.notify(str(task.exception()).splitlines()[0])

    async def start(self, viewport: dict[str, int]) -> None:
        self.viewport = viewport
        self.playwright = await async_playwright().start()
        directory = ""
        if self.settings.profile_path:
            directory = str(self.settings.profile_path)
            self.settings.profile_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.settings.profile_path, 0o700)
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                directory, headless=True, chromium_sandbox=self.settings.sandbox,
                executable_path=self.settings.executable,
                viewport=viewport, device_scale_factor=self.settings.device_scale_factor,
                color_scheme=self.settings.color_scheme, locale=self.settings.locale,
                accept_downloads=False, ignore_https_errors=False,
                ignore_default_args=["--disable-popup-blocking"],
                handle_sigint=False, handle_sigterm=False, handle_sighup=False,
                timeout=30000,
            )
        except Error as exc:
            await self.playwright.stop()
            self.playwright = None
            message = str(exc)
            if "Executable doesn't exist" in message:
                raise RuntimeError("Chromium is not installed. Run: python -m playwright install chromium") from exc
            if "ProcessSingleton" in message or "SingletonLock" in message:
                raise RuntimeError("This profile is already open. Choose --profile work2 or use --incognito.") from exc
            raise
        self.context.set_default_timeout(5000)
        self.context.set_default_navigation_timeout(20000)
        self.context.on("page", self._register_page)
        self.context.on("close", lambda _: self.notify("Browser closed.") if not self.closing else None)
        for page in self.context.pages:
            self._register_page(page)
        if not self.pages:
            await self.new_tab()

    def _register_page(self, page: Page) -> None:
        if page in self.pages:
            return
        self.pages.append(page)
        self.index = len(self.pages) - 1
        page.on("close", lambda _: self._page_closed(page))
        page.on("framenavigated", lambda frame: self.spawn(self._update_title(page)) if frame == page.main_frame else None)
        page.on("domcontentloaded", lambda _: self.spawn(self._update_title(page)))
        page.on("download", lambda download: self.spawn(self._cancel_download(download)))
        page.on("dialog", lambda dialog: self._dialog(dialog))
        page.on("filechooser", lambda _: self.notify("File uploads are not supported in this version; no local file was shared."))
        page.on("crash", lambda _: self.notify("This tab crashed. Ctrl+R reloads it; Ctrl+W closes it."))

    async def _update_title(self, page: Page) -> None:
        try:
            self.titles[page] = await page.title()
        except Error:
            pass  # A closing or navigating tab has no stable execution context.

    def _page_closed(self, page: Page) -> None:
        if page not in self.pages:
            return
        old_current = self.pages[self.index % len(self.pages)]
        self.pages.remove(page)
        self.titles.pop(page, None)
        if old_current in self.pages:
            self.index = self.pages.index(old_current)
        else:
            self.index = min(self.index, max(0, len(self.pages) - 1))
        self.pressed_buttons.clear()

    async def _cancel_download(self, download: Download) -> None:
        await download.cancel()
        self.notify(f"Download blocked: {download.suggested_filename}. This version does not save website downloads.")

    def _dialog(self, dialog: Dialog) -> None:
        if self.pending_dialog is not None:
            self.spawn(dialog.dismiss())
            return
        self.pending_dialog = dialog
        origin = urlsplit(dialog.page.url).netloc if dialog.page else "page"
        self.notify(f"{origin}: {dialog.type}: {dialog.message}  [F9 accept / F10 dismiss]")

    async def answer_dialog(self, accept: bool) -> None:
        dialog = self.pending_dialog
        self.pending_dialog = None
        if dialog:
            if accept:
                # No silent confirmation: the user explicitly pressed F9.
                await dialog.accept()
            else:
                await dialog.dismiss()
            self.notify("Dialog accepted." if accept else "Dialog dismissed.")

    async def new_tab(self, url: str = "about:blank") -> None:
        assert self.context
        page = await self.context.new_page()
        self._register_page(page)
        self.index = self.pages.index(page)
        await self.navigate(url)

    async def navigate(self, value: str) -> None:
        target = normalize_url(value)
        if target == "about:blank":
            await self.page.goto("about:blank")
            await self.page.set_content(HOME_HTML, wait_until="domcontentloaded")
            self.titles[self.page] = "Start"
        else:
            await self.page.goto(target, wait_until="domcontentloaded")
            await self._update_title(self.page)

    async def reload(self) -> None:
        if self.page.url == "about:blank":
            await self.navigate("about:blank")
        else:
            await self.page.reload(wait_until="domcontentloaded")

    async def history(self, forward: bool = False) -> None:
        if forward:
            await self.page.go_forward(wait_until="domcontentloaded")
        else:
            await self.page.go_back(wait_until="domcontentloaded")

    async def switch_tab(self, delta: int) -> None:
        if self.pages:
            await self.release_mouse()
            self.index = (self.index + delta) % len(self.pages)
            if self.page.viewport_size != self.viewport:
                await self.page.set_viewport_size(self.viewport)
            await self.page.bring_to_front()

    async def close_tab(self) -> None:
        await self.release_mouse()
        await self.page.close()
        if not self.pages and not self.closing:
            await self.new_tab()

    async def resize(self, viewport: dict[str, int]) -> None:
        self.viewport = viewport
        if self.pages and self.page.viewport_size != viewport:
            await self.page.set_viewport_size(viewport)

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="png", full_page=False, timeout=4000, caret="initial")

    async def insert_text(self, text: str) -> None:
        await self.page.keyboard.insert_text(text)

    async def key(self, event: Event) -> None:
        modifiers = [name for name in ("Control", "Alt", "Shift", "Meta") if name in event.modifiers]
        await self.page.keyboard.press("+".join(modifiers + [event.key]))

    async def mouse(self, event: Event, position: tuple[float, float] | None) -> None:
        if position is None:
            if event.release:
                await self.release_mouse()
            return
        x, y = position
        await self.page.mouse.move(x, y)
        code = event.button
        if code & 64:  # Vertical and horizontal SGR wheel events.
            direction = code & 3
            if direction < 2:
                await self.page.mouse.wheel(0, -100 if direction == 0 else 100)
            else:
                await self.page.mouse.wheel(-100 if direction == 2 else 100, 0)
            return
        button = {0: "left", 1: "middle", 2: "right"}.get(code & 3)
        if event.release:
            if button and button in self.pressed_buttons:
                try:
                    await self.page.mouse.up(button=button, click_count=self.pressed_clicks.get(button, 1))
                finally:
                    self.pressed_buttons.discard(button)
                    self.pressed_clicks.pop(button, None)
            elif button is None:
                await self.release_mouse()
        elif not event.motion and button:
            if button in self.pressed_buttons:
                await self.page.mouse.up(button=button)
            now = asyncio.get_running_loop().time()
            last_time, last_x, last_y, count = self.last_click
            count = count % 3 + 1 if now - last_time < 0.45 and abs(x - last_x) < 5 and abs(y - last_y) < 5 else 1
            self.last_click = (now, x, y, count)
            # Mouse modifiers are held only for this operation, never left stuck.
            held = []
            try:
                for modifier in event.modifiers:
                    await self.page.keyboard.down(modifier)
                    held.append(modifier)
                self.pressed_buttons.add(button)
                self.pressed_clicks[button] = count
                await self.page.mouse.down(button=button, click_count=count)
            finally:
                for modifier in reversed(held):
                    await self.page.keyboard.up(modifier)

    async def release_mouse(self) -> None:
        if self.pages:
            for button in tuple(self.pressed_buttons):
                try:
                    await self.page.mouse.up(button=button)
                except Error:
                    pass
        self.pressed_buttons.clear()
        self.pressed_clicks.clear()

    async def selection(self) -> str:
        # Read only in response to the user's copy shortcut, not on a timer.
        for frame in reversed(self.page.frames):
            try:
                text = await frame.evaluate("""() => {
                    const e = document.activeElement;
                    if (e && (e.tagName === 'TEXTAREA' || e.tagName === 'INPUT')) {
                        if (e.type === 'password') return '';
                        if (e.selectionStart !== null) return e.value.slice(e.selectionStart, e.selectionEnd);
                    }
                    return String(window.getSelection() || '');
                }""")
                if text:
                    return text[:1_048_576]
            except Error:
                continue
        return ""

    async def close(self) -> None:
        self.closing = True
        if self.pending_dialog:
            try:
                await self.pending_dialog.dismiss()
            except Error:
                pass
            self.pending_dialog = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.context = None
            self.playwright = None
