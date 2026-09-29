"""V6.1: V6 unchanged, the cockpit opens itself in a browser tab at start (issue #2)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import crypto_bot_v6 as bot_v6
import crypto_bot_v61 as bot
import crypto_cockpit_v61 as cockpit_ui
from alpaca_fake_v6 import FakeAlpacaV6, no_wait

ROOT = Path(__file__).resolve().parents[1]
ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')


@pytest.fixture
def alpaca61(tmp_path, monkeypatch):
    fake = FakeAlpacaV6()
    monkeypatch.setattr(bot, 'DATENQUELLE', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: i['symbol'] for c, i in bot.COINS.items()})
    monkeypatch.setattr(bot, 'ALPACA_KONTO', None)
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    bot.KURSFEHLER.clear()
    bot.SPREADS.clear()
    return fake


def source(module):
    return Path(module.__file__).read_text(encoding='utf-8')


# ---------- strategy unchanged ----------

def test_bot_is_v6_apart_from_the_version():
    old, new = source(bot_v6).splitlines(), source(bot).splitlines()
    assert len(old) == len(new)
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert [b for _, b in changed] == [
        '"""Trading Bot V6.1: Strategien A (Pullback) und B (Squeeze), Alpaca-Live-Daten, virtuell. Keine Orders."""',
        '# TRADING BOT V6.1 · LIVE-DEMO',
        'VERSION = "6.1"',
    ]


def test_v61_continues_the_v6_account_files():
    assert bot.speicherorte_einrichten.__code__.co_consts == bot_v6.speicherorte_einrichten.__code__.co_consts


@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    for forbidden in ORDER_CALLS:
        assert forbidden not in source(module)


def test_cockpit_runs_with_v61(alpaca61, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v61.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v61.html').read_text()
    assert 'Marktlage und Strategien' in page and 'Alpaca-Orders: 0' in page
    assert 'V6.1' in page


# ---------- issue #2: open the cockpit automatically ----------

def test_browser_view_js_opens_tab_and_keeps_link():
    js = cockpit_ui.browser_view_js(8769)
    assert 'google.colab.kernel.proxyPort' in js
    assert 'window.open(url, tabName)' in js
    assert json.dumps(cockpit_ui.LINK_TEXT, ensure_ascii=False) in js      # fallback link
    assert 'colab.research.google.com' in js                                 # pop-up hint
    assert js.rstrip().endswith(')') and ', true, ' in js


def test_browser_view_js_without_auto_open_only_shows_the_link():
    js = cockpit_ui.browser_view_js(8769, automatisch=False)
    assert ', false, ' in js
    assert 'if (!automatisch) { return; }' in js


def test_browser_view_js_quotes_its_arguments(monkeypatch):
    monkeypatch.setattr(cockpit_ui, 'LINK_TEXT', '"); alert(1); ("')
    js = cockpit_ui.browser_view_js(8769)
    assert '"\\"); alert(1); (\\""' in js


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('blocked', [False, True])
def test_browser_view_js_runs(tmp_path, blocked):
    """Run the snippet in node with a minimal fake of Colab's output frame."""
    script = tmp_path / 'run.js'
    script.write_text('''
const opened = [];
function node(tag) { return {tag, style: {cssText: ''}, children: [], textContent: '',
                             appendChild(c) { this.children.push(c); }}; }
global.document = {createElement: node, body: node('body')};
global.window = {element: node('out'), open: (url, name) => { opened.push([url, name]); return %s; }};
global.google = {colab: {kernel: {accessAllowed: true,
                                  proxyPort: async (port) => `https://${port}-proxy.example/`}}};
(async () => {
  await %s;
  const box = window.element.children[0];
  console.log(JSON.stringify({opened, link: box.children[0].href, text: box.children[0].textContent,
                              note: box.children[1].textContent}));
})();
''' % ('null' if blocked else '{}', cockpit_ui.browser_view_js(8769)), encoding='utf-8')
    result = json.loads(subprocess.run(['node', str(script)], capture_output=True, text=True,
                                       check=True).stdout)
    assert result['opened'] == [['https://8769-proxy.example/', cockpit_ui.TAB_NAME]]
    assert result['link'] == 'https://8769-proxy.example/'
    assert result['text'] == cockpit_ui.LINK_TEXT
    expected = cockpit_ui.POPUP_HINWEIS if blocked else cockpit_ui.GEOEFFNET_HINWEIS
    assert result['note'] == expected


@pytest.mark.parametrize('automatisch', [True, False])
def test_open_browser_view_outside_colab(monkeypatch, automatisch):
    opened = []
    monkeypatch.setattr('webbrowser.open', lambda url, new=0: opened.append(url))
    server = cockpit_ui.open_browser_view(cockpit_ui.Cockpit(display_enabled=False), port=18830,
                                          automatisch=automatisch)
    try:
        port = server.server_address[1]
        assert opened == ([f'http://localhost:{port}/'] if automatisch else [])
    finally:
        server.shutdown()
        server.server_close()


def test_defaults():
    assert cockpit_ui.AUTOMATISCH_OEFFNEN is True
    assert cockpit_ui.open_browser_view.__defaults__ == (8769, True)


def test_notebook_cockpit_cell_has_the_switch():
    notebook = json.loads((ROOT / 'TRADING_BOT_V6_1_Live_Demo.ipynb').read_text(encoding='utf-8'))
    cells = [''.join(c['source']) for c in notebook['cells'] if c['cell_type'] == 'code']
    cockpit_cell = next(c for c in cells if 'open_browser_view' in c and not c.startswith('%%writefile'))
    assert 'COCKPIT_AUTOMATISCH_OEFFNEN = True' in cockpit_cell
    assert 'automatisch=COCKPIT_AUTOMATISCH_OEFFNEN' in cockpit_cell
    assert "cockpit_v61_latest.html" in cockpit_cell
