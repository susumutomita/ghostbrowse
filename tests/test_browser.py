import asyncio
from dataclasses import replace
import os
import struct

import pytest

from ghostbrowse.app import App, AppSettings
from ghostbrowse.browser import Browser
from ghostbrowse.protocol import Event
from ghostbrowse.terminal import Terminal


async def click(browser, selector):
    bounds = await browser.page.locator(selector).bounding_box()
    position = (bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
    await browser.mouse(Event('mouse', button=0), position)
    await browser.mouse(Event('mouse', button=0, release=True), position)


async def test_actual_chromium_click_input_javascript_and_png(browser):
    await click(browser, '#count')
    assert await browser.page.locator('#count').inner_text() == '1'
    await click(browser, '#entry')
    await browser.insert_text('日本語の入力 ✅')
    await click(browser, '#submit')
    assert await browser.page.locator('#result').inner_text() == '日本語の入力 ✅'
    png = await browser.screenshot()
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>II', png[16:24]) == (1000, 720)


async def test_keys_and_multiline_paste(browser):
    await click(browser, '#entry')
    await browser.key(Event('key', key='a'))
    await browser.key(Event('key', key='Backspace'))
    assert await browser.page.locator('#entry').input_value() == ''
    assert 'a' in await browser.page.evaluate('window.keys')
    await click(browser, '#multi')
    await browser.insert_text('line one\n日本語二行目')
    assert await browser.page.locator('#multi').input_value() == 'line one\n日本語二行目'


@pytest.mark.network
async def test_navigation_history_popups_and_close(browser, site):
    await browser.navigate(site + '/second')
    assert await browser.page.title() == 'Second page'
    await browser.history()
    assert await browser.page.title() == 'Local integration fixture'
    await browser.history(forward=True)
    assert await browser.page.title() == 'Second page'
    await browser.navigate(site)
    async with browser.context.expect_page():
        await click(browser, '#popup')
    await browser.page.wait_for_load_state()
    assert len(browser.pages) == 2
    assert browser.page.url.endswith('/second')
    await browser.switch_tab(-1)
    assert browser.page.url.rstrip('/') == site
    await browser.close_tab()
    assert len(browser.pages) == 1
    await browser.close_tab()
    assert len(browser.pages) == 1
    assert browser.page.url == 'about:blank'


async def test_wheel_resize_and_selection(browser):
    await browser.mouse(Event('mouse', button=65), (400, 400))
    await browser.page.wait_for_function('window.scrollY>0')
    await browser.resize({'width': 640, 'height': 480})
    png = await browser.screenshot()
    assert struct.unpack('>II', png[16:24]) == (640, 480)
    await browser.page.evaluate("() => {const r=document.createRange();r.selectNodeContents(document.querySelector('#selectable'));getSelection().removeAllRanges();getSelection().addRange(r)}")
    assert await browser.selection() == 'Select this browser text.'


@pytest.mark.network
async def test_profile_persistence(browser_settings, site, tmp_path):
    settings = replace(browser_settings, profile_path=tmp_path / 'profile')
    first = Browser(settings)
    await first.start({'width': 800, 'height': 600})
    await first.navigate(site)
    await first.page.evaluate("localStorage.setItem('persist-test','saved')")
    await first.context.add_cookies([{'name': 'session', 'value': 'yes', 'url': site, 'expires': 2_000_000_000}])
    await first.close()
    second = Browser(settings)
    try:
        await second.start({'width': 800, 'height': 600})
        await second.navigate(site)
        assert await second.page.evaluate("localStorage.getItem('persist-test')") == 'saved'
        assert any(cookie['name'] == 'session' for cookie in await second.context.cookies())
        assert os.stat(settings.profile_path).st_mode & 0o777 == 0o700
    finally:
        await second.close()


@pytest.mark.network
async def test_controller_terminal_events_use_real_browser(browser, browser_settings, site):
    app = App(AppSettings(), browser_settings, Terminal(input_fd=0, output_fd=1))
    app.browser = browser
    await app.handle(Event('key', key='l', modifiers=frozenset({'Control'})))
    await app.handle(Event('paste', text=site + '/second'))
    await app.handle(Event('key', key='Enter'))
    await app.navigation
    assert await browser.page.title() == 'Second page'
    await app.handle(Event('key', key='t', modifiers=frozenset({'Control'})))
    await app.navigation
    assert len(browser.pages) == 2
    await app.handle(Event('key', key='p', modifiers=frozenset({'Control'})))
    assert browser.page.url.endswith('/second')
    await app.handle(Event('key', key='q', modifiers=frozenset({'Control'})))
    assert app.stop.is_set()


async def test_dialog_requires_explicit_acceptance(browser):
    task = asyncio.create_task(click(browser, '#alert'))
    for _ in range(100):
        if browser.pending_dialog:
            break
        await asyncio.sleep(0.01)
    assert browser.pending_dialog is not None
    await browser.answer_dialog(False)
    await task
    assert browser.pending_dialog is None


async def test_release_outside_page_does_not_leave_drag_stuck(browser):
    await browser.mouse(Event('mouse', button=0), (100, 100))
    assert browser.pressed_buttons == {'left'}
    await browser.mouse(Event('mouse', button=0, release=True), None)
    assert not browser.pressed_buttons


async def test_password_selection_is_not_copied(browser):
    await browser.page.locator('#entry').evaluate("e=>{e.type='password';e.value='secret';e.focus();e.select()}")
    assert await browser.selection() == ''


@pytest.mark.network
async def test_actual_http_fetch(browser):
    await click(browser, '#fetch')
    await browser.page.wait_for_function("document.querySelector('#fetch').textContent==='real fetch worked'")


async def test_offline_new_tab_switch_close_and_home(browser):
    original = browser.page
    await browser.new_tab()
    assert len(browser.pages) == 2
    assert await browser.page.title() == 'Ghostbrowse'
    await browser.switch_tab(-1)
    assert browser.page is original
    await browser.switch_tab(1)
    await browser.close_tab()
    assert browser.page is original


async def test_offline_controller_input_and_exit(browser, browser_settings):
    app = App(AppSettings(), browser_settings, Terminal(input_fd=0, output_fd=1))
    app.browser = browser
    await click(browser, '#entry')
    await app.handle(Event('text', text='a'))
    await app.handle(Event('paste', text='日本語'))
    assert await browser.page.locator('#entry').input_value() == 'a日本語'
    assert 'a' in await browser.page.evaluate('window.keys')
    await app.handle(Event('key', key='l', modifiers=frozenset({'Control'})))
    await app.handle(Event('paste', text='localhost:3000'))
    assert app.address.text == 'localhost:3000'
    await app.handle(Event('key', key='Escape'))
    assert app.address is None
    await app.handle(Event('key', key='q', modifiers=frozenset({'Control'})))
    assert app.stop.is_set()


async def test_retina_screenshot_and_css_pointer(browser_settings):
    retina = Browser(replace(browser_settings, device_scale_factor=2))
    await retina.start({'width': 400, 'height': 240})
    try:
        await retina.page.set_content('<button style="width:180px;height:60px" onclick="this.textContent=&#39;clicked&#39;">click</button>')
        await retina.mouse(Event('mouse', button=0), (80, 30))
        await retina.mouse(Event('mouse', button=0, release=True), (80, 30))
        assert await retina.page.locator('button').inner_text() == 'clicked'
        png = await retina.screenshot()
        assert struct.unpack('>II', png[16:24]) == (800, 480)
    finally:
        await retina.close()


async def test_ascii_punctuation_key_events(browser):
    await click(browser, '#entry')
    for char in 'aA!@#$%^&*()_+-=[]{};:,.<>/? ' :
        await browser.key(Event('key', key=char))
    assert await browser.page.locator('#entry').input_value() == 'aA!@#$%^&*()_+-=[]{};:,.<>/? '
