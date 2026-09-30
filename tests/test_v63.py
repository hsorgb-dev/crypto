"""V6.3: more entries (looser filters for thin Alpaca data, 3 more coins) and a blocker statistic."""
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

import crypto_bot_v62 as bot_v62
import crypto_bot_v63 as bot
import crypto_cockpit_v63 as cockpit_ui
from alpaca_fake_v6 import FakeAlpacaV6, frame, no_wait, uptrend
from test_bot_v6 import closed, signal_frame, squeeze_closes, with_pullback

ROOT = Path(__file__).resolve().parents[1]
ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')
CHANGED = {'VERSION': '6.3', 'A_VOLUMEN_FAKTOR': 0.0, 'B_VOLUMEN_FAKTOR': 1.0,
           'PULLBACK_GUELTIG_STUNDEN': 8, 'HANDELSPAUSE': 6 * 3600, 'MAX_STUNDEN_OHNE_HANDEL': 10,
           'MAX_15M_OHNE_HANDEL': 5, 'B_MAX_STUNDEN_OHNE_HANDEL': 2,
           'INTERVALL': 60, 'AUSGABE_MINUTEN': 5}
UNCHANGED_FUNCTIONS = ('indikatoren_1h', 'marktregime', 'pullback_pruefen', 'zustand_a_aktualisieren',
                       'zustand_b_aktualisieren', 'signal_15m_pruefen', 'signal_a_pruefen',
                       'signal_b_pruefen', 'ausstieg_pruefen', 'offenes_risiko_berechnen',
                       'virtueller_kauf', 'virtueller_verkauf', 'trailing_stop_berechnen',
                       'trailing_stop_aktualisieren', 'strategie_auswertung')
OPEN = {'seit': '2000-01-01T00:00:00+00:00', 'bis': '2100-01-01T00:00:00+00:00'}


@pytest.fixture
def alpaca63(tmp_path, monkeypatch):
    fake = FakeAlpacaV6()
    monkeypatch.setattr(bot, 'DATENQUELLE', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: i['symbol'] for c, i in bot.COINS.items()})
    monkeypatch.setattr(bot, 'ALPACA_KONTO', None)
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    bot.KURSFEHLER.clear()
    bot.SPREADS.clear()
    return fake


# ---------- what changed, what did not ----------

def test_changed_parameters():
    for name, value in CHANGED.items():
        assert getattr(bot, name) == value, name


def test_all_other_parameters_unchanged():
    def settings(module):
        return {k: v for k, v in vars(module).items()
                if k.isupper() and isinstance(v, (int, float, str, tuple, dict))
                and k not in CHANGED and k not in ('COINS', 'ROUTINE_ABLEHNUNGEN')
                and k not in ('DATEN_FEHLERTYPEN', 'ZEITRAEUME')
                and k not in ('SPREADS', 'KURSFEHLER', 'HANDELSPAARE', 'ALPACA_KONTO', 'DATENQUELLE')
                and not k.endswith(('_DATEI', '_ORDNER'))
                and not k.startswith(('BLOCKER_', 'A_STATUS', 'B_STATUS', 'SECRET_'))}
    assert settings(bot) == settings(bot_v62)
    assert bot.GEBUEHR == 0.0025 and bot.MAX_OFFENE_POSITIONEN == 3
    assert bot.ROUTINE_ABLEHNUNGEN == tuple(
        'Handelspause nach Verkauf' if g == '24 Stunden Handelspause' else g
        for g in bot_v62.ROUTINE_ABLEHNUNGEN)


def test_three_more_coins():
    assert {c: i for c, i in bot.COINS.items() if c in bot_v62.COINS} == bot_v62.COINS
    assert [i['symbol'] for c, i in bot.COINS.items() if c not in bot_v62.COINS] == [
        'AAVE/USD', 'BCH/USD', 'UNI/USD']


@pytest.mark.parametrize('name', UNCHANGED_FUNCTIONS)
def test_strategy_and_exit_code_unchanged(name):
    assert inspect.getsource(getattr(bot, name)) == inspect.getsource(getattr(bot_v62, name))


def test_own_files(tmp_path):
    bot.speicherorte_einrichten(tmp_path)
    assert bot.KONTO_DATEI.name == 'konto_v63_alpaca_usd.json'
    assert bot.BACKUP_DATEI.name == 'konto_v63_alpaca_usd_backup.json'
    assert bot.STRATEGIE_DATEI.name == 'strategie_zustand_v63.json'
    assert bot.ANALYSE_DATEI.name == 'marktlage_v63.json'
    assert bot.STATISTIK_DATEI.name == 'blocker_statistik_v63.json'
    assert bot.MARKTDATEN_ORDNER.name == 'Marktdaten_Alpaca_v6'


@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    text = Path(module.__file__).read_text(encoding='utf-8')
    for forbidden in ORDER_CALLS:
        assert forbidden not in text


# ---------- looser filters ----------

def test_signal_a_without_volume_filter():
    assert bot.signal_a_pruefen(closed(signal_frame(100.0, jump=1.005, volume=1.0)), OPEN)[1] == 'Signal A'
    assert 'Hoch' in bot.signal_a_pruefen(closed(signal_frame(100.0, jump=1.0)), OPEN)[1]


def test_signal_b_needs_average_volume():
    b = {'seit': OPEN['seit'], 'oben': 100.2, 'unten': 99.0}
    assert bot.signal_b_pruefen(closed(signal_frame(100.0, volume=10.0)), b, atr=1.0)[1] == 'Signal B'
    assert bot.signal_b_pruefen(closed(signal_frame(100.0, volume=9.0)), b, atr=1.0)[1] == \
        'Ausbruchsvolumen zu gering'


@pytest.mark.parametrize('quiet, ok', [(5, True), (6, False)])
def test_quiet_15m_candles(quiet, ok):
    df = closed(signal_frame(100.0, volume=20.0))
    df.iloc[-2 - quiet:-2, df.columns.get_loc('ohne_handel')] = True
    grund = bot.signal_a_pruefen(df, OPEN)[1]
    assert (grund == 'Signal A') is ok, grund


@pytest.mark.parametrize('quiet, ok', [(2, True), (3, False)])
def test_squeeze_tolerates_two_quiet_hours(quiet, ok):
    raw = closed(frame(squeeze_closes()))
    for back in range(quiet):
        raw.iloc[-3 - 5 * back, raw.columns.get_loc('ohne_handel')] = True
    squeeze, _, grund = bot.squeeze_pruefen(bot.indikatoren_1h(raw))
    assert squeeze is ok, grund


@pytest.mark.parametrize('quiet, ok', [(10, True), (11, False)])
def test_hourly_data_check_allows_ten_quiet_hours(quiet, ok):
    df = closed(frame(uptrend(100, 301)))
    for back in range(quiet):
        df.iloc[-2 - 4 * back, df.columns.get_loc('ohne_handel')] = True
    if ok:
        assert bot.marktdaten_pruefen(df, 'bitcoin')
    else:
        with pytest.raises(ValueError, match='Zu wenig Handel'):
            bot.marktdaten_pruefen(df, 'bitcoin')


@pytest.mark.parametrize('hours_ago, blocked', [(5, True), (7, False)])
def test_trading_pause_is_six_hours(hours_ago, blocked):
    konto = bot.neues_konto()
    konto['letzter_ausstieg']['bitcoin'] = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    pruefung, grund = bot.risk_gate(konto, 'bitcoin', {'bitcoin': 100.0}, 2.0, spread=0.0005)
    assert (grund == 'Handelspause nach Verkauf') is blocked
    assert (pruefung is None) is blocked


def test_pullback_waits_eight_hours():
    df = bot.indikatoren_1h(with_pullback(closed(frame(uptrend(100, 301)))))
    t0 = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    a = bot.zustand_a_aktualisieren(None, df, t0)
    assert a['status'] == 'BEREIT' and a['bis'] == (t0 + timedelta(hours=8)).isoformat()
    assert bot.zustand_a_aktualisieren(a, df, t0 + timedelta(hours=7))['status'] == 'BEREIT'
    assert bot.zustand_a_aktualisieren(a, df, t0 + timedelta(hours=8))['status'] == 'ABGELAUFEN'


# ---------- blocker statistic ----------

def test_blocker_reason_is_normalised():
    assert bot.blocker_grund('Spread zu hoch (0.20 % > 0.15 %)') == 'Spread zu hoch'
    assert bot.blocker_grund('Zu wenig Handel: 12 der letzten 50 Stunden ohne Trade (dot)') == 'Zu wenig Handel'
    assert bot.blocker_grund(None) == '?'
    statistik = {}
    bot.blocker_zaehlen(statistik, 'risk_gate', 'Spread zu hoch (0.30 % > 0.15 %)')
    bot.blocker_zaehlen(statistik, 'risk_gate', 'Spread zu hoch (0.20 % > 0.15 %)')
    assert statistik == {'risk_gate': {'Spread zu hoch': 2}}


def test_strategy_a_end_to_end_with_low_volume_is_counted(alpaca63):
    raw_1h = with_pullback(closed(frame(uptrend(100, 321))))
    running = frame(uptrend(100, 321)).iloc[[-1]].copy()
    alpaca63.frames[('BTC/USD', 'h')] = pd.concat([raw_1h.drop(columns='ohne_handel'), running])
    pullback, _ = bot.pullback_pruefen(bot.indikatoren_1h(raw_1h))
    level = float(raw_1h['close'].iloc[-1])
    # volume far below the average: V6.2 would have rejected this signal
    alpaca63.frames[('BTC/USD', '15min')] = signal_frame(level, jump=1.004, volume=2.0)
    seit = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    bot.STRATEGIE_DATEI.write_text(json.dumps(
        {'bitcoin': {'a': {'status': 'BEREIT', 'seit': seit, 'benutzt': [], **pullback}}}))

    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    konto = json.loads(bot.KONTO_DATEI.read_text())
    assert konto['positionen']['bitcoin']['strategie'] == 'A'

    statistik = json.loads(bot.STATISTIK_DATEI.read_text())
    assert statistik == lauf['statistik'] and statistik['seit']
    coins = len(bot.COINS)
    assert sum(statistik['daten'].values()) == coins
    assert sum(statistik['a'].values()) == coins and statistik['a']['Pullback bereit'] == 1
    assert sum(statistik['b'].values()) == coins
    assert statistik['signal_a'] == {'Signal A': 1}
    assert statistik['risk_gate'] == {'Freigegeben': 1}

    # the statistic continues after a restart
    assert bot.neuer_lauf()['statistik'] == statistik


def test_data_errors_are_counted(alpaca63):
    df = frame(uptrend(100, 321))
    df.iloc[-40:-30, df.columns.get_loc('volume')] = 0.0     # 10 hours in a row without trades
    alpaca63.frames[('ETH/USD', 'h')] = df
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert lauf['statistik']['daten']['Zu lange ohne Handel'] == 1
    assert lauf['statistik']['daten']['Daten in Ordnung'] == len(bot.COINS) - 1


def test_cockpit_shows_why_no_trade(alpaca63, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v63.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v63.html').read_text()
    assert 'Warum kein Trade?' in page and 'V6.3' in page
    assert 'A · Pullback-Erkennung · stündlich' in page
    assert f'Daten in Ordnung: {len(bot.COINS)} (100 %)' in page


def test_cockpit_without_statistic():
    data = cockpit_ui.snapshot(dict(konto=None, kurse=None), 'WARTET', [])
    assert 'Noch keine Prüfung gezählt.' in cockpit_ui.render_html(data)


def test_notebook_cockpit_cell():
    notebook = json.loads((ROOT / 'TRADING_BOT_V6_3_Live_Demo.ipynb').read_text(encoding='utf-8'))
    cells = [''.join(c['source']) for c in notebook['cells'] if c['cell_type'] == 'code']
    cockpit_cell = next(c for c in cells if 'open_browser_view' in c and not c.startswith('%%writefile'))
    assert 'import crypto_bot_v63 as bot' in cockpit_cell
    assert 'cockpit_v63_latest.html' in cockpit_cell
    assert 'COCKPIT_AUTOMATISCH_OEFFNEN = True' in cockpit_cell
    assert cockpit_ui.open_browser_view.__defaults__ == (8771, True)
    assert cockpit_ui.TAB_NAME == 'krypto-cockpit-v63'


# ---------- start help: cockpit link and Alpaca key check ----------

import shutil      # noqa: E402
import subprocess  # noqa: E402
import sys         # noqa: E402
import types       # noqa: E402

import requests    # noqa: E402
from alpaca.common.exceptions import APIError  # noqa: E402


def colab_error(name):
    return type(name, (Exception,), {})()


def api_error(status):
    return APIError('{"code": 40110000, "message": "request is not authorized"}',
                    types.SimpleNamespace(response=types.SimpleNamespace(status_code=status), request=None))


class FakeBroker:
    def __init__(self, fail=None):
        self.fail = fail

    def get_account(self):
        if self.fail:
            raise self.fail
        return types.SimpleNamespace(equity='100000', currency='USD')


class FakeQuotes:
    def __init__(self, fail=None):
        self.fail = fail
        self.requests = []

    def get_crypto_latest_quote(self, request):
        self.requests.append(request.symbol_or_symbols)
        if self.fail:
            raise self.fail
        return {}


def test_secrets_are_read_and_checked():
    assert bot.secrets_lesen({'ALPACA_PAPER_API_KEY': ' k ', 'ALPACA_PAPER_SECRET_KEY': 's'}.get) == ('k', 's')

    def raising(name):
        def lesen(_):
            raise colab_error(name)
        return lesen

    with pytest.raises(bot.ZugangsFehler, match='ALPACA_PAPER_API_KEY fehlt'):
        bot.secrets_lesen(raising('SecretNotFoundError'))
    with pytest.raises(bot.ZugangsFehler, match='nicht freigegeben.*Notebookzugriff'):
        bot.secrets_lesen(raising('NotebookAccessError'))
    with pytest.raises(bot.ZugangsFehler, match='nicht freigegeben'):
        bot.secrets_lesen(raising('TimeoutException'))
    with pytest.raises(bot.ZugangsFehler, match='ALPACA_PAPER_SECRET_KEY ist leer'):
        bot.secrets_lesen({'ALPACA_PAPER_API_KEY': 'k', 'ALPACA_PAPER_SECRET_KEY': '  '}.get)


def test_alpaca_keys_are_checked_read_only(capsys):
    quotes = FakeQuotes()
    snapshot = bot.alpaca_zugang_pruefen(FakeBroker(), quotes)
    assert snapshot['equity'] == 100000.0 and quotes.requests == [['BTC/USD']]
    assert 'Alpaca-Schlüssel gültig' in capsys.readouterr().out


@pytest.mark.parametrize('broker_fail, quote_fail, message', [
    (api_error(401), None, r'lehnt die Schlüssel ab \(HTTP 401 bei der Kontoabfrage\)'),
    (api_error(403), None, r'HTTP 403'),
    (None, api_error(401), r'HTTP 401 bei der Kursabfrage'),
    (requests.exceptions.ConnectionError(), None, 'nicht erreichbar'),
])
def test_rejected_keys_give_a_clear_message(broker_fail, quote_fail, message):
    with pytest.raises(bot.ZugangsFehler, match=message):
        bot.alpaca_zugang_pruefen(FakeBroker(broker_fail), FakeQuotes(quote_fail))


def test_start_error_is_shown_in_the_cockpit(tmp_path, capsys, monkeypatch):
    shown = []
    fake_ipython(monkeypatch, shown)
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'c.html')
    cockpit_ui.startfehler_anzeigen(bot.ZugangsFehler('Secret X fehlt. <b>'), cockpit)
    page = cockpit.latest_browser_page
    assert 'Bot nicht gestartet: Alpaca-Zugang' in page and 'Secret X fehlt. &lt;b&gt;' in page
    # no file: before start() Drive is not mounted, a file there would block the mount
    assert not (tmp_path / 'c.html').exists()
    assert 'Secret X fehlt' in capsys.readouterr().out
    assert 'Secret X fehlt. &lt;b&gt;' in shown[0].data     # red box in the start cell


def fake_ipython(monkeypatch, shown):
    """Minimal IPython.display (not installed in CI): HTML/Javascript objects with .data, recorded display()."""
    display_module = types.ModuleType('IPython.display')
    for name in ('HTML', 'Javascript'):
        setattr(display_module, name, type(name, (), {'__init__': lambda self, data: setattr(self, 'data', data)}))
    display_module.display = lambda obj, **kw: shown.append(obj)
    ipython = types.ModuleType('IPython')
    ipython.display = display_module
    monkeypatch.setitem(sys.modules, 'IPython', ipython)
    monkeypatch.setitem(sys.modules, 'IPython.display', display_module)


@pytest.fixture
def fake_colab(monkeypatch):
    shown = []
    output = types.ModuleType('google.colab.output')
    output.answer = 'https://8771-abc.colab.googleusercontent.com/'
    output.scripts = []

    def eval_js(script, timeout_sec=None):
        output.scripts.append((script, timeout_sec))
        if isinstance(output.answer, Exception):
            raise output.answer
        return output.answer

    output.eval_js = eval_js
    colab = types.ModuleType('google.colab')
    colab.output = output
    google = types.ModuleType('google')
    google.colab = colab
    for name, module in (('google', google), ('google.colab', colab), ('google.colab.output', output)):
        monkeypatch.setitem(sys.modules, name, module)
    fake_ipython(monkeypatch, shown)
    output.shown = shown
    return output


def open_view(automatisch=True):
    cockpit = cockpit_ui.Cockpit(display_enabled=False)
    server = cockpit_ui.open_browser_view(cockpit, port=18850, automatisch=automatisch)
    server.shutdown()
    server.server_close()
    return cockpit


def test_colab_link_is_plain_html_and_tab_is_opened(fake_colab, capsys):
    cockpit = open_view()
    url = fake_colab.answer
    assert fake_colab.scripts[0][0].startswith('google.colab.kernel.proxyPort(18850')
    assert cockpit.link == url
    html, js = fake_colab.shown
    assert type(html).__name__ == 'HTML' and f'href="{url}"' in html.data and cockpit_ui.LINK_TEXT in html.data
    assert type(js).__name__ == 'Javascript' and json.dumps(url) in js.data and 'window.open' in js.data
    assert f'Cockpit-Link: {url}' in capsys.readouterr().out
    # the link is also part of the cockpit view
    page = cockpit_ui.render_html(dict(cockpit_ui.snapshot(dict(konto=None, kurse=None), 'WARTET', []), link=url))
    assert f'href="{url}"' in page


def test_colab_link_without_auto_open(fake_colab):
    open_view(automatisch=False)
    assert [type(o).__name__ for o in fake_colab.shown] == ['HTML']


def test_colab_link_falls_back_to_javascript(fake_colab, capsys):
    fake_colab.answer = TimeoutError()
    cockpit = open_view()
    assert cockpit.link is None
    [js] = fake_colab.shown
    assert 'google.colab.kernel.proxyPort' in js.data
    assert 'Ersatzweg' in capsys.readouterr().out


NODE_HARNESS = '''
const opened = [];
function node(tag) { return {tag, style: {cssText: ''}, children: [], textContent: '',
                             appendChild(c) { this.children.push(c); }}; }
global.document = {createElement: node, body: node('body')};
global.window = {element: node('out'), open: (url, name) => { opened.push([url, name]); return %s; }};
global.google = {colab: {kernel: {proxyPort: async (port) => { %s }}}};
(async () => {
  await %s;
  const texts = [];
  (function walk(n) { if (n.textContent) texts.push(n.textContent); (n.children || []).forEach(walk); })(window.element);
  console.log(JSON.stringify({opened, texts}));
})();
'''


def run_node(tmp_path, js, blocked=False, proxy='return `https://${port}-proxy.example/`;'):
    script = tmp_path / 'run.js'
    script.write_text(NODE_HARNESS % ('null' if blocked else '{}', proxy, js), encoding='utf-8')
    return json.loads(subprocess.run(['node', str(script)], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('blocked', [False, True])
def test_open_tab_js_runs(tmp_path, blocked):
    result = run_node(tmp_path, cockpit_ui.open_tab_js('https://x.example/'), blocked)
    assert result['opened'] == [['https://x.example/', cockpit_ui.TAB_NAME]]
    expected = cockpit_ui.POPUP_HINWEIS if blocked else cockpit_ui.GEOEFFNET_HINWEIS
    assert result['texts'] == [expected]


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_fallback_js_reports_a_failing_proxy(tmp_path):
    result = run_node(tmp_path, cockpit_ui.browser_view_js(8771), proxy='throw new Error("kaputt");')
    assert result['opened'] == []
    assert len(result['texts']) == 1 and 'Cockpit-Link nicht verfügbar' in result['texts'][0]
    assert 'kaputt' in result['texts'][0]


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_fallback_js_still_opens_the_tab(tmp_path):
    result = run_node(tmp_path, cockpit_ui.browser_view_js(8771))
    assert result['opened'] == [['https://8771-proxy.example/', cockpit_ui.TAB_NAME]]
    assert cockpit_ui.LINK_TEXT in result['texts']


def start_cell():
    notebook = json.loads((ROOT / 'TRADING_BOT_V6_3_Live_Demo.ipynb').read_text(encoding='utf-8'))
    cells = [''.join(c['source']) for c in notebook['cells'] if c['cell_type'] == 'code']
    return next(c for c in cells if 'bot.start(' in c and not c.startswith('%%writefile'))


@pytest.fixture
def run_start_cell(monkeypatch, tmp_path):
    """Execute the notebook's start cell with fake Colab secrets and fake Alpaca clients."""
    started = []
    monkeypatch.setattr(bot, 'start', lambda *a, **kw: started.append(kw))
    fake_ipython(monkeypatch, [])

    def run(secrets, broker_fail=None, quote_fail=None):
        userdata = types.ModuleType('google.colab.userdata')

        def get(name):
            value = secrets.get(name)
            if isinstance(value, Exception):
                raise value
            if value is None:
                raise colab_error('SecretNotFoundError')
            return value

        userdata.get = get
        colab = types.ModuleType('google.colab')
        colab.userdata = userdata
        google = types.ModuleType('google')
        google.colab = colab
        historical = types.ModuleType('alpaca.data.historical')
        historical.CryptoHistoricalDataClient = lambda **kw: FakeQuotes(quote_fail)
        client = types.ModuleType('alpaca.trading.client')
        client.TradingClient = lambda **kw: FakeBroker(broker_fail)
        for name, module in (('google', google), ('google.colab', colab), ('google.colab.userdata', userdata),
                             ('alpaca.data.historical', historical), ('alpaca.trading.client', client)):
            monkeypatch.setitem(sys.modules, name, module)
        cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'c.html')
        namespace = {'cockpit': cockpit}
        exec(start_cell(), namespace)
        return started, cockpit, namespace

    return run


KEYS = {'ALPACA_PAPER_API_KEY': 'k', 'ALPACA_PAPER_SECRET_KEY': 's'}


def test_start_cell_starts_with_valid_keys(run_start_cell):
    started, cockpit, namespace = run_start_cell(KEYS)
    assert len(started) == 1 and started[0]['cockpit'] is cockpit
    assert 'paper_key' not in namespace and 'paper_secret' not in namespace


@pytest.mark.parametrize('secrets, broker_fail, text', [
    ({}, None, 'ALPACA_PAPER_API_KEY fehlt'),
    ({**KEYS, 'ALPACA_PAPER_SECRET_KEY': colab_error('NotebookAccessError')}, None, 'nicht freigegeben'),
    (KEYS, api_error(401), 'lehnt die Schlüssel ab'),
])
def test_start_cell_warns_and_does_not_start(run_start_cell, capsys, secrets, broker_fail, text):
    started, cockpit, _ = run_start_cell(secrets, broker_fail)
    assert started == []
    assert text in capsys.readouterr().out
    assert text in cockpit.latest_browser_page and 'Bot nicht gestartet' in cockpit.latest_browser_page


# ---------- prices and stops every minute ----------

def test_takt_is_aligned_to_full_minutes():
    t0 = datetime(2026, 9, 24, 10, 3, 5, tzinfo=timezone.utc)
    assert bot.sekunden_bis_naechster_takt(t0) == pytest.approx(15)
    assert bot.sekunden_bis_naechster_takt(t0 + timedelta(seconds=25)) == pytest.approx(50)


def run_at(monkeypatch, lauf, zeitpunkt):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return zeitpunkt
    monkeypatch.setattr(bot, 'datetime', Fixed)
    bot.ein_durchlauf(lauf)


def test_stop_every_minute_but_text_log_every_five(alpaca63, monkeypatch, capsys):
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert 'KURSPRÜFUNG' in capsys.readouterr().out            # first pass: full log
    now = datetime.now(timezone.utc).replace(second=30, microsecond=0)
    quiet = now - timedelta(minutes=(now.minute - 1) % 5)       # minute 1, 6, 11, ...
    loud = now - timedelta(minutes=now.minute % 5)              # minute 0, 5, 10, ...
    for zeitpunkt in (quiet, loud):
        lauf['letzte_stunde'] = zeitpunkt.strftime('%Y-%m-%d-%H')
        lauf['letzte_viertelstunde'] = f"{lauf['letzte_stunde']}-{zeitpunkt.minute // 15}"

    # a position whose stop is hit is sold in a quiet minute, and that is printed
    konto = bot.konto_laden()
    kurs = lauf['kurse']['bitcoin']
    pruefung, _ = bot.risk_gate(konto, 'bitcoin', {'bitcoin': kurs}, kurs * 0.01, spread=0.0005)
    bot.virtueller_kauf(konto, 'bitcoin', pruefung)
    konto['positionen']['bitcoin']['stop_loss'] = kurs * 1.5
    bot.konto_speichern(konto)
    capsys.readouterr()

    run_at(monkeypatch, lauf, quiet)
    out = capsys.readouterr().out
    assert 'KURSPRÜFUNG' not in out and 'jede Minute' not in out
    assert 'bitcoin' not in bot.konto_laden()['positionen']
    assert bot.konto_laden()['transaktionen'][-1]['aktion'] == 'VERKAUF'
    assert out.strip()                                        # the sale is reported right away

    run_at(monkeypatch, lauf, loud)
    out = capsys.readouterr().out
    assert 'KURSPRÜFUNG' in out and 'jede Minute geprüft' in out


def test_cockpit_says_every_minute(alpaca63, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v63.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v63.html').read_text()
    assert 'Der Stop wird jede Minute gegen diesen Kurs geprüft' in page
    assert 'alle 5 Minuten' not in page
    assert 'data-stall-min="6"' in page


# ---------- Google Drive mount ----------

def test_drive_mount_moves_local_leftovers_aside(tmp_path, capsys):
    mountpoint = tmp_path / 'drive'
    leftover = mountpoint / 'MyDrive' / 'Trading_Bot' / 'cockpit_v63_latest.html'
    leftover.parent.mkdir(parents=True)
    leftover.write_text('alt')
    mounted = []
    bot.drive_einbinden(mountpoint, mount=mounted.append)
    assert mounted == [str(mountpoint)] and not mountpoint.exists()
    [aside] = [p for p in tmp_path.iterdir() if p.name.startswith('drive_lokal_')]
    assert (aside / 'MyDrive' / 'Trading_Bot' / 'cockpit_v63_latest.html').read_text() == 'alt'
    assert 'Verschoben nach' in capsys.readouterr().out


@pytest.mark.parametrize('prepare', ['missing', 'empty'])
def test_drive_mount_without_leftovers(tmp_path, capsys, prepare):
    mountpoint = tmp_path / 'drive'
    if prepare == 'empty':
        mountpoint.mkdir()
    mounted = []
    bot.drive_einbinden(mountpoint, mount=mounted.append)
    assert mounted == [str(mountpoint)]
    assert [p.name for p in tmp_path.iterdir()] == ([] if prepare == 'missing' else ['drive'])
    assert capsys.readouterr().out == ''


def test_start_uses_the_safe_mount():
    source = inspect.getsource(bot.start)
    assert 'drive_einbinden()' in source and 'drive.mount' not in source
