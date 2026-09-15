"""Actual subprocess + POSIX PTY + real Chromium end-to-end tests.

The test driver answers terminal capability queries; it is not Ghostty. The PNG
frames decoded here are the actual bytes emitted by the application, not mocks.
"""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


class PtyDriver:
    def __init__(self, tmp_path, pixel_mouse=False):
        self.master, self.slave = os.openpty()
        self.original = termios.tcgetattr(self.slave)
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 33, 100, 900, 594))
        self.state_path = tmp_path / 'state.json'
        self.capture = bytearray()
        self.pixel_mouse = pixel_mouse
        self.answered = False
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/pty_probe_child.py'), str(self.state_path)],
                                        stdin=self.slave, stdout=self.slave, stderr=self.slave,
                                        cwd=ROOT, env={**os.environ, 'TERM': 'xterm-ghostty', 'TERM_PROGRAM': 'ghostty'})

    def drain(self, seconds=0.05):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], max(0, min(0.03, deadline-time.monotonic())))[0]:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.capture.extend(data)
                if not self.answered and b'a=q,t=d,f=24;AAAA' in self.capture:
                    mode = b'2' if self.pixel_mouse else b'0'
                    self.send(b'\x1b_Gi=31;OK\x1b\\\x1b[6;18;9t\x1b[?1016;' + mode + b'$y')
                    self.answered = True

    def send(self, data):
        os.write(self.master, data)

    def wait_for(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        state = {}
        while time.monotonic() < deadline:
            self.drain()
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text())
            if predicate(state):
                return state
            if self.process.poll() is not None:
                break
        raise AssertionError(f'PTY condition failed. State={state}, exit={self.process.poll()}, output_tail={bytes(self.capture[-2000:])!r}')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            deadline = time.monotonic() + 10
            while self.process.poll() is None and time.monotonic() < deadline:
                self.drain()
            if self.process.poll() is None:
                self.process.kill()
        self.process.wait(timeout=5)
        self.drain(0.1)
        os.close(self.master)
        os.close(self.slave)


def frames(data):
    current = []
    result = []
    for header, payload in re.findall(rb'\x1b_G([^;]*);([^\x1b]*)\x1b\\', data):
        fields = dict(part.split(b'=', 1) for part in header.split(b',') if b'=' in part)
        if fields.get(b'a') == b'T':
            current = []
        if b'm' in fields:
            current.append(payload)
            if fields[b'm'] == b'0':
                image = base64.b64decode(b''.join(current))
                if image.startswith(b'\x89PNG'):
                    result.append(image)
                current = []
    return result


@pytest.mark.parametrize(('pixel_mouse', 'exit_method'), [(False, 'key'), (True, 'signal')])
def test_pty_browser_input_render_resize_and_terminal_restore(tmp_path, pixel_mouse, exit_method):
    driver = PtyDriver(tmp_path, pixel_mouse)
    try:
        driver.wait_for(lambda state: state.get('ready'))
        driver.drain(0.25)
        if pixel_mouse:
            driver.send(b'\x1b[<0;51;97M\x1b[<0;51;97m')
        else:
            driver.send(b'\x1b[<0;6;6M\x1b[<0;6;6m')
        driver.wait_for(lambda state: state.get('count') == 1)
        if pixel_mouse:
            driver.send(b'\x1b[<0;90;190M\x1b[<0;90;190m')
        else:
            driver.send(b'\x1b[<0;10;11M\x1b[<0;10;11m')
        driver.send(b'\x1b[200~' + '日本語 input'.encode() + b'\x1b[201~')
        driver.wait_for(lambda state: state.get('entry') == '日本語 input')
        driver.send(b'z')
        driver.wait_for(lambda state: state.get('entry') == '日本語 inputz' and 'z' in state.get('keys', []))
        fcntl.ioctl(driver.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 25, 80, 720, 450))
        os.kill(driver.process.pid, signal.SIGWINCH)
        state = driver.wait_for(lambda state: state.get('viewport') == [720, 396])
        driver.drain(0.5)
        if exit_method == 'key':
            driver.send(b'\x11')
        else:
            driver.process.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while driver.process.poll() is None and time.monotonic() < deadline:
            driver.drain()
        assert driver.process.poll() == 0
        driver.drain(0.1)
        assert termios.tcgetattr(driver.slave) == driver.original
        assert b'\x1b[?1049l' in driver.capture
        assert b'\x1b[?1003l' in driver.capture
        assert b'\x1b[<u' in driver.capture
        images = frames(bytes(driver.capture))
        assert len(images) >= 2
        dimensions = [struct.unpack('>II', image[16:24]) for image in images]
        assert (900, 540) in dimensions
        assert (720, 396) in dimensions
        evidence = os.environ.get('GHOSTBROWSE_EVIDENCE_DIR')
        if evidence:
            path = Path(evidence)
            path.mkdir(parents=True, exist_ok=True)
            (path / f'pty-{exit_method}-frame.png').write_bytes(images[-1])
            (path / f'pty-{exit_method}-result.json').write_text(json.dumps({
                **state, 'exit_code': driver.process.returncode, 'terminal_attributes_restored': True,
                'frames_decoded': len(images), 'frame_sizes': sorted(set(dimensions)),
                'pixel_mouse': pixel_mouse, 'actual_ghostty': False,
            }, indent=2, ensure_ascii=False))
    finally:
        driver.close()
