"""V4.1 trading logic: the two V4.0 fixes, a full offline pass, and the cockpit hook."""
import json

import pytest

import crypto_bot_v41 as bot
from kraken_fake import kraken, no_wait  # noqa: F401  (fixture)


def position(einstieg=60000.0, stop=58000.0, hoechster=60000.0, menge=0.05):
    return dict(menge=menge, einstieg=einstieg, stop_loss=stop, atr_bei_einstieg=500.0,
                hoechster_schlusskurs=hoechster, gesamtkosten=menge * einstieg * (1 + bot.GEBUEHR),
                kaufzeit=bot.zeitstempel(), datenquelle='Kraken')


def saved_account():
    return json.loads(bot.KONTO_DATEI.read_text())


def test_smoke_test_passes():
    bot.smoke_test()


def test_module_import_has_no_side_effects():
    # Colab/Drive and Kraken are only touched by start().
    source = open(bot.__file__, encoding='utf-8').read()
    assert 'from google.colab import drive' in source.split('def start(')[1]
    assert bot.HANDELSPAARE == {} or isinstance(bot.HANDELSPAARE, dict)


# ---------- fix 1: trailing stop above entry ----------

def test_stop_above_entry_is_a_valid_account(kraken):
    konto = bot.neues_konto()
    konto['positionen']['bitcoin'] = position(stop=61000.0)
    bot.konto_speichern(konto)          # V4.0: ValueError "Ungültige Kursmarken"
    assert bot.konto_laden()['positionen']['bitcoin']['stop_loss'] == 61000.0
    assert bot.offenes_risiko_berechnen(konto) == 0


def test_trailing_stop_can_rise_above_entry_without_stopping_the_bot(kraken):
    konto = bot.neues_konto()
    entry = kraken.last_close('bitcoin') * 0.8     # a trend that has run far since the entry
    konto['positionen']['bitcoin'] = position(einstieg=entry, stop=entry * 0.97, hoechster=entry)
    konto['guthaben'] -= konto['positionen']['bitcoin']['gesamtkosten']
    bot.konto_speichern(konto)
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    pos = saved_account()['positionen']['bitcoin']
    assert pos['stop_loss'] > pos['einstieg']
    assert lauf['letzter_fehler'] is None
    assert any(e['art'] == 'STOP_NACHGEZOGEN' for e in bot.EREIGNISSE)


# ---------- fix 2: highest close is kept even when the stop stays ----------

def test_new_highest_close_is_saved_when_stop_does_not_move(kraken):
    konto = bot.neues_konto()
    konto['positionen']['bitcoin'] = position(einstieg=60000.0, stop=59000.0, hoechster=60000.0)
    bot.konto_speichern(konto)
    # New close 60500 with a large ATR: 60500 - 3*1000 < 59000, the stop stays.
    bot.trailing_stop_aktualisieren(konto, 'bitcoin', 60500.0, 1000.0)
    pos = saved_account()['positionen']['bitcoin']
    assert pos['stop_loss'] == 59000.0
    assert pos['hoechster_schlusskurs'] == 60500.0


# ---------- full pass with the offline Kraken fake ----------

def test_uptrend_pass_buys_within_risk_limits(kraken):
    lauf = bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    konto = saved_account()
    assert konto['positionen'], 'the synthetic uptrend must produce a LONG entry'
    kapital = bot.kontowert_berechnen(konto, lauf['kurse'])
    assert bot.offenes_risiko_berechnen(konto) <= kapital * bot.MAX_GESAMTRISIKO + 1e-6
    assert [a['signal'] for a in lauf['analyse']] == ['LONG'] * 3
    assert json.loads(bot.ANALYSE_DATEI.read_text()) == lauf['analyse']
    assert {e['art'] for e in bot.EREIGNISSE} >= {'KAUF'}


def test_analysis_runs_once_per_hour(kraken):
    bot.hauptschleife(durchlaeufe=2, warten=no_wait)
    assert [c[0] for c in kraken.calls].count('OHLC') == 3


def test_saved_analysis_is_shown_after_restart(kraken):
    bot.hauptschleife(durchlaeufe=1, warten=no_wait)
    assert [a['coin'] for a in bot.neuer_lauf()['analyse']] == list(bot.COINS)


# ---------- cockpit hook ----------

class RecordingCockpit:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def update(self, lauf, stufe, ereignisse=()):
        self.calls.append((stufe, dict(lauf), list(ereignisse)))
        if self.fail:
            raise RuntimeError('broken view')


def comparable(konto):
    """Account content without wall-clock timestamps."""
    konto = json.loads(json.dumps(konto))
    for t in konto['transaktionen']:
        t.pop('zeit')
    for p in konto['positionen'].values():
        p.pop('kaufzeit')
    konto.pop('erstellt_am')
    konto.pop('letzter_ausstieg')
    return konto


def run_with(cockpit, folder, fake, monkeypatch, durchlaeufe=2):
    monkeypatch.setattr(bot, 'kraken_anfrage', fake)
    bot.speicherorte_einrichten(folder)
    bot.EREIGNISSE.clear()
    lauf = bot.hauptschleife(cockpit=cockpit, durchlaeufe=durchlaeufe, warten=no_wait)
    return lauf, comparable(saved_account())


def test_cockpit_changes_nothing_but_the_view(tmp_path, kraken, monkeypatch):
    from kraken_fake import FakeKraken
    with_fake, without_fake = FakeKraken(), FakeKraken()
    cockpit = RecordingCockpit()
    _, with_account = run_with(cockpit, tmp_path / 'with', with_fake, monkeypatch)
    _, without_account = run_with(None, tmp_path / 'without', without_fake, monkeypatch)
    assert with_account == without_account
    assert with_fake.calls == without_fake.calls
    assert [c[0] for c in cockpit.calls] == ['WARTET', 'WARTET']


def test_broken_cockpit_never_changes_or_stops_trading(tmp_path, kraken, monkeypatch):
    from kraken_fake import FakeKraken
    cockpit = RecordingCockpit(fail=True)
    lauf, broken_account = run_with(cockpit, tmp_path / 'broken', FakeKraken(), monkeypatch)
    _, clean_account = run_with(None, tmp_path / 'clean', FakeKraken(), monkeypatch)
    assert broken_account == clean_account
    assert len(cockpit.calls) == 1            # switched off after the first failure
    assert lauf['cockpit_fehler'] == 'RuntimeError'
    assert lauf['durchlaeufe'] == 2


def test_api_error_is_reported_and_the_loop_continues(kraken):
    kraken.fail_ticker = True
    cockpit = RecordingCockpit()
    lauf = bot.hauptschleife(cockpit=cockpit, durchlaeufe=2, warten=no_wait)
    assert [c[0] for c in cockpit.calls] == ['API_FEHLER', 'API_FEHLER']
    assert lauf['api_fehler'] == 2 and 'nicht erreichbar' in lauf['letzter_fehler']
    assert saved_account()['positionen'] == {}


def test_program_error_paints_failed_view_and_stops(kraken, monkeypatch):
    def broken(*args):
        raise KeyError('kaputt')
    monkeypatch.setattr(bot, 'positionen_ueberwachen', broken)
    cockpit = RecordingCockpit()
    with pytest.raises(KeyError):
        bot.hauptschleife(cockpit=cockpit, durchlaeufe=1, warten=no_wait)
    assert cockpit.calls[-1][0] == 'FEHLER'
    assert 'kaputt' in cockpit.calls[-1][1]['letzter_fehler']


def test_manual_stop_paints_stopped_view(kraken):
    def interrupt(seconds):
        raise KeyboardInterrupt
    cockpit = RecordingCockpit()
    bot.hauptschleife(cockpit=cockpit, warten=interrupt)
    assert [c[0] for c in cockpit.calls] == ['WARTET', 'GESTOPPT']
