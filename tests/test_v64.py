"""V6.4: strategy C (VWAP + EMA + RSI on 15m bars, long only) next to the unchanged strategies A and B."""
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import crypto_bot_v63 as bot_v63
import crypto_bot_v64 as bot
import crypto_cockpit_v64 as cockpit_ui
from alpaca_fake_v6 import FakeAlpacaV6, frame, no_wait

ROOT = Path(__file__).resolve().parents[1]
ORDER_CALLS = ('submit_order', 'close_position', 'close_all_positions', 'cancel_order',
               'replace_order', 'TradingClient(', 'OrderRequest')
NEW = {'C_EMA': 20, 'C_RSI_PERIODE': 14, 'C_RSI_GRENZE': 50, 'C_VWAP_ANLAUF_KERZEN': 4,
       'C_MIN_15M_KERZEN': 100, 'C_STOP_ATR': 2.0}
UNCHANGED_FUNCTIONS = ('indikatoren_1h', 'marktregime', 'pullback_pruefen', 'squeeze_pruefen',
                       'zustand_a_aktualisieren', 'zustand_b_aktualisieren', 'signal_15m_pruefen',
                       'signal_a_pruefen', 'signal_b_pruefen', 'stundenanalyse', 'ausstieg_pruefen',
                       'risk_gate', 'offenes_risiko_berechnen', 'virtueller_kauf', 'virtueller_verkauf',
                       'trailing_stop_berechnen', 'trailing_stop_aktualisieren', 'positionen_ueberwachen',
                       'tageslimit_pruefen', 'strategie_auswertung', 'ein_durchlauf', 'marktdaten_pruefen')
NOON = pd.Timestamp('2026-09-24 12:00', tz='UTC')


@pytest.fixture
def alpaca64(tmp_path, monkeypatch):
    fake = FakeAlpacaV6()
    monkeypatch.setattr(bot, 'DATENQUELLE', fake)
    monkeypatch.setattr(bot, 'HANDELSPAARE', {c: i['symbol'] for c, i in bot.COINS.items()})
    monkeypatch.setattr(bot, 'ALPACA_KONTO', None)
    bot.speicherorte_einrichten(tmp_path / 'Trading_Bot')
    bot.EREIGNISSE.clear()
    bot.KURSFEHLER.clear()
    bot.SPREADS.clear()
    return fake


def falling_then(last_jump, n=150, step=0.9998, end=None):
    """15m candles drifting down (below VWAP, EMA and RSI 50); the last CLOSED one moves by `last_jump`."""
    closes = 100 * step ** np.arange(n)
    closes[-2] = closes[-3] * last_jump
    closes[-1] = closes[-2]
    return frame(closes, '15min', spread=0.001, end=end)


def rising_then(last_drop, n=150, step=1.0002, end=None):
    closes = 100 * step ** np.arange(n)
    closes[-2] = closes[-3] * last_drop
    closes[-1] = closes[-2]
    return frame(closes, '15min', spread=0.001, end=end)


def closed(df):
    df = df.iloc[:-1].copy()
    df['ohne_handel'] = False
    return df


def c_frame(raw):
    return bot.indikatoren_15m(closed(raw))


# ---------- what changed, what did not ----------

def test_version_and_new_parameters():
    assert bot.VERSION == '6.4'
    for name, value in NEW.items():
        assert getattr(bot, name) == value, name
    assert bot.STRATEGIE_NAMEN == {**bot_v63.STRATEGIE_NAMEN, 'C': 'C · VWAP + RSI'}
    assert bot.GRUND_TEXTE == {**bot_v63.GRUND_TEXTE,
                               'GEGENSIGNAL': 'C: Gegensignal (unter VWAP und EMA, RSI < 50)'}
    assert bot.BLOCKER_STUFEN == ('daten', 'a', 'b', 'signal_a', 'signal_b', 'signal_c', 'risk_gate')


def test_all_other_parameters_unchanged():
    def settings(module):
        return {k: v for k, v in vars(module).items()
                if k.isupper() and isinstance(v, (int, float, str, tuple, dict))
                and k not in NEW and k not in ('VERSION', 'STRATEGIE_NAMEN', 'GRUND_TEXTE', 'BLOCKER_STUFEN')
                and k not in ('SPREADS', 'KURSFEHLER', 'HANDELSPAARE', 'ALPACA_KONTO', 'DATENQUELLE')
                and k not in ('DATEN_FEHLERTYPEN', 'ZEITRAEUME')     # hold classes/objects of each module
                and not k.endswith(('_DATEI', '_ORDNER'))}
    assert settings(bot) == settings(bot_v63)
    assert bot.ZEITRAEUME.keys() == bot_v63.ZEITRAEUME.keys()
    assert [t.__name__ for t in bot.DATEN_FEHLERTYPEN] == [t.__name__ for t in bot_v63.DATEN_FEHLERTYPEN]


@pytest.mark.parametrize('name', UNCHANGED_FUNCTIONS)
def test_strategies_a_b_risk_and_exits_unchanged(name):
    assert inspect.getsource(getattr(bot, name)) == inspect.getsource(getattr(bot_v63, name))


def test_own_files(tmp_path):
    bot.speicherorte_einrichten(tmp_path)
    assert bot.KONTO_DATEI.name == 'konto_v64_alpaca_usd.json'
    assert bot.BACKUP_DATEI.name == 'konto_v64_alpaca_usd_backup.json'
    assert bot.STRATEGIE_DATEI.name == 'strategie_zustand_v64.json'
    assert bot.ANALYSE_DATEI.name == 'marktlage_v64.json'
    assert bot.STATISTIK_DATEI.name == 'blocker_statistik_v64.json'
    assert bot.MARKTDATEN_ORDNER.name == 'Marktdaten_Alpaca_v6'


@pytest.mark.parametrize('module', [bot, cockpit_ui])
def test_modules_contain_no_order_calls(module):
    text = Path(module.__file__).read_text(encoding='utf-8')
    for forbidden in ORDER_CALLS:
        assert forbidden not in text


def test_smoke_test_passes():
    bot.smoke_test()


# ---------- indicators ----------

def test_rsi_extremes_and_warmup():
    up = bot.rsi_berechnen(pd.Series(np.arange(40.0)))
    assert up.iloc[:14].isna().all() and up.iloc[14:].eq(100).all()
    assert bot.rsi_berechnen(pd.Series(-np.arange(40.0))).iloc[-1] == 0
    assert bot.rsi_berechnen(pd.Series(np.full(40, 5.0))).iloc[-1] == 50
    zigzag = bot.rsi_berechnen(pd.Series(np.tile([10.0, 11.0], 200)))
    assert zigzag.iloc[-1] == pytest.approx(50, abs=2)


def test_rsi_matches_wilder_by_hand():
    closes = pd.Series(100 + np.cumsum(np.random.default_rng(7).normal(0, 1, 120)))
    gain = loss = None
    for i, d in enumerate(closes.diff().iloc[1:], start=1):
        g, l_ = max(d, 0), max(-d, 0)
        gain = g if gain is None else gain + (g - gain) / 14
        loss = l_ if loss is None else loss + (l_ - loss) / 14
    assert bot.rsi_berechnen(closes).iloc[-1] == pytest.approx(100 - 100 / (1 + gain / loss))


def test_vwap_restarts_at_midnight_utc_and_ignores_quiet_candles():
    index = pd.date_range('2026-09-23 23:15', periods=6, freq='15min', tz='UTC')
    df = pd.DataFrame({'high': [11, 21, 31, 41, 51, 61.0], 'low': [9, 19, 29, 39, 49, 59.0],
                       'close': [10, 20, 30, 40, 50, 60.0], 'volume': [1, 3, 1, 1, 0, 2.0]}, index=index)
    vwap, candles = bot.vwap_berechnen(df)
    assert vwap.iloc[2] == pytest.approx((10 + 60 + 30) / 5)   # 23:15-23:45: weighted by volume
    assert vwap.iloc[3] == 40                                  # 00:00 UTC: new day, new VWAP
    assert vwap.iloc[4] == 40                                  # no trades: unchanged
    assert vwap.iloc[5] == pytest.approx((40 + 120) / 3)
    assert candles.tolist() == [1, 2, 3, 1, 2, 3]


# ---------- strategy C: entry ----------

def test_signal_when_all_three_conditions_come_together():
    df = c_frame(falling_then(1.02, end=NOON))
    werte, grund = bot.signal_c_pruefen(df)
    assert grund == 'Signal C'
    assert werte['schluss'] > werte['vwap'] and werte['schluss'] > werte['ema'] and werte['rsi'] > 50
    assert bot.bedingungen_c(df.iloc[-2]) == (False, True)     # the candle before: still below


def test_no_signal_while_conditions_already_hold():
    df = c_frame(rising_then(1.001, end=NOON))
    assert bot.bedingungen_c(df.iloc[-1])[0] and bot.bedingungen_c(df.iloc[-2])[0]
    assert bot.signal_c_pruefen(df) == (None, 'Bedingungen schon vorher erfüllt')


@pytest.mark.parametrize('column, value, grund', [
    ('VWAP', 1e9, 'Schluss nicht über VWAP'),
    ('EMA_C', 1e9, 'Schluss nicht über EMA 20'),
    ('RSI', 50.0, 'RSI nicht über 50'),
])
def test_each_condition_is_required(column, value, grund):
    df = c_frame(falling_then(1.02, end=NOON))
    df.iloc[-1, df.columns.get_loc(column)] = value
    assert bot.signal_c_pruefen(df) == (None, grund)


@pytest.mark.parametrize('end, ok', [('2026-09-24 01:00', False), ('2026-09-24 01:15', True)])
def test_no_signal_in_the_first_hour_after_the_vwap_restart(end, ok):
    # `end` is the running candle: the last closed one is the 4th resp. 5th of the UTC day
    df = c_frame(falling_then(1.02, end=pd.Timestamp(end, tz='UTC')))
    grund = bot.signal_c_pruefen(df)[1]
    assert (grund == 'Signal C') is ok, grund
    if not ok:
        assert grund == 'VWAP-Anlauf nach Tagesbeginn (00:00 UTC)'


def test_data_checks():
    assert bot.signal_c_pruefen(c_frame(falling_then(1.02, n=90, end=NOON)))[1] == 'zu wenige 15-Min-Kerzen'
    df = closed(falling_then(1.02, end=NOON))
    df.iloc[-1, df.columns.get_loc('ohne_handel')] = True
    assert bot.signal_c_pruefen(bot.indikatoren_15m(df))[1] == 'letzte 15-Min-Kerze ohne Handel'
    df = closed(falling_then(1.02, end=NOON))
    df.iloc[-10:-4, df.columns.get_loc('ohne_handel')] = True
    assert bot.signal_c_pruefen(bot.indikatoren_15m(df))[1] == 'zu wenig Handel in den 15-Min-Kerzen'


# ---------- strategy C: exit on the opposite signal ----------

def test_opposite_signal_only_after_the_purchase():
    df = c_frame(rising_then(0.98, end=NOON))
    assert bot.bedingungen_c(df.iloc[-1]) == (False, True)
    before = (NOON - timedelta(hours=1)).isoformat()
    assert bot.gegensignal_c_pruefen(df, before) == (True, 'Gegensignal')
    # bought right after the last closed candle (on its signal): that candle is no exit signal
    just_bought = (NOON + timedelta(seconds=20)).isoformat()
    assert bot.gegensignal_c_pruefen(df, just_bought) == (False, 'noch keine Kerze nach dem Kauf')
    assert bot.gegensignal_c_pruefen(c_frame(rising_then(1.001, end=NOON)), before) == (False, 'kein Gegensignal')


def c_lauf(atr=1.0):
    lauf = bot.neuer_lauf()
    lauf['atr'] = {'bitcoin': atr}
    lauf['regime'] = {'bitcoin': 'NEUTRAL'}
    return lauf


def test_entry_goes_through_the_risk_gate(alpaca64):
    alpaca64.frames[('BTC/USD', '15min')] = falling_then(1.02)
    bot.SPREADS['bitcoin'] = 0.0005
    kurs = float(alpaca64.frames[('BTC/USD', '15min')]['close'].iloc[-2])
    konto = bot.konto_laden()
    lauf = c_lauf(atr=1.0)
    lauf['strategien'] = {}
    # the VWAP restart rule depends on the time of day; here the candle pattern is what matters
    bot.C_VWAP_ANLAUF_KERZEN, alt = 0, bot.C_VWAP_ANLAUF_KERZEN
    try:
        bot.signalpruefung(konto, {'bitcoin': kurs}, lauf)
    finally:
        bot.C_VWAP_ANLAUF_KERZEN = alt
    position = bot.konto_laden()['positionen']['bitcoin']
    assert position['strategie'] == 'C'
    assert position['stop_loss'] == pytest.approx(position['einstieg'] - 2.0)
    assert position['rsi_bei_einstieg'] > 50 and position['vwap_bei_einstieg'] < kurs
    assert lauf['statistik']['signal_c'] == {'Signal C': 1}
    assert lauf['statistik']['risk_gate'] == {'Freigegeben': 1}
    assert 'C: Signal C → virtuell gekauft' in lauf['marktlage']['bitcoin']['signal']['text']
    assert lauf['marktlage']['bitcoin']['c']['long'] is True
    # other coins have no hourly data in this run: nothing fetched, nothing checked
    assert {call[1] for call in alpaca64.calls if call[0] == 'bars'} == {'BTC/USD'}


def test_c_position_is_sold_on_the_opposite_signal(alpaca64):
    alpaca64.frames[('BTC/USD', '15min')] = rising_then(0.98)
    kurs = float(alpaca64.frames[('BTC/USD', '15min')]['close'].iloc[-2])
    konto = bot.konto_laden()
    pruefung, _ = bot.risk_gate(konto, 'bitcoin', {'bitcoin': kurs * 1.02}, 1.0, spread=0.0005)
    pruefung['zusatz'] = {'strategie': 'C'}
    bot.virtueller_kauf(konto, 'bitcoin', pruefung)
    konto['positionen']['bitcoin']['kaufzeit'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    bot.konto_speichern(konto)
    lauf = c_lauf()
    bot.C_VWAP_ANLAUF_KERZEN, alt = 0, bot.C_VWAP_ANLAUF_KERZEN
    try:
        bot.signalpruefung(konto, {'bitcoin': kurs}, lauf)
    finally:
        bot.C_VWAP_ANLAUF_KERZEN = alt
    konto = bot.konto_laden()
    assert 'bitcoin' not in konto['positionen']
    verkauf = konto['transaktionen'][-1]
    assert (verkauf['aktion'], verkauf['grund'], verkauf['strategie']) == ('VERKAUF', 'GEGENSIGNAL', 'C')
    assert 'C: Gegensignal → virtuell verkauft' in lauf['marktlage']['bitcoin']['signal']['text']
    assert 'signal_c' not in lauf['statistik']        # exits are not entry checks
    assert bot.strategie_auswertung(konto['transaktionen'])['C']['gruende'] == {'GEGENSIGNAL': 1}


def test_a_position_is_not_closed_by_the_c_opposite_signal(alpaca64):
    alpaca64.frames[('BTC/USD', '15min')] = rising_then(0.98)
    kurs = float(alpaca64.frames[('BTC/USD', '15min')]['close'].iloc[-2])
    konto = bot.konto_laden()
    pruefung, _ = bot.risk_gate(konto, 'bitcoin', {'bitcoin': kurs * 1.02}, 1.0, spread=0.0005)
    pruefung['zusatz'] = {'strategie': 'A'}
    bot.virtueller_kauf(konto, 'bitcoin', pruefung)
    konto['positionen']['bitcoin']['kaufzeit'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    bot.signalpruefung(konto, {'bitcoin': kurs}, c_lauf())
    assert 'bitcoin' in bot.konto_laden()['positionen']


def test_end_to_end_buy_with_strategy_c(alpaca64, monkeypatch):
    monkeypatch.setattr(bot, 'C_VWAP_ANLAUF_KERZEN', 0)
    alpaca64.frames[('BTC/USD', '15min')] = falling_then(1.02)
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    konto = json.loads(bot.KONTO_DATEI.read_text())
    assert list(konto['positionen']) == ['bitcoin']
    assert konto['positionen']['bitcoin']['strategie'] == 'C'
    # every coin with valid hourly data is checked for C; the default fake data rise steadily
    statistik = json.loads(bot.STATISTIK_DATEI.read_text())
    assert statistik['signal_c'] == {'Signal C': 1, 'Bedingungen schon vorher erfüllt': len(bot.COINS) - 1}
    assert lauf['marktlage']['ethereum']['c']['long'] is True


# ---------- cockpit and notebook ----------

def test_cockpit_shows_strategy_c(alpaca64, monkeypatch, tmp_path):
    monkeypatch.setattr(bot, 'C_VWAP_ANLAUF_KERZEN', 0)
    alpaca64.frames[('BTC/USD', '15min')] = falling_then(1.02)
    cockpit = cockpit_ui.Cockpit(display_enabled=False, latest_path=tmp_path / 'v64.html')
    bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    page = (tmp_path / 'v64.html').read_text()
    assert 'V6.4' in page and 'Strategien A + B + C' in page
    assert 'C · VWAP + RSI (15 Min)' in page and 'über VWAP und EMA, RSI &gt; 50' in page
    assert 'C · 15-Min-Signal (VWAP, EMA, RSI)' in page and 'Signal C: 1' in page
    assert 'C · VWAP + RSI' in page                       # open position with its strategy
    assert 'Neue Käufe: 15-Min-Signal (A, B oder C) und Risk Gate' in page


def test_cockpit_cell_without_c_values():
    assert 'nicht geprüft' in cockpit_ui.strategy_c_cell(None)
    cell = cockpit_ui.strategy_c_cell({'short': True, 'rsi': 41.234, 'vwap': 10.0})
    assert 'unter VWAP und EMA, RSI &lt; 50' in cell and '41,2' in cell


def test_notebook_cells_use_v64():
    notebook = json.loads((ROOT / 'TRADING_BOT_V6_4_Live_Demo.ipynb').read_text(encoding='utf-8'))
    cells = [''.join(c['source']) for c in notebook['cells']]
    assert cells[0].startswith('# Trading Bot V6.4')
    code = [c for c in cells[1:] if not c.startswith('%%writefile')]
    assert all('v63' not in c for c in code)
    cockpit_cell = next(c for c in code if 'open_browser_view' in c)
    assert 'import crypto_bot_v64 as bot' in cockpit_cell and 'cockpit_v64_latest.html' in cockpit_cell
    assert any('bot.start(' in c and 'import crypto_bot_v64 as bot' in c for c in code)
    assert cockpit_ui.TAB_NAME == 'krypto-cockpit-v64'
