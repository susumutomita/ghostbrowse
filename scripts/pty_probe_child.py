"""Test-only PTY child. Real Chromium, with authored in-memory HTML; no web I/O.

This file is NOT installed as part of the application package. Sandbox overrides
are limited to the explicit test environment setting for a locked-down container.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostbrowse.app import App, AppSettings
from ghostbrowse.browser import BrowserSettings
from ghostbrowse.terminal import Terminal

FIXTURE = '''<!doctype html><meta charset="utf-8"><title>PTY browser test</title>
<style>body{margin:0;background:#f6f7f9;color:#19232d;font:18px system-ui}
button{position:absolute;left:36px;top:54px;width:180px;height:54px;font:20px system-ui}
input{position:absolute;left:36px;top:140px;width:340px;height:42px;font:18px system-ui}
p{position:absolute;left:36px;top:208px}footer{position:absolute;left:36px;top:290px;color:#52606d}
</style><button id="count" onclick="this.textContent='Clicks: '+(++window.count)">Clicks: 0</button>
<input id="entry" aria-label="Test input"><p>Real Chromium / PTY input / Kitty PNG output</p>
<footer>This is test content, not a screenshot of Ghostty.</footer>
<script>window.count=0;window.keys=[];document.addEventListener('keydown',e=>window.keys.push(e.key))</script>'''


async def main() -> int:
    state_path = Path(sys.argv[1])
    app = App(AppSettings(fps=10, idle_fps=3), BrowserSettings(
        executable=os.environ.get('GHOSTBROWSE_TEST_CHROMIUM'),
        sandbox=os.environ.get('GHOSTBROWSE_TEST_NO_SANDBOX') != '1'), Terminal())

    async def inspect() -> None:
        while not app.stop.is_set():
            if app.browser.pages and app.navigation and app.navigation.done():
                break
            await asyncio.sleep(0.02)
        if app.stop.is_set():
            return
        await app.browser.page.set_content(FIXTURE)
        app.wake.set()
        while not app.stop.is_set():
            result = await app.browser.page.evaluate("""() => ({ready:true, count:window.count,
                entry:document.querySelector('#entry').value, keys:window.keys,
                viewport:[innerWidth,innerHeight]})""")
            temporary = state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            temporary.replace(state_path)
            await asyncio.sleep(0.04)

    observer = asyncio.create_task(inspect())
    try:
        return await app.run()
    finally:
        observer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await observer


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
