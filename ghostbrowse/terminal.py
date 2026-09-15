"""POSIX terminal lifecycle, bounded input and Kitty image presentation."""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import sys
import termios
import threading
import tty
from dataclasses import dataclass

from .protocol import CSI, ESC, ST, Event, InputParser, delete_image, fit_text, kitty_png


@dataclass(frozen=True)
class Geometry:
    columns: int
    rows: int
    cell_pixels_x: int = 9
    cell_pixels_y: int = 18
    zoom: float = 1.0

    @property
    def content_rows(self) -> int:
        return max(1, self.rows - 3)

    @property
    def viewport(self) -> dict[str, int]:
        # Keep readable CSS text even on Retina displays. Only use the physical
        # cell aspect ratio; otherwise Retina would make the text half size.
        css_height = 18.0 / self.zoom
        css_width = css_height * self.cell_pixels_x / self.cell_pixels_y
        return {"width": max(1, min(4096, round(self.columns * css_width))),
                "height": max(1, min(2160, round(self.content_rows * css_height)))}

    def pointer(self, x: int, y: int, pixels: bool) -> tuple[float, float] | None:
        if pixels:
            cell_x = (x - 1) / self.cell_pixels_x
            cell_y = (y - 1) / self.cell_pixels_y
        else:
            cell_x, cell_y = x - 0.5, y - 0.5
        if not (0 <= cell_x < self.columns and 2 <= cell_y < self.rows - 1):
            return None
        viewport = self.viewport
        return (min(viewport["width"] - 0.01, cell_x * viewport["width"] / self.columns),
                min(viewport["height"] - 0.01, (cell_y - 2) * viewport["height"] / self.content_rows))


class Terminal:
    def __init__(self, zoom: float = 1.0, input_fd: int | None = None, output_fd: int | None = None) -> None:
        self.input_fd = sys.stdin.fileno() if input_fd is None else input_fd
        self.output_fd = sys.stdout.fileno() if output_fd is None else output_fd
        self.saved = None
        self.parser = InputParser()
        self.events: asyncio.Queue[Event] = asyncio.Queue(maxsize=4096)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.flush_handle: asyncio.TimerHandle | None = None
        self.output_lock = threading.RLock()
        self.base_image_id = secrets.randbelow(0x7FFFFFFD) + 1
        self.image_index = 0
        self.last_image_id: int | None = None
        self.graphics_ok = asyncio.Event()
        self.graphics_rejected = False
        self.pixel_mouse = False
        self.cell_pixels = (9, 18)
        self.zoom = zoom
        self.active = False
        self.input_closed = False
        self.output_closed = False
        self.overflow_reported = False
        self._last_bars: tuple[str, str, str, int, int] | None = None

    @property
    def geometry(self) -> Geometry:
        size = shutil.get_terminal_size((100, 32))
        return Geometry(max(1, size.columns), max(1, size.lines), *self.cell_pixels, self.zoom)

    def write(self, data: bytes) -> None:
        with self.output_lock:
            if self.output_closed:
                return
            view = memoryview(data)
            while view:
                try:
                    written = os.write(self.output_fd, view)
                except InterruptedError:
                    continue
                except OSError:
                    self.output_closed = True
                    raise
                view = view[written:]

    def enter(self) -> None:
        if not os.isatty(self.input_fd) or not os.isatty(self.output_fd):
            raise RuntimeError("Run ghostbrowse directly in a Ghostty terminal, not through a pipe.")
        self.saved = termios.tcgetattr(self.input_fd)
        tty.setraw(self.input_fd, termios.TCSANOW)
        self.active = True
        self.loop = asyncio.get_running_loop()
        self.loop.add_reader(self.input_fd, self._read_input)
        # Alternate screen, hidden cursor, no line-wrap, mouse, paste, focus,
        # keyboard disambiguation. Each changed mode is undone in restore().
        self.write((f"{CSI}?1049h{CSI}?25l{CSI}?7l{CSI}2J{CSI}H"
                    f"{CSI}?1003h{CSI}?1006h{CSI}?2004h{CSI}?1004h{CSI}>1u").encode())
        self.write((f"{ESC}_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA{ST}"
                    f"{CSI}16t{CSI}?1016$p").encode())

    async def probe(self, force: bool = False) -> None:
        if force:
            await asyncio.sleep(0.12)
            return
        try:
            await asyncio.wait_for(self.graphics_ok.wait(), 2.0)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("No Kitty graphics response. Run directly in Ghostty, outside tmux/screen/zellij. "
                               "Use --force-graphics only when you know the terminal supports this protocol.") from exc
        if self.graphics_rejected:
            raise RuntimeError("The terminal rejected the Kitty graphics query.")

    def _read_input(self) -> None:
        try:
            data = os.read(self.input_fd, 65536)
        except (OSError, EOFError):
            data = b""
        if not data:
            self.input_closed = True
            if self.loop:
                self.loop.remove_reader(self.input_fd)
            self._enqueue(Event("eof"))
            return
        for event in self.parser.feed(data):
            self._dispatch(event)
        if self.flush_handle:
            self.flush_handle.cancel()
        assert self.loop
        self.flush_handle = self.loop.call_later(0.06, self._flush_escape)

    def _flush_escape(self) -> None:
        for event in self.parser.flush_escape():
            self._dispatch(event)

    def _dispatch(self, event: Event) -> None:
        if event.kind == "reply":
            if event.text.startswith("Gi=31;"):
                self.graphics_rejected = not event.text.endswith(";OK")
                self.graphics_ok.set()
            dimensions = re.fullmatch(r"6;(\d+);(\d+)t", event.text)
            if dimensions:
                height, width = map(int, dimensions.groups())
                if 1 <= width <= 256 and 1 <= height <= 256:
                    self.cell_pixels = (width, height)
                    self._enqueue(Event("resize"))
            if event.text in ("?1016;1$y", "?1016;2$y"):
                self.pixel_mouse = True
                self.write(f"{CSI}?1016h".encode())
            return
        self._enqueue(event)

    def _enqueue(self, event: Event) -> None:
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            # Never lose the emergency exit behind a flood of mouse reports.
            if event.kind == "eof" or (event.kind == "key" and event.key in ("q", "c") and "Control" in event.modifiers):
                while not self.events.empty():
                    self.events.get_nowait()
                self.events.put_nowait(event)
            elif not event.motion:
                self.overflow_reported = True

    def bars(self, tabs: str, address: str, status: str) -> bytes:
        geometry = self.geometry
        signature = (tabs, address, status, geometry.columns, geometry.rows)
        if signature == self._last_bars:
            return b""
        self._last_bars = signature
        # Exactly 2 toolbar rows and 1 status row. Web content begins on row 3.
        parts = []
        for row, text, style in ((1, tabs, "1;38;5;255;48;5;236"),
                                  (2, address, "38;5;252;48;5;234"),
                                  (geometry.rows, status, "38;5;250;48;5;236")):
            parts.append(f"{CSI}{row};1H{CSI}{style}m{fit_text(text, geometry.columns)}{CSI}0m")
        return "".join(parts).encode("utf-8")

    async def paint(self, png: bytes | None, tabs: str, address: str, status: str) -> None:
        geometry = self.geometry
        chunks = [f"{CSI}?2026h".encode()]
        if png is not None:
            # Double-buffer the frame: complete the new image before deleting
            # the old one, and keep at most two images in the terminal's cache.
            self.image_index ^= 1
            image_id = self.base_image_id + self.image_index
            chunks += [f"{CSI}3;1H".encode(), kitty_png(png, image_id, geometry.columns, geometry.content_rows)]
            if self.last_image_id is not None:
                chunks.append(delete_image(self.last_image_id))
            self.last_image_id = image_id
        chunks += [self.bars(tabs, address, status), f"{CSI}?2026l".encode()]
        await asyncio.to_thread(self.write, b"".join(chunks))

    def restore(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.flush_handle:
            self.flush_handle.cancel()
        if self.loop and not self.loop.is_closed():
            self.loop.remove_reader(self.input_fd)
        try:
            self.write(delete_image(self.base_image_id) + delete_image(self.base_image_id + 1)
                       + (f"{CSI}?2026l{CSI}<u{CSI}?1016l{CSI}?1003l{CSI}?1006l"
                          f"{CSI}?1004l{CSI}?2004l{CSI}?7h{CSI}0m{CSI}?25h{CSI}?1049l").encode())
        except OSError:
            pass
        finally:
            if self.saved is not None:
                try:
                    termios.tcsetattr(self.input_fd, termios.TCSANOW, self.saved)
                except termios.error:
                    pass
