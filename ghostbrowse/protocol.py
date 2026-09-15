"""Small, testable implementation of the terminal protocols we actually use.

No browser-controlled string is ever emitted as a terminal control sequence.
Images use direct PNG transmission, so the terminal never reads arbitrary files.
"""
from __future__ import annotations

import base64
import codecs
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterator

ESC = "\x1b"
CSI = ESC + "["
ST = ESC + "\\"
PASTE_START = CSI + "200~"
PASTE_END = CSI + "201~"
MAX_PASTE = 1_048_576
MAX_ESCAPE = 8192


@dataclass(frozen=True)
class Event:
    kind: str
    text: str = ""
    key: str = ""
    modifiers: frozenset[str] = field(default_factory=frozenset)
    x: int = 0
    y: int = 0
    button: int = 0
    release: bool = False
    motion: bool = False


def modifier_names(number: int) -> frozenset[str]:
    bits = max(0, number - 1)
    return frozenset(name for bit, name in
                     ((1, "Shift"), (2, "Alt"), (4, "Control"), (8, "Meta"))
                     if bits & bit)


class InputParser:
    """Incremental UTF-8/VT decoder. Handles split escape and paste sequences."""

    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""
        self.paste: str | None = None
        self.paste_overflow = False

    def feed(self, data: bytes) -> list[Event]:
        self.buffer += self.decoder.decode(data)
        return list(self._parse())

    def flush_escape(self) -> list[Event]:
        """Called after a short inter-byte timeout, not after every read."""
        if self.buffer == ESC and self.paste is None:
            self.buffer = ""
            return [Event("key", key="Escape")]
        return []

    def _parse(self) -> Iterator[Event]:
        while self.buffer:
            if self.paste is not None:
                end = self.buffer.find(PASTE_END)
                if end < 0:
                    # Keep enough trailing bytes to recognize a split end marker.
                    safe = max(0, len(self.buffer) - len(PASTE_END) + 1)
                    self._append_paste(self.buffer[:safe])
                    self.buffer = self.buffer[safe:]
                    return
                self._append_paste(self.buffer[:end])
                self.buffer = self.buffer[end + len(PASTE_END):]
                if self.paste_overflow:
                    yield Event("error", text="Paste exceeds the 1 MiB limit; ignored.")
                else:
                    yield Event("paste", text=self.paste)
                self.paste = None
                self.paste_overflow = False
                continue
            if self.buffer.startswith(PASTE_START):
                self.buffer = self.buffer[len(PASTE_START):]
                self.paste = ""
                continue
            if self.buffer[0] != ESC:
                char = self.buffer[0]
                code = ord(char)
                if code < 32 or code == 127:
                    self.buffer = self.buffer[1:]
                    special = {9: "Tab", 10: "Enter", 13: "Enter", 127: "Backspace", 8: "Backspace"}
                    if code in special:
                        yield Event("key", key=special[code])
                    elif 1 <= code <= 26:
                        yield Event("key", key=chr(96 + code), modifiers=frozenset({"Control"}))
                    continue
                end = 1
                while end < len(self.buffer) and ord(self.buffer[end]) >= 32 and self.buffer[end] != "\x7f":
                    end += 1
                yield Event("text", text=self.buffer[:end])
                self.buffer = self.buffer[end:]
                continue
            if len(self.buffer) < 2:
                return
            if self.buffer.startswith(CSI):
                match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", self.buffer)
                if match is None:
                    if len(self.buffer) > MAX_ESCAPE:
                        self.buffer = ""
                        yield Event("error", text="Discarded an oversized terminal escape sequence.")
                    return
                self.buffer = self.buffer[match.end():]
                event = self._csi(*match.groups())
                if event:
                    yield event
                continue
            if self.buffer[1] in "_]P^":
                end = self.buffer.find(ST, 2)
                bell = self.buffer.find("\x07", 2) if self.buffer[1] == "]" else -1
                if bell >= 0 and (end < 0 or bell < end):
                    end, terminator = bell, 1
                else:
                    terminator = 2
                if end < 0:
                    if len(self.buffer) > MAX_ESCAPE:
                        self.buffer = ""
                    return
                payload = self.buffer[2:end]
                self.buffer = self.buffer[end + terminator:]
                yield Event("reply", text=payload)
                continue
            if self.buffer[1] == "O":
                if len(self.buffer) < 3:
                    return
                final = self.buffer[2]
                self.buffer = self.buffer[3:]
                key = {"P": "F1", "Q": "F2", "R": "F3", "S": "F4",
                       "A": "ArrowUp", "B": "ArrowDown", "C": "ArrowRight", "D": "ArrowLeft",
                       "H": "Home", "F": "End"}.get(final)
                if key:
                    yield Event("key", key=key)
                continue
            char = self.buffer[1]
            self.buffer = self.buffer[2:]
            if char == ESC:
                yield Event("key", key="Escape")
            elif char.isprintable():
                yield Event("key", key=char, modifiers=frozenset({"Alt"}))

    def _append_paste(self, text: str) -> None:
        if self.paste_overflow:
            return
        assert self.paste is not None
        if len(self.paste) + len(text) > MAX_PASTE:
            self.paste = ""
            self.paste_overflow = True
        else:
            self.paste += text

    def _csi(self, parameters: str, intermediate: str, final: str) -> Event | None:
        if parameters.startswith("<") and final in "Mm":
            parts = parameters[1:].split(";")
            if len(parts) != 3 or not all(part.isdigit() for part in parts):
                return None
            button, x, y = map(int, parts)
            return Event("mouse", x=x, y=y, button=button,
                         motion=bool(button & 32), release=final == "m",
                         modifiers=frozenset(name for bit, name in
                                             ((4, "Shift"), (8, "Alt"), (16, "Control")) if button & bit))
        if final == "t" or (intermediate == "$" and final == "y") or final == "c":
            return Event("reply", text=parameters + intermediate + final)
        if final in "IO" and not parameters:
            return Event("focus", text="in" if final == "I" else "out")
        if final == "u":
            parts = parameters.split(";")
            try:
                code = int(parts[0].split(":")[0])
                modparts = parts[1].split(":") if len(parts) > 1 else ["1"]
                modifiers = modifier_names(int(modparts[0] or 1))
                if len(modparts) > 1 and modparts[1] == "3":
                    return None  # Release events must not type a second character.
                names = {27: "Escape", 13: "Enter", 9: "Tab", 127: "Backspace",
                         57348: "Insert", 57349: "Delete", 57350: "ArrowLeft", 57351: "ArrowRight",
                         57352: "ArrowUp", 57353: "ArrowDown", 57354: "PageUp", 57355: "PageDown",
                         57356: "Home", 57357: "End"}
                names.update({57364 + index: f"F{index + 1}" for index in range(12)})
                if code in names:
                    return Event("key", key=names[code], modifiers=modifiers)
                if 32 <= code <= 0x10FFFF and not (0xD800 <= code <= 0xDFFF):
                    char = chr(code)
                    if not modifiers or modifiers == frozenset({"Shift"}):
                        # The third field, when present, is the actual text after layout/shift.
                        if len(parts) > 2 and parts[2]:
                            char = "".join(chr(int(value)) for value in parts[2].split(":"))
                        elif "Shift" in modifiers:
                            char = char.upper()
                        return Event("text", text=char)
                    return Event("key", key=char, modifiers=modifiers)
            except (ValueError, OverflowError):
                return None
            return None
        key = {"A": "ArrowUp", "B": "ArrowDown", "C": "ArrowRight", "D": "ArrowLeft",
               "H": "Home", "F": "End", "Z": "Tab", "P": "F1", "Q": "F2",
               "R": "F3", "S": "F4"}.get(final)
        parts = parameters.split(";")
        if final == "~":
            key = {"1": "Home", "2": "Insert", "3": "Delete", "4": "End", "5": "PageUp", "6": "PageDown",
                   "11": "F1", "12": "F2", "13": "F3", "14": "F4", "15": "F5",
                   "17": "F6", "18": "F7", "19": "F8", "20": "F9", "21": "F10",
                   "23": "F11", "24": "F12"}.get(parts[0])
        if not key:
            return None
        try:
            modifiers = modifier_names(int(parts[1])) if len(parts) > 1 else frozenset()
        except ValueError:
            return None
        if final == "Z":
            modifiers = modifiers | {"Shift"}
        return Event("key", key=key, modifiers=modifiers)


def safe_text(text: str) -> str:
    """Strip terminal escapes, C0/C1 controls and bidi formatting controls."""
    return "".join(char if not unicodedata.category(char).startswith("C") else " " for char in text)


def cell_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def fit_text(text: str, width: int) -> str:
    result: list[str] = []
    used = 0
    for char in safe_text(text):
        size = cell_width(char)
        if used + size > width:
            break
        result.append(char)
        used += size
    return "".join(result) + " " * max(0, width - used)


def kitty_png(png: bytes, image_id: int, columns: int, rows: int) -> bytes:
    """Encode a complete PNG using <=4096-byte base64 chunks and one placement."""
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The frame is not a PNG image")
    if not 1 <= image_id <= 0xFFFFFFFF or columns < 1 or rows < 1:
        raise ValueError("Invalid image placement")
    encoded = base64.b64encode(png)
    chunks = []
    for offset in range(0, len(encoded), 4096):
        payload = encoded[offset:offset + 4096]
        more = int(offset + len(payload) < len(encoded))
        header = (f"a=T,t=d,f=100,i={image_id},p=1,q=2,C=1,c={columns},r={rows},"
                  if offset == 0 else "")
        chunks.append(f"{ESC}_G{header}m={more};".encode("ascii") + payload + ST.encode("ascii"))
    return b"".join(chunks)


def delete_image(image_id: int) -> bytes:
    return f"{ESC}_Ga=d,d=I,i={image_id},q=2;{ST}".encode("ascii")
