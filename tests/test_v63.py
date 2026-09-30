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
           'MAX_15M_OHNE_HANDEL': 5, 'B_MAX_STUNDEN_OHNE_HANDEL': 2}
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
                and not k.startswith(('BLOCKER_', 'A_STATUS', 'B_STATUS'))}
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
