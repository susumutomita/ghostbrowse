from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading

import pytest
import pytest_asyncio

from ghostbrowse.browser import Browser, BrowserSettings

OFFLINE = os.environ.get('GHOSTBROWSE_TEST_OFFLINE') == '1'

HTML = '''<!doctype html><html lang="ja"><meta charset="utf-8"><title>Local integration fixture</title>
<style>body{margin:0;font:18px sans-serif;background:#f4f6f8;color:#17212b}main{padding:24px}
button,input,a,textarea{font:18px sans-serif;margin:8px;padding:12px}button{min-width:140px;min-height:48px}
.spacer{height:1800px}.row{display:flex;align-items:center}a{color:#1365ac}</style>
<main><h1>Ghostbrowse integration fixture</h1><div class="row"><button id="count" onclick="this.textContent=++window.count">0</button>
<input id="entry" placeholder="Type here" /></div><textarea id="multi"></textarea>
<form id="form" onsubmit="event.preventDefault(); document.querySelector('#result').textContent=document.querySelector('#entry').value">
<button id="submit">Submit</button></form><p id="result"></p>
<p id="selectable">Select this browser text.</p><a id="next" href="/second">Next page</a>
<a id="popup" href="/second" target="_blank">New tab</a>
<button id="fetch" onclick="fetch('/api').then(r=>r.json()).then(v=>this.textContent=v.message)">Fetch</button>
<button id="alert" onclick="alert('Explicit confirmation')">Alert</button>
<iframe src="/frame" title="frame"></iframe><div class="spacer"></div><p id="bottom">Bottom</p>
<script>window.count=0;window.keys=[];document.addEventListener('keydown',e=>window.keys.push(e.key));</script></main></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api':
            content, content_type = b'{"message":"real fetch worked"}', 'application/json'
        elif self.path == '/second':
            content, content_type = b'<title>Second page</title><h1>Second page</h1><a href="/">Back home</a>', 'text/html'
        elif self.path == '/frame':
            content, content_type = b'<p>Frame content</p><input id="frame-input">', 'text/html'
        else:
            content, content_type = HTML.encode(), 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


@pytest.fixture(scope='session')
def site():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.fixture
def browser_settings():
    return BrowserSettings(executable=os.environ.get('GHOSTBROWSE_TEST_CHROMIUM'),
                           sandbox=os.environ.get('GHOSTBROWSE_TEST_NO_SANDBOX') != '1')


@pytest_asyncio.fixture
async def browser(browser_settings, site):
    browser = Browser(browser_settings)
    await browser.start({'width': 1000, 'height': 720})
    try:
        if OFFLINE:
            await browser.page.set_content(HTML.replace('src="/frame"', 'srcdoc="Frame content"'))
        else:
            await browser.navigate(site)
        yield browser
    finally:
        await browser.close()


def pytest_collection_modifyitems(config, items):
    if OFFLINE:
        marker = pytest.mark.skip(reason="Network disabled by this environment's Chromium policy")
        for item in items:
            if 'network' in item.keywords:
                item.add_marker(marker)
