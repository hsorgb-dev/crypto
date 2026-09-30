# crypto

Krypto-Paper-Trading-Bot (Trendfolge) als Google-Colab-Notebook. Er handelt nur virtuell und sendet keine echten Orders.

| Datei | Inhalt |
|---|---|
| `TRADING_BOT_V6_3_Live_Demo.ipynb` | **Neueste Version.** Wie V6.2, aber mit mehr Einstiegen: Filter, die auf den dünnen Alpaca-Daten unnötig blockierten, sind gelockert (Volumenfilter, Datenprüfung, Handelspause 6 statt 24 h, Wartezeit auf das A-Signal 8 statt 4 h), dazu 3 weitere Coins. Neu im Cockpit: „Warum kein Trade?“ zählt, welche Prüfung Einstiege verhindert. Strategien, Stops und Risikomanagement unverändert. Eigenes virtuelles Konto. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V6_2_Live_Demo.ipynb` | Wie V6.1, aber mit realistischerer Gebühr (0,25 % statt 0,1 % je Seite, Alpaca-Taker) und einer Auswertung je Strategie im Cockpit (Trades, Trefferquote, Ergebnis, Ø R, Ausstiegsgründe). Eigenes virtuelles Konto. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V6_1_Live_Demo.ipynb` | V6 mit einer Änderung: Das Cockpit öffnet sich beim Start automatisch in einem eigenen Browser-Tab (einmalig Pop-ups für Colab erlauben; der Link bleibt als Rückfall). Strategie und Konto wie V6. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V6_Live_Demo.ipynb` | Live-Demo. Strategien A (Momentum mit Pullback-Einstieg) und B (Volatility Squeeze Breakout) auf Stunden- und 15-Minuten-Kerzen, echte Alpaca-Kurse, keine Orders, 10 Coins in USD. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V5_Alpaca.ipynb` | Trendfolge (MA/ADX) auf Alpaca-Daten. Gleiche Strategie wie V4.1, aber Kurse von Alpaca (nur lesend, keine Orders), 10 Coins gegen USD und ein virtuelles Budget von 10.000 USD. Cockpit wie V4.1. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V4_1_Cockpit.ipynb` | Kraken/EUR-Version. Die Trendfolge-Strategie aus V4.0, dazu ein Cockpit (Colab-Ansicht mit Vollbild-Link, Browser-Tab, `cockpit_latest.html`) und zwei Fehlerkorrekturen. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V4_0.ipynb` | Vorgängerversion V4.0 als Referenz, unverändert. |
| `tools/notebook_modules.py` | Holt die `%%writefile`-Module aus dem Notebook heraus und schreibt sie wieder zurück. |
| `tests/` | pytest-Tests gegen die Module genau so, wie das Notebook sie schreibt. Sie laufen offline, Kraken und Alpaca werden dabei simuliert. |

## Tests

```bash
pip install pandas requests pytest alpaca-py
python -m pytest -q
```

Module bearbeiten:

```bash
python tools/notebook_modules.py extract TRADING_BOT_V4_1_Cockpit.ipynb /tmp/mods
# /tmp/mods/*.py bearbeiten
python tools/notebook_modules.py update TRADING_BOT_V4_1_Cockpit.ipynb /tmp/mods
```
