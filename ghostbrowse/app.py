"""Application controller. Terminal shortcuts never depend on page JavaScript."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import signal
import sys
import time
from dataclasses import dataclass

from playwright.async_api import Error

from .browser import Browser, BrowserSettings
from .protocol import CSI, ESC, ST, Event, cell_width, safe_text
from .terminal import Terminal


@dataclass
class AddressEditor:
    text: str = ""
    cursor: int = 0
    selected: bool = True

    def insert(self, value: str) -> None:
        # A pasted newline must not navigate or execute a command.
        value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)
        if self.selected:
            self.text, self.cursor, self.selected = "", 0, False
        room = max(0, 8192 - len(self.text))
        value = value[:room]
        self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
        self.cursor += len(value)

    def key(self, event: Event) -> None:
        key = event.key
        if "Control" in event.modifiers and key.lower() in ("a", "u"):
            if key.lower() == "a":
                self.selected = True
            else:
                self.text, self.cursor, self.selected = "", 0, False
            return
        if key in ("Backspace", "Delete") and self.selected:
            self.text, self.cursor, self.selected = "", 0, False
            return
        if key == "ArrowLeft":
            self.cursor = 0 if self.selected else max(0, self.cursor - 1)
        elif key == "ArrowRight":
            self.cursor = len(self.text) if self.selected else min(len(self.text), self.cursor + 1)
        elif key == "Home":
            self.cursor = 0
        elif key == "End":
            self.cursor = len(self.text)
        elif key == "Backspace" and self.cursor:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1
        elif key == "Delete":
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        self.selected = False

    def display(self, columns: int) -> str:
        text = self.text[:self.cursor] + "|" + self.text[self.cursor:]
        # Scroll a long URL horizontally so the insertion point stays visible.
        prefix = " URL [replace]: " if self.selected else " URL > "
        available = max(8, columns - len(prefix))
        before = sum(cell_width(char) for char in self.text[:self.cursor])
        while before >= available - 2 and text:
            before -= cell_width(text[0])
            text = text[1:]
        return prefix + text


@dataclass(frozen=True)
class AppSettings:
    url: str = "about:blank"
    fps: float = 8.0
    idle_fps: float = 1.0
    force_graphics: bool = False


class App:
    def __init__(self, settings: AppSettings, browser_settings: BrowserSettings, terminal: Terminal) -> None:
        self.settings = settings
        self.terminal = terminal
        self.browser = Browser(browser_settings, self.notify)
        self.stop = asyncio.Event()
        self.wake = asyncio.Event()
        self.address: AddressEditor | None = None
        self.status = "Starting local Chromium..."
        self.status_until = time.monotonic() + 60
        self.help_visible = False
        self.last_input = time.monotonic()
        self.focused = True
        self.last_digest: bytes | None = None
        self.last_page = None
        self.navigation: asyncio.Task | None = None
        self.loading = False
        self.render_errors = 0
        self.exit_code = 0

    def notify(self, message: str) -> None:
        self.status = safe_text(message)
        self.status_until = time.monotonic() + 8
        self.wake.set()

    def launch_navigation(self, coroutine) -> None:
        if self.navigation and not self.navigation.done():
            self.navigation.cancel()
        started = False
        async def run() -> None:
            nonlocal started
            started = True
            self.loading = True
            self.wake.set()
            try:
                await coroutine
            except asyncio.CancelledError:
                raise
            except (Error, ValueError, RuntimeError) as exc:
                self.notify(str(exc).splitlines()[0])
            finally:
                self.loading = False
                self.last_input = time.monotonic()
                self.wake.set()
        self.navigation = asyncio.create_task(run())
        # A rapidly superseded task can be cancelled before its first await.
        # Explicitly close the not-yet-awaited coroutine in that case.
        self.navigation.add_done_callback(lambda _: coroutine.close() if not started else None)

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        watched_signals = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
        tasks: list[asyncio.Task] = []
        try:
            self.terminal.enter()
            for sig in watched_signals:
                loop.add_signal_handler(sig, self.stop.set)
            loop.add_signal_handler(signal.SIGWINCH, self.resized)
            await self.terminal.probe(self.settings.force_graphics)
            await self.terminal.paint(None, " GHOSTBROWSE", " Starting...", " Ctrl+Q: quit")
            # Starting a browser can be slow. Keep the exit key and signals live
            # during startup instead of waiting for the launch timeout.
            startup = asyncio.create_task(self.browser.start(self.terminal.geometry.viewport))
            startup_keys = asyncio.create_task(self._startup_input())
            stop_wait = asyncio.create_task(self.stop.wait())
            try:
                done, _ = await asyncio.wait((startup, stop_wait), return_when=asyncio.FIRST_COMPLETED)
                if stop_wait in done:
                    startup.cancel()
                    await asyncio.gather(startup, return_exceptions=True)
                    return 0
                await startup
            finally:
                startup_keys.cancel()
                stop_wait.cancel()
                await asyncio.gather(startup_keys, stop_wait, return_exceptions=True)
            self.notify("Ready. Ctrl+L opens a URL; F1 shows shortcuts.")
            self.launch_navigation(self.browser.navigate(self.settings.url))
            tasks = [asyncio.create_task(self.input_loop()), asyncio.create_task(self.render_loop())]
            waiter = asyncio.create_task(self.stop.wait())
            tasks.append(waiter)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not waiter:
                    await task
        finally:
            self.stop.set()
            if self.navigation:
                self.navigation.cancel()
                await asyncio.gather(self.navigation, return_exceptions=True)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Restore the terminal even if closing the browser itself fails.
            try:
                with contextlib.suppress(asyncio.TimeoutError, Error):
                    await asyncio.wait_for(self.browser.close(), 8)
            finally:
                for sig in (*watched_signals, signal.SIGWINCH):
                    loop.remove_signal_handler(sig)
                self.terminal.restore()
        return self.exit_code

    async def _startup_input(self) -> None:
        while not self.stop.is_set():
            event = await self.terminal.events.get()
            if event.kind == "eof" or (event.kind == "key" and event.key.lower() in ("q", "c") and "Control" in event.modifiers):
                self.stop.set()
                return

    def resized(self) -> None:
        self.terminal.write(f"{CSI}16t".encode())
        self.last_digest = None
        self.wake.set()

    def chrome(self) -> tuple[str, str, str]:
        tabs = " GHOSTBROWSE " + " ".join(
            f"{'[' if index == self.browser.index else ' '}{index + 1}:{self.browser.titles.get(page, 'Tab')[:18]}"
            f"{']' if index == self.browser.index else ' '}" for index, page in enumerate(self.browser.pages))
        url = self.browser.page.url if self.browser.pages else ""
        if self.address:
            address = self.address.display(self.terminal.geometry.columns)
        else:
            address = f" {'Loading' if self.loading else 'URL'}  {url}"
        if self.browser.pending_dialog:
            status = f" {self.status}"
        elif self.help_visible:
            status = " ^L URL | ^T new | ^N/P tabs | ^W close | F7/F8 back/next | ^R reload | ^B copy | ^Y URL | ^Q quit"
        elif time.monotonic() < self.status_until:
            status = " " + self.status
        else:
            profile = "private" if self.browser.settings.profile_path is None else self.browser.settings.profile_path.name
            status = f" {profile} | Ctrl+L URL | Ctrl+T tab | Ctrl+Q shell | F1 help"
        return tabs, address, status

    async def render_loop(self) -> None:
        while not self.stop.is_set():
            start = time.monotonic()
            self.wake.clear()
            if not self.browser.pages:
                self.stop.set()
                break
            geometry = self.terminal.geometry
            if geometry.columns < 32 or geometry.rows < 8:
                await self.terminal.paint(None, " GHOSTBROWSE", " Pane too small.", " Resize to at least 32 x 8. Ctrl+Q quits.")
                await asyncio.sleep(0.2)
                continue
            try:
                page = self.browser.page
                if page is not self.last_page:
                    self.last_digest = None
                    self.last_page = page
                await self.browser.resize(geometry.viewport)
                if self.focused and not self.browser.pending_dialog:
                    png = await self.browser.screenshot()
                    digest = hashlib.blake2b(png, digest_size=16).digest()
                    new_frame = png if digest != self.last_digest else None
                    self.last_digest = digest
                else:
                    new_frame = None
                await self.terminal.paint(new_frame, *self.chrome())
                self.render_errors = 0
            except Error as exc:
                self.render_errors += 1
                if self.render_errors >= 3:
                    self.notify("Waiting for page: " + str(exc).splitlines()[0])
                    await self.terminal.paint(None, *self.chrome())
                await asyncio.sleep(0.1)
            active = self.loading or (time.monotonic() - self.last_input < 2)
            rate = self.settings.fps if active and self.focused else self.settings.idle_fps
            delay = max(0.01, 1.0 / rate - (time.monotonic() - start))
            # Wake quickly on input, but do not busy-loop on mouse floods.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), delay)

    async def input_loop(self) -> None:
        while not self.stop.is_set():
            event = await self.terminal.events.get()
            self.last_input = time.monotonic()
            try:
                await asyncio.wait_for(self.handle(event), 1.5)
            except asyncio.TimeoutError:
                # A JS alert can keep CDP input dispatch pending until it is
                # answered. Return to the input loop so F9/F10 and quit work.
                if not self.browser.pending_dialog:
                    self.notify("The page did not finish handling input; Ctrl+Q still quits.")
            except (Error, ValueError, RuntimeError) as exc:
                self.notify(str(exc).splitlines()[0])
            if self.terminal.overflow_reported:
                self.terminal.overflow_reported = False
                self.notify("Input arrived faster than Chromium could process it; some input was dropped.")
            self.wake.set()

    async def handle(self, event: Event) -> None:
        if event.kind == "eof":
            self.stop.set()
            return
        if event.kind == "error":
            self.notify(event.text)
            return
        if event.kind == "focus":
            self.focused = event.text == "in"
            if not self.focused:
                await self.browser.release_mouse()
            return
        if event.kind == "resize":
            self.last_digest = None
            return
        if event.kind == "key":
            key = event.key.lower()
            control = "Control" in event.modifiers
            if control and key in ("q", "c"):
                self.stop.set()
                return
            if key == "f1":
                self.help_visible = not self.help_visible
                return
            if key in ("f9", "f10") and self.browser.pending_dialog:
                await self.browser.answer_dialog(key == "f9")
                return
            if control and key == "l":
                text = self.browser.page.url
                if text == "about:blank":
                    text = ""
                self.address = AddressEditor(text, len(text), True)
                await self.browser.release_mouse()
                return
        if self.address:
            if event.kind in ("text", "paste"):
                self.address.insert(event.text)
            elif event.kind == "key":
                if event.key == "Escape":
                    self.address = None
                elif event.key == "Enter":
                    value = self.address.text
                    self.address = None
                    self.launch_navigation(self.browser.navigate(value))
                else:
                    self.address.key(event)
            return
        if event.kind == "mouse":
            geometry = self.terminal.geometry
            toolbar_row = ((event.y - 1) // geometry.cell_pixels_y + 1
                           if self.terminal.pixel_mouse else event.y)
            if toolbar_row == 2 and not event.motion and not event.release and not (event.button & 64):
                self.address = AddressEditor(self.browser.page.url, len(self.browser.page.url), True)
                await self.browser.release_mouse()
            else:
                await self.browser.mouse(event, geometry.pointer(event.x, event.y, self.terminal.pixel_mouse))
            return
        if event.kind in ("text", "paste"):
            if event.kind == "text" and len(event.text) == 1 and event.text.isascii():
                # Preserve keydown/keyup for JS applications, not just the input event.
                await self.browser.key(Event("key", key=event.text))
            else:
                await self.browser.insert_text(event.text)
            return
        if event.kind != "key":
            return
        key = event.key.lower()
        control = "Control" in event.modifiers
        if control and key == "r":
            self.launch_navigation(self.browser.reload())
        elif control and key == "t":
            self.launch_navigation(self.browser.new_tab())
        elif control and key == "w":
            self.launch_navigation(self.browser.close_tab())
        elif control and key in ("n", "p"):
            await self.browser.switch_tab(1 if key == "n" else -1)
        elif key == "f7" or ("Alt" in event.modifiers and key == "arrowleft"):
            self.launch_navigation(self.browser.history())
        elif key == "f8" or ("Alt" in event.modifiers and key == "arrowright"):
            self.launch_navigation(self.browser.history(forward=True))
        elif control and key in ("b", "y"):
            text = self.browser.page.url if key == "y" else await self.browser.selection()
            await self.copy(text)
        else:
            await self.browser.key(event)

    async def copy(self, text: str) -> None:
        if not text:
            self.notify("No text selected. Drag inside the page, then press Ctrl+B.")
            return
        data = text.encode("utf-8")[:1_048_576]
        if sys.platform == "darwin":
            process = await asyncio.create_subprocess_exec("/usr/bin/pbcopy", stdin=asyncio.subprocess.PIPE)
            await asyncio.wait_for(process.communicate(data), 3)
            if process.returncode:
                raise RuntimeError("pbcopy could not write to the clipboard")
        else:
            # OSC 52 is emitted only on an explicit user copy command.
            payload = base64.b64encode(data)
            await asyncio.to_thread(self.terminal.write, f"{ESC}]52;c;".encode() + payload + ST.encode())
        self.notify("Copied to clipboard.")
