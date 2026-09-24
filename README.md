# crypto

Kraken-Paper-Trading-Bot für BTC, ETH und SOL in EUR, als Google-Colab-Notebook. Er handelt nur virtuell und sendet keine echten Orders.

| Datei | Inhalt |
|---|---|
| `TRADING_BOT_V4_1_Cockpit.ipynb` | **Aktuelle Version.** Die Trendfolge-Strategie aus V4.0, dazu ein Cockpit (Colab-Ansicht mit Vollbild-Link, Browser-Tab, `cockpit_latest.html`) und zwei Fehlerkorrekturen. Details stehen in der ersten Zelle. |
| `TRADING_BOT_V4_0.ipynb` | Vorgängerversion V4.0 als Referenz, unverändert. |
| `tools/notebook_modules.py` | Holt die `%%writefile`-Module aus dem Notebook heraus und schreibt sie wieder zurück. |
| `tests/` | pytest-Tests gegen die Module genau so, wie das Notebook sie schreibt. Sie laufen offline, Kraken wird dabei simuliert. |

## Tests

```bash
pip install pandas requests pytest
python -m pytest -q
```

Module bearbeiten:

```bash
python tools/notebook_modules.py extract TRADING_BOT_V4_1_Cockpit.ipynb /tmp/mods
# /tmp/mods/*.py bearbeiten
python tools/notebook_modules.py update TRADING_BOT_V4_1_Cockpit.ipynb /tmp/mods
```
