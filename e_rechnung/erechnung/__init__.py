"""E-Rechnungen (XRechnung 3.0 UBL/CII, ZUGFeRD/Factur-X EN 16931) aus JSON erzeugen.

    from erechnung import create_invoice, load_profile
    result = create_invoice(daten, fmt="xrechnung-ubl", profile=load_profile("firma.json"))
    if result.ok:
        result.save("ausgabe/")
    else:
        print(result.errors)
"""
from .api import Result, create_invoice, load_profile, merge
from .checks import FORMATS, iban_valid, leitweg_id_valid
from .model import InputError, parse_invoice

__all__ = ["FORMATS", "InputError", "Result", "create_invoice", "iban_valid", "leitweg_id_valid",
           "load_profile", "merge", "parse_invoice"]
__version__ = "1.0.0"
