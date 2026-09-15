import base64
import re

import pytest

from ghostbrowse.app import AddressEditor
from ghostbrowse.browser import normalize_url
from ghostbrowse.protocol import Event, InputParser, fit_text, kitty_png, safe_text
from ghostbrowse.terminal import Geometry


@pytest.mark.parametrize(('source', 'expected'), [
    ('localhost:3000', 'http://localhost:3000/'),
    ('127.0.0.1:5173/foo?a=1&b=2', 'http://127.0.0.1:5173/foo?a=1&b=2'),
    ('[::1]:8080', 'http://[::1]:8080/'),
    ('example.com', 'https://example.com/'),
    ('https://example.com/a%20b', 'https://example.com/a%20b'),
    ('//example.com/x', 'https://example.com/x'),
    ('https://example.com/日本語?q=入力', 'https://example.com/%E6%97%A5%E6%9C%AC%E8%AA%9E?q=%E5%85%A5%E5%8A%9B'),
    ('about:blank', 'about:blank'), ('', 'about:blank'),
    ('dev.internal:3000/x', 'http://dev.internal:3000/x'),
])
def test_urls(source, expected):
    assert normalize_url(source) == expected


@pytest.mark.parametrize('source', [
    'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,x',
    'https://user:password@example.com', 'https://example.com:99999',
    'https://', 'http://x\n.evil', 'ssh://example.com', 'some search words',
])
def test_reject_unsafe_urls(source):
    with pytest.raises(ValueError):
        normalize_url(source)


def test_fragmented_unicode_and_escape():
    parser = InputParser()
    data = '日本語'.encode()
    events = []
    for byte in data:
        events += parser.feed(bytes([byte]))
    assert ''.join(event.text for event in events) == '日本語'
    assert parser.feed(b'\x1b[') == []
    assert parser.feed(b'1;5D') == [Event('key', key='ArrowLeft', modifiers=frozenset({'Control'}))]


def test_paste_is_never_shortcuts():
    parser = InputParser()
    events = []
    payload = b'\x1b[200~first\n\x11\x03\x1b[113;5u\x1b[201~'
    for byte in payload:
        events += parser.feed(bytes([byte]))
    assert len(events) == 1
    assert events[0].kind == 'paste'
    assert '\x11' in events[0].text


def test_escape_has_a_timeout():
    parser = InputParser()
    assert parser.feed(b'\x1b') == []
    assert parser.flush_escape() == [Event('key', key='Escape')]
    assert parser.flush_escape() == []


@pytest.mark.parametrize(('data', 'key', 'modifiers'), [
    (b'\x11', 'q', {'Control'}), (b'\x03', 'c', {'Control'}),
    (b'\x1b[113;5u', 'q', {'Control'}),
    (b'\x1b[1;3D', 'ArrowLeft', {'Alt'}),
    (b'\x1b[Z', 'Tab', {'Shift'}), (b'\x1bOP', 'F1', set()),
    (b'\x1b[18~', 'F7', set()), (b'\x1b[57355u', 'PageDown', set()),
    (b'\x1b[13u', 'Enter', set()),
])
def test_keys(data, key, modifiers):
    assert InputParser().feed(data) == [Event('key', key=key, modifiers=frozenset(modifiers))]


def test_kitty_key_release_is_not_repeated():
    assert InputParser().feed(b'\x1b[97;1:3u') == []
    assert InputParser().feed(b'\x1b[97;2u')[0].text == 'A'


def test_mouse_wheel_motion_and_release():
    parser = InputParser()
    press, move, release, wheel = parser.feed(b'\x1b[<0;20;10M\x1b[<32;30;12M\x1b[<0;30;12m\x1b[<65;30;12M')
    assert press.x == 20 and press.y == 10
    assert move.motion
    assert release.release
    assert wheel.button == 65


def test_terminal_replies_are_not_typed():
    replies = InputParser().feed(b'\x1b_Gi=31;OK\x1b\\\x1b[6;36;18t\x1b[?1016;2$y')
    assert all(event.kind == 'reply' for event in replies)
    assert [event.text for event in replies] == ['Gi=31;OK', '6;36;18t', '?1016;2$y']


def test_untrusted_title_cannot_inject_terminal_commands():
    text = safe_text('evil\x1b]52;c;aaaa\x07\u202e')
    assert '\x1b' not in text and '\x07' not in text and '\u202e' not in text
    assert fit_text('日本語abc', 5) == '日本 '


def test_png_chunking_round_trip():
    png = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 100
    encoded = kitty_png(png, 91, 80, 20)
    matches = re.findall(rb'\x1b_G([^;]*);([^\x1b]*)\x1b\\', encoded)
    assert len(matches) > 2
    assert all(len(payload) <= 4096 for _, payload in matches)
    assert b'a=T' in matches[0][0] and b'c=80,r=20' in matches[0][0]
    assert all(b'm=1' in header for header, _ in matches[:-1])
    assert b'm=0' in matches[-1][0]
    assert base64.b64decode(b''.join(payload for _, payload in matches)) == png


def test_reject_non_png():
    with pytest.raises(ValueError):
        kitty_png(b'not a png', 91, 80, 20)


def test_geometry_retina_and_pixel_mouse():
    geometry = Geometry(100, 33, 18, 36)
    assert geometry.viewport == {'width': 900, 'height': 540}
    assert geometry.pointer(1, 1, False) is None
    assert geometry.pointer(1, 33, False) is None
    assert geometry.pointer(1, 3, False) == (4.5, 9.0)
    assert geometry.pointer(181, 109, True) == (90.0, 18.0)
    assert Geometry(100, 33, 18, 36, 2).viewport == {'width': 450, 'height': 270}


def test_address_editor_preserves_text_and_cursor():
    editor = AddressEditor('old', 3, True)
    editor.insert('https://example.com')
    assert editor.text == 'https://example.com'
    editor.key(Event('key', key='Home'))
    editor.insert('X')
    editor.key(Event('key', key='Delete'))
    assert editor.text == 'Xttps://example.com'
    editor.key(Event('key', key='u', modifiers=frozenset({'Control'})))
    editor.insert('localhost:3000\n\x11')
    assert editor.text == 'localhost:3000'


def test_paste_limit_is_bounded_and_resynchronizes():
    parser = InputParser()
    assert parser.feed(b'\x1b[200~' + b'a' * 1_100_000) == []
    assert parser.feed(b'\x1b[201~\x11')[0].kind == 'error'
    assert parser.paste is None
