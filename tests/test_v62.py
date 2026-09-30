"""V6.2: fee 0.25 %, initial risk (R) per trade, evaluation per strategy in the cockpit."""
import inspect
import json
from pathlib import Path

import pytest

import crypto_bot_v61 as bot_v61
import crypto_bot_v62 as bot
import crypto_cockpit_v62 as cockpit_ui
from alpaca_fake_v6 import FakeAlpacaV6, no_wait

ROOT = Path(__file__).resolve().parents[1]
ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')
STRATEGY_FUNCTIONS = ('indikatoren_1h', 'marktregime', 'pullback_pruefen', 'squeeze_pruefen',
                      'zustand_a_aktualisieren', 'zustand_b_aktualisieren', 'signal_15m_pruefen',
                      'signal_a_pruefen', 'signal_b_pruefen', 'stundenanalyse', 'ausstieg_pruefen',
                      'signalpruefung', 'risk_gate', 'offenes_risiko_berechnen')


@pytest.fixture
def alpaca62(tmp_path, monkeypatch):
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

def test_fee_is_alpaca_taker_fee():
    assert bot.GEBUEHR == 0.0025
    assert bot.SLIPPAGE == bot_v61.SLIPPAGE == 0.002
    assert bot.VERSION == '6.2'


def test_all_other_parameters_unchanged():
    def settings(module):
        return {k: v for k, v in vars(module).items()
                if k.isupper() and isinstance(v, (int, float, str, tuple, dict))
                and k not in ('GEBUEHR', 'VERSION')
                # objects without value equality (exception classes, alpaca TimeFrame)
                and k not in ('DATEN_FEHLERTYPEN', 'ZEITRAEUME')
                # runtime state filled while the bot runs, not settings
                and k not in ('SPREADS', 'KURSFEHLER', 'HANDELSPAARE', 'ALPACA_KONTO', 'DATENQUELLE')
                and not k.endswith(('_DATEI', '_ORDNER'))}
    assert settings(bot) == settings(bot_v61)


@pytest.mark.parametrize('name', STRATEGY_FUNCTIONS)
def test_strategy_code_unchanged(name):
    assert inspect.getsource(getattr(bot, name)) == inspect.getsource(getattr(bot_v61, name))


def test_own_account_files(tmp_path):
    bot.speicherorte_einrichten(tmp_path)
    assert bot.KONTO_DATEI.name == 'konto_v62_alpaca_usd.json'
    assert bot.BACKUP_DATEI.name == 'konto_v62_alpaca_usd_backup.json'
    assert bot.STRATEGIE_DATEI.name == 'strategie_zustand_v62.json'
    assert bot.ANALYSE_DATEI.name == 'marktlage_v62.json'


@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    text = Path(module.__file__).read_text(encoding='utf-8')
    for forbidden in ORDER_CALLS:
        assert forbidden not in text


# ---------- initial risk and R multiple ----------

def buy(konto, coin='bitcoin', kurs=100.0, atr=2.0):
    pruefung, grund = bot.risk_gate(konto, coin, {coin: kurs}, atr, spread=0.0005)
    assert pruefung is not None, grund
    assert bot.virtueller_kauf(konto, coin, pruefung)
    return pruefung


def test_selling_at_the_initial_stop_is_minus_one_r(alpaca62):
    konto = bot.neues_konto()
    pruefung = buy(konto)
    position = konto['positionen']['bitcoin']
    assert position['anfangsrisiko'] == pytest.approx(pruefung['geplantes_risiko'])
    kauf = konto['transaktionen'][-1]
    assert kauf['aktion'] == 'KAUF' and kauf['anfangsrisiko'] == pytest.approx(pruefung['geplantes_risiko'])

    bot.virtueller_verkauf(konto, 'bitcoin', position['stop_loss'], 'STOP')
    verkauf = konto['transaktionen'][-1]
    assert verkauf['r_vielfaches'] == pytest.approx(-1.0)
    assert verkauf['gewinn'] == pytest.approx(-pruefung['geplantes_risiko'])


def test_r_multiple_survives_a_moved_stop(alpaca62):
    konto = bot.neues_konto()
    buy(konto)
    position = konto['positionen']['bitcoin']
    risiko = position['anfangsrisiko']
    position['stop_loss'] = position['einstieg'] * 1.05   # trailing stop moved above entry
    bot.virtueller_verkauf(konto, 'bitcoin', position['einstieg'] * 1.10, 'STOP')
    verkauf = konto['transaktionen'][-1]
    assert verkauf['anfangsrisiko'] == pytest.approx(risiko)
    assert verkauf['r_vielfaches'] == pytest.approx(verkauf['gewinn'] / risiko)
    assert verkauf['r_vielfaches'] > 1


def test_position_without_initial_risk_has_no_r(alpaca62):
    konto = bot.neues_konto()
    buy(konto)
    del konto['positionen']['bitcoin']['anfangsrisiko']    # e.g. copied from an older account
    bot.virtueller_verkauf(konto, 'bitcoin', 101.0, 'TRENDBRUCH')
    assert konto['transaktionen'][-1]['r_vielfaches'] is None


def test_fee_is_part_of_the_initial_risk(alpaca62):
    konto = bot.neues_konto()
    pruefung = buy(konto)
    einstieg, stop = pruefung['einstieg'], pruefung['stop_loss']
    je_einheit = einstieg * (1 + 0.0025) - stop * (1 - 0.002) * (1 - 0.0025)
    assert pruefung['geplantes_risiko'] == pytest.approx(pruefung['menge'] * je_einheit)


# ---------- evaluation per strategy ----------

def sale(strategie, gewinn, grund, r=None):
    return dict(aktion='VERKAUF', strategie=strategie, gewinn=gewinn, grund=grund, r_vielfaches=r)


def test_strategy_evaluation():
    stats = bot.strategie_auswertung([
        dict(aktion='KAUF', strategie='A'),
        sale('A', 30.0, 'TRENDBRUCH', 0.3),
        sale('A', -100.0, 'STOP', -1.0),
        sale('A', 250.0, 'STOP', 2.5),
        sale('B', -50.0, 'FEHLAUSBRUCH', -0.5),
        sale('B', 10.0, 'ZEITSTOP', None),
    ])
    assert list(stats) == ['A', 'B']
    a, b = stats['A'], stats['B']
    assert (a['trades'], a['gewinner']) == (3, 2)
    assert a['trefferquote'] == pytest.approx(2 / 3)
    assert a['ergebnis'] == pytest.approx(180.0) and a['durchschnitt'] == pytest.approx(60.0)
    assert a['r_durchschnitt'] == pytest.approx(0.6) and a['r_anzahl'] == 3
    assert a['gruende'] == {'TRENDBRUCH': 1, 'STOP': 2}
    assert b['r_anzahl'] == 1 and b['r_durchschnitt'] == pytest.approx(-0.5)
    assert bot.strategie_auswertung([]) == {}


def test_cockpit_shows_the_evaluation(alpaca62, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v62.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    konto = json.loads(bot.KONTO_DATEI.read_text())
    konto['transaktionen'] += [sale('A', 40.0, 'TRENDBRUCH', 0.4), sale('A', -100.0, 'STOP', -1.0),
                               sale('B', 15.0, 'ZEITSTOP', 0.15)]
    lauf = dict(konto=konto, kurse={}, analyse=[])
    page = cockpit_ui.render_html(cockpit_ui.snapshot(lauf, 'WARTET', []))
    assert 'Auswertung je Strategie' in page
    assert '50 %' in page and '−0,30 R' not in page and '-0,30 R' in page    # A: (0.4 - 1.0) / 2
    assert 'A: Stundenschluss unter EMA 50: 1' in page and 'Stop ausgelöst: 1' in page
    assert 'B: Zeitstop ohne Fortschritt: 1' in page
    assert 'Gebühr 0,25 %' in page
    assert 'etwa 30 Trades' in page


def test_cockpit_evaluation_empty(alpaca62, tmp_path):
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v62.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v62.html').read_text()
    assert 'Auswertung je Strategie' in page and 'V6.2' in page


def test_notebook_cockpit_cell():
    notebook = json.loads((ROOT / 'TRADING_BOT_V6_2_Live_Demo.ipynb').read_text(encoding='utf-8'))
    cells = [''.join(c['source']) for c in notebook['cells'] if c['cell_type'] == 'code']
    cockpit_cell = next(c for c in cells if 'open_browser_view' in c and not c.startswith('%%writefile'))
    assert 'import crypto_bot_v62 as bot' in cockpit_cell
    assert 'cockpit_v62_latest.html' in cockpit_cell
    assert 'COCKPIT_AUTOMATISCH_OEFFNEN = True' in cockpit_cell
    assert cockpit_ui.open_browser_view.__defaults__ == (8770, True)
