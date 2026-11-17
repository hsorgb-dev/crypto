"""Datenmodell einer E-Rechnung nach EN 16931 und Einlesen aus JSON.

Die Feldnamen sind englisch und lehnen sich an die Business Terms (BT-xx)
der EN 16931 an. Fehler beim Einlesen werden gesammelt und gemeinsam als
``InputError`` gemeldet, damit ein KI-Werkzeug alle fehlenden Angaben auf
einmal beim Nutzer erfragen kann.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

VAT_CATEGORIES = {
    "S": "Normalsatz / ermäßigter Satz",
    "Z": "Nullsatz",
    "E": "Steuerbefreit",
    "AE": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)",
    "K": "Innergemeinschaftliche Lieferung/Leistung",
    "G": "Ausfuhr in Drittland",
    "O": "Nicht steuerbar",
    "L": "Kanarische Inseln (IGIC)",
    "M": "Ceuta/Melilla (IPSI)",
}

# Vorgabetexte für Kategorien, die einen Befreiungsgrund verlangen (BR-xx-10).
DEFAULT_EXEMPTIONS = {
    "AE": ("Steuerschuldnerschaft des Leistungsempfängers gemäß § 13b UStG (Reverse Charge)", "VATEX-EU-AE"),
    "K": ("Steuerfreie innergemeinschaftliche Lieferung gemäß § 4 Nr. 1b UStG", "VATEX-EU-IC"),
    "G": ("Steuerfreie Ausfuhrlieferung gemäß § 4 Nr. 1a UStG", "VATEX-EU-G"),
    "O": ("Nicht im Inland steuerbare Leistung", "VATEX-EU-O"),
}
KLEINUNTERNEHMER_TEXT = "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet (Kleinunternehmerregelung)."

# Deutsche Einheiten → UN/ECE Recommendation 20.
UNIT_ALIASES = {
    "stk": "H87", "stk.": "H87", "stück": "H87", "stueck": "H87", "st": "H87",
    "std": "HUR", "std.": "HUR", "h": "HUR", "stunde": "HUR", "stunden": "HUR",
    "min": "MIN", "minute": "MIN", "minuten": "MIN",
    "tag": "DAY", "tage": "DAY", "tg": "DAY",
    "woche": "WEE", "wochen": "WEE",
    "monat": "MON", "monate": "MON", "mon": "MON",
    "jahr": "ANN", "jahre": "ANN",
    "kg": "KGM", "g": "GRM", "t": "TNE",
    "m": "MTR", "km": "KMT", "cm": "CMT", "mm": "MMT",
    "m2": "MTK", "m²": "MTK", "qm": "MTK", "m3": "MTQ", "m³": "MTQ",
    "l": "LTR", "liter": "LTR",
    "pauschal": "LS", "pausch.": "LS", "psch": "LS", "psch.": "LS", "pauschale": "LS",
    "set": "SET", "paar": "PR", "kwh": "KWH", "stück(e)": "H87",
}
UNIT_LABELS = {
    "H87": "Stk", "C62": "Stk", "HUR": "Std", "MIN": "Min", "DAY": "Tag", "WEE": "Woche",
    "MON": "Monat", "ANN": "Jahr", "KGM": "kg", "GRM": "g", "TNE": "t", "MTR": "m",
    "KMT": "km", "CMT": "cm", "MMT": "mm", "MTK": "m²", "MTQ": "m³", "LTR": "l",
    "LS": "pausch.", "SET": "Set", "PR": "Paar", "KWH": "kWh",
}

TYPE_CODES = {
    "380": "Rechnung",
    "381": "Gutschrift (Rechnungskorrektur)",
    "384": "Korrigierte Rechnung",
    "326": "Teilrechnung",
    "389": "Selbst ausgestellte Rechnung (Gutschriftverfahren)",
    "875": "Teilschlussrechnung (Bau)",
    "876": "Abschlagsrechnung (Bau)",
    "877": "Schlussrechnung (Bau)",
}


class InputError(ValueError):
    """Die Eingabedaten lassen sich nicht in eine Rechnung umwandeln."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class Address:
    city: str | None
    postal_code: str | None
    country_code: str = "DE"
    street: str | None = None
    additional: str | None = None
    subdivision: str | None = None


@dataclass
class Contact:
    name: str | None = None
    phone: str | None = None
    email: str | None = None


@dataclass
class Party:
    name: str
    address: Address
    trading_name: str | None = None
    id: str | None = None
    vat_id: str | None = None
    tax_number: str | None = None
    registration_id: str | None = None
    legal_info: str | None = None
    electronic_address: str | None = None
    electronic_address_scheme: str | None = None
    contact: Contact | None = None


@dataclass
class Payment:
    means_code: str = "58"
    iban: str | None = None
    bic: str | None = None
    account_name: str | None = None
    bank_name: str | None = None
    remittance_info: str | None = None
    mandate_id: str | None = None
    creditor_id: str | None = None
    debited_iban: str | None = None
    card_number: str | None = None
    card_holder: str | None = None
    info: str | None = None


@dataclass
class AllowanceCharge:
    is_charge: bool
    amount: Decimal
    reason: str | None = None
    reason_code: str | None = None
    vat_category: str | None = None
    vat_rate: Decimal | None = None
    base_amount: Decimal | None = None
    percent: Decimal | None = None


@dataclass
class Line:
    id: str
    name: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    vat_category: str
    vat_rate: Decimal
    price_base_quantity: Decimal = Decimal(1)
    description: str | None = None
    seller_item_id: str | None = None
    buyer_item_id: str | None = None
    note: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    order_line_ref: str | None = None
    allowances: list[AllowanceCharge] = field(default_factory=list)
    charges: list[AllowanceCharge] = field(default_factory=list)


@dataclass
class Note:
    text: str
    subject_code: str | None = None


@dataclass
class Skonto:
    days: int
    percent: Decimal
    base_amount: Decimal | None = None


@dataclass
class Invoice:
    number: str
    issue_date: date
    seller: Party
    buyer: Party
    lines: list[Line]
    payment: Payment | None
    type_code: str = "380"
    currency: str = "EUR"
    due_date: date | None = None
    buyer_reference: str | None = None
    order_reference: str | None = None
    contract_reference: str | None = None
    project_reference: str | None = None
    preceding_invoice_number: str | None = None
    preceding_invoice_date: date | None = None
    delivery_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    delivery_party: Party | None = None
    notes: list[Note] = field(default_factory=list)
    payment_terms: str | None = None
    skonto: list[Skonto] = field(default_factory=list)
    allowances: list[AllowanceCharge] = field(default_factory=list)
    charges: list[AllowanceCharge] = field(default_factory=list)
    prepaid_amount: Decimal = Decimal(0)
    rounding_amount: Decimal = Decimal(0)
    vat_exemptions: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    @property
    def is_credit_note(self) -> bool:
        return self.type_code == "381"


# --------------------------------------------------------------------------
# Einlesen
# --------------------------------------------------------------------------

class _Reader:
    """Liest Werte aus verschachtelten dicts und sammelt Fehler mit Pfadangabe."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    def text(self, obj: dict, key: str, path: str, required: bool = False) -> str | None:
        value = obj.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                self.err(f"{path}.{key}" if path else key, "Angabe fehlt")
            return None
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            self.err(f"{path}.{key}" if path else key, "muss ein Text sein")
            return None
        return value.strip()

    def dec(self, obj: dict, key: str, path: str, required: bool = False,
            default: Decimal | None = None) -> Decimal | None:
        value = obj.get(key)
        where = f"{path}.{key}" if path else key
        if value is None or value == "":
            if required:
                self.err(where, "Angabe fehlt")
            return default
        if isinstance(value, bool):
            self.err(where, "muss eine Zahl sein")
            return default
        if isinstance(value, str):
            value = value.strip().replace(" ", "")
            # Deutsches Format "1.234,56" zulassen.
            if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", value):
                value = value.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"-?\d+,\d+", value):
                value = value.replace(",", ".")
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            self.err(where, f"'{obj.get(key)}' ist keine Zahl")
            return default
        if not result.is_finite():
            self.err(where, "muss eine endliche Zahl sein")
            return default
        return result

    def date(self, obj: dict, key: str, path: str, required: bool = False) -> date | None:
        value = obj.get(key)
        where = f"{path}.{key}" if path else key
        if value is None or value == "":
            if required:
                self.err(where, "Angabe fehlt")
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            value = value.strip()
            m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value)
            try:
                if m:
                    return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
        self.err(where, f"'{value}' ist kein gültiges Datum (JJJJ-MM-TT oder TT.MM.JJJJ)")
        return None

    def obj(self, obj: dict, key: str, path: str, required: bool = False) -> dict | None:
        value = obj.get(key)
        where = f"{path}.{key}" if path else key
        if value is None:
            if required:
                self.err(where, "Angabe fehlt")
            return None
        if not isinstance(value, dict):
            self.err(where, "muss ein Objekt sein")
            return None
        return value

    def items(self, obj: dict, key: str, path: str) -> list:
        value = obj.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            self.err(f"{path}.{key}" if path else key, "muss eine Liste sein")
            return []
        return value


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return "H87"
    key = unit.strip().lower()
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    return unit.strip().upper()


def _address(r: _Reader, data: dict | None, path: str) -> Address:
    data = data or {}
    country = (r.text(data, "country_code", path) or "DE").upper()
    return Address(
        street=r.text(data, "street", path),
        additional=r.text(data, "additional", path),
        city=r.text(data, "city", path),
        postal_code=r.text(data, "postal_code", path),
        country_code=country,
        subdivision=r.text(data, "subdivision", path),
    )


def _party(r: _Reader, data: dict, path: str) -> Party:
    contact_data = r.obj(data, "contact", path)
    contact = None
    if contact_data:
        contact = Contact(
            name=r.text(contact_data, "name", f"{path}.contact"),
            phone=r.text(contact_data, "phone", f"{path}.contact"),
            email=r.text(contact_data, "email", f"{path}.contact"),
        )
    ea = data.get("electronic_address")
    ea_value, ea_scheme = None, None
    if isinstance(ea, dict):
        ea_value = r.text(ea, "value", f"{path}.electronic_address")
        ea_scheme = r.text(ea, "scheme", f"{path}.electronic_address")
    elif ea is not None:
        ea_value = r.text(data, "electronic_address", path)
    return Party(
        name=r.text(data, "name", path, required=True) or "",
        address=_address(r, r.obj(data, "address", path, required=True), f"{path}.address"),
        trading_name=r.text(data, "trading_name", path),
        id=r.text(data, "id", path),
        vat_id=(r.text(data, "vat_id", path) or "").replace(" ", "").upper() or None,
        tax_number=r.text(data, "tax_number", path),
        registration_id=r.text(data, "registration_id", path),
        legal_info=r.text(data, "legal_info", path),
        electronic_address=ea_value,
        electronic_address_scheme=ea_scheme,
        contact=contact,
    )


def _allowance_charges(r: _Reader, data: dict, key: str, path: str, is_charge: bool,
                       default_cat: str, default_rate: Decimal, line_level: bool = False,
                       line_base: Decimal | None = None) -> list[AllowanceCharge]:
    result = []
    for i, item in enumerate(r.items(data, key, path)):
        p = f"{path}.{key}[{i}]"
        if not isinstance(item, dict):
            r.err(p, "muss ein Objekt sein")
            continue
        percent = r.dec(item, "percent", p)
        base = r.dec(item, "base_amount", p)
        if percent is not None and base is None and line_base is not None:
            base = line_base
        amount = r.dec(item, "amount", p)
        if amount is None:
            if percent is not None and base is not None:
                amount = (base * percent / 100).quantize(Decimal("0.01"), "ROUND_HALF_UP")
            else:
                r.err(p, "amount oder percent + base_amount angeben")
                continue
        if percent is not None and base is None:
            r.err(p, "Bei percent wird auch base_amount benötigt")
        rate = r.dec(item, "vat_rate", p)
        cat = (r.text(item, "vat_category", p) or "").upper() or None
        result.append(AllowanceCharge(
            is_charge=is_charge,
            amount=amount,
            reason=r.text(item, "reason", p),
            reason_code=r.text(item, "reason_code", p),
            vat_category=None if line_level else (cat or default_cat),
            vat_rate=None if line_level else (rate if rate is not None else default_rate),
            base_amount=base,
            percent=percent,
        ))
    return result


def _note(r: _Reader, item, path: str) -> Note | None:
    if isinstance(item, str):
        return Note(item.strip()) if item.strip() else None
    if isinstance(item, dict):
        text = r.text(item, "text", path, required=True)
        return Note(text, r.text(item, "subject_code", path)) if text else None
    r.err(path, "Hinweis muss Text oder {text, subject_code} sein")
    return None


def parse_invoice(data: dict, today: date | None = None) -> Invoice:
    """Wandelt ein dict (z. B. aus JSON) in eine ``Invoice`` um.

    Wirft ``InputError`` mit allen gefundenen Problemen.
    """
    if not isinstance(data, dict):
        raise InputError(["Die Rechnung muss ein JSON-Objekt sein"])
    r = _Reader()
    today = today or date.today()

    seller_data = r.obj(data, "seller", "", required=True) or {}
    buyer_data = r.obj(data, "buyer", "", required=True) or {}
    seller = _party(r, seller_data, "seller")
    buyer = _party(r, buyer_data, "buyer")
    kleinunternehmer = bool(seller_data.get("kleinunternehmer"))

    default_rate = r.dec(data, "default_vat_rate", "", default=Decimal(19))
    default_cat = (r.text(data, "default_vat_category", "") or "").upper() or None
    if kleinunternehmer:
        default_rate, default_cat = Decimal(0), "E"
    if default_cat is None:
        default_cat = "S" if default_rate else "Z"

    issue_date = r.date(data, "issue_date", "") or today

    lines: list[Line] = []
    raw_lines = r.items(data, "lines", "")
    if not raw_lines:
        r.err("lines", "Mindestens eine Rechnungsposition angeben")
    for i, item in enumerate(raw_lines):
        p = f"lines[{i}]"
        if not isinstance(item, dict):
            r.err(p, "muss ein Objekt sein")
            continue
        qty = r.dec(item, "quantity", p, default=Decimal(1))
        price = r.dec(item, "unit_price", p, required=True)
        base_qty = r.dec(item, "price_base_quantity", p, default=Decimal(1))
        if base_qty is not None and base_qty <= 0:
            r.err(f"{p}.price_base_quantity", "muss größer als 0 sein")
            base_qty = Decimal(1)
        rate = r.dec(item, "vat_rate", p)
        cat = (r.text(item, "vat_category", p) or "").upper() or None
        if kleinunternehmer:
            cat, rate = cat or "E", rate if rate is not None else Decimal(0)
        if rate is None:
            rate = Decimal(0) if cat in ("E", "Z", "AE", "K", "G", "O") else default_rate
        if cat is None:
            cat = default_cat if rate == default_rate else ("S" if rate else None)
            if cat is None:
                r.err(f"{p}.vat_category", "Bei 0 % USt bitte Kategorie angeben (Z, E, AE, K, G oder O)")
                cat = "Z"
        line_base = None
        if qty is not None and price is not None:
            line_base = (qty * price / base_qty).quantize(Decimal("0.01"), "ROUND_HALF_UP")
        lines.append(Line(
            id=r.text(item, "id", p) or str(i + 1),
            name=r.text(item, "name", p, required=True) or "",
            quantity=qty if qty is not None else Decimal(1),
            unit_code=normalize_unit(r.text(item, "unit", p) or r.text(item, "unit_code", p)),
            unit_price=price if price is not None else Decimal(0),
            price_base_quantity=base_qty,
            vat_category=cat,
            vat_rate=rate,
            description=r.text(item, "description", p),
            seller_item_id=r.text(item, "seller_item_id", p),
            buyer_item_id=r.text(item, "buyer_item_id", p),
            note=r.text(item, "note", p),
            period_start=r.date(item, "period_start", p),
            period_end=r.date(item, "period_end", p),
            order_line_ref=r.text(item, "order_line_ref", p),
            allowances=_allowance_charges(r, item, "allowances", p, False, cat, rate, True, line_base),
            charges=_allowance_charges(r, item, "charges", p, True, cat, rate, True, line_base),
        ))

    # Zahlung
    payment = None
    pay = r.obj(data, "payment", "")
    if pay:
        payment = Payment(
            means_code=r.text(pay, "means_code", "payment") or "58",
            iban=(r.text(pay, "iban", "payment") or "").replace(" ", "").upper() or None,
            bic=(r.text(pay, "bic", "payment") or "").replace(" ", "").upper() or None,
            account_name=r.text(pay, "account_name", "payment"),
            bank_name=r.text(pay, "bank_name", "payment"),
            remittance_info=r.text(pay, "remittance_info", "payment"),
            mandate_id=r.text(pay, "mandate_id", "payment"),
            creditor_id=r.text(pay, "creditor_id", "payment"),
            debited_iban=(r.text(pay, "debited_iban", "payment") or "").replace(" ", "").upper() or None,
            card_number=r.text(pay, "card_number", "payment"),
            card_holder=r.text(pay, "card_holder", "payment"),
            info=r.text(pay, "info", "payment"),
        )

    due_date = r.date(data, "due_date", "")
    payment_days = r.dec(data, "payment_days", "")
    payment_terms = r.text(data, "payment_terms", "")
    if due_date is None and payment_days is not None:
        due_date = issue_date + timedelta(days=int(payment_days))
    if payment_terms is None and payment_days is not None and due_date is not None:
        payment_terms = (f"Zahlbar innerhalb von {int(payment_days)} Tagen ohne Abzug "
                         f"bis zum {due_date:%d.%m.%Y}.")

    skonto = []
    for i, item in enumerate(r.items(data, "skonto", "")):
        p = f"skonto[{i}]"
        if not isinstance(item, dict):
            r.err(p, "muss ein Objekt sein")
            continue
        days = r.dec(item, "days", p, required=True)
        pct = r.dec(item, "percent", p, required=True)
        if days is not None and pct is not None:
            skonto.append(Skonto(int(days), pct, r.dec(item, "base_amount", p)))

    notes = []
    for i, item in enumerate(r.items(data, "notes", "")):
        n = _note(r, item, f"notes[{i}]")
        if n:
            notes.append(n)

    exemptions: dict[str, tuple[str | None, str | None]] = {}
    raw_ex = data.get("vat_exemptions")
    if isinstance(raw_ex, str):
        exemptions["*"] = (raw_ex, None)
    elif isinstance(raw_ex, dict):
        for cat, val in raw_ex.items():
            if isinstance(val, str):
                exemptions[cat.upper()] = (val, None)
            elif isinstance(val, dict):
                exemptions[cat.upper()] = (val.get("reason"), val.get("code"))
    elif raw_ex is not None:
        r.err("vat_exemptions", "muss Text oder Objekt {Kategorie: Grund} sein")
    if kleinunternehmer and "E" not in exemptions:
        exemptions["E"] = (KLEINUNTERNEHMER_TEXT, None)

    delivery_party = None
    delivery = r.obj(data, "delivery", "")
    delivery_date = r.date(data, "delivery_date", "")
    if delivery:
        delivery_date = delivery_date or r.date(delivery, "date", "delivery")
        if delivery.get("address"):
            delivery_party = Party(
                name=r.text(delivery, "name", "delivery") or "",
                address=_address(r, r.obj(delivery, "address", "delivery"), "delivery.address"),
            )

    period = r.obj(data, "billing_period", "") or {}
    preceding = r.obj(data, "preceding_invoice", "") or {}

    invoice = Invoice(
        number=r.text(data, "invoice_number", "", required=True) or "",
        issue_date=issue_date,
        type_code=r.text(data, "type_code", "") or "380",
        currency=(r.text(data, "currency", "") or "EUR").upper(),
        seller=seller,
        buyer=buyer,
        lines=lines,
        payment=payment,
        due_date=due_date,
        buyer_reference=r.text(data, "buyer_reference", ""),
        order_reference=r.text(data, "order_reference", ""),
        contract_reference=r.text(data, "contract_reference", ""),
        project_reference=r.text(data, "project_reference", ""),
        preceding_invoice_number=r.text(preceding, "number", "preceding_invoice"),
        preceding_invoice_date=r.date(preceding, "date", "preceding_invoice"),
        delivery_date=delivery_date,
        period_start=r.date(period, "start", "billing_period"),
        period_end=r.date(period, "end", "billing_period"),
        delivery_party=delivery_party,
        notes=notes,
        payment_terms=payment_terms,
        skonto=skonto,
        allowances=_allowance_charges(r, data, "allowances", "", False, default_cat, default_rate),
        charges=_allowance_charges(r, data, "charges", "", True, default_cat, default_rate),
        prepaid_amount=r.dec(data, "prepaid_amount", "", default=Decimal(0)),
        rounding_amount=r.dec(data, "rounding_amount", "", default=Decimal(0)),
        vat_exemptions=exemptions,
    )
    if r.errors:
        raise InputError(r.errors)
    return invoice


def exemption_for(invoice: Invoice, category: str) -> tuple[str | None, str | None]:
    """Befreiungsgrund (Text, Code) für eine USt-Kategorie; None bei S/Z."""
    if category in ("S", "Z", "L", "M"):
        return None, None
    if category in invoice.vat_exemptions:
        return invoice.vat_exemptions[category]
    if "*" in invoice.vat_exemptions:
        return invoice.vat_exemptions["*"]
    return DEFAULT_EXEMPTIONS.get(category, (None, None))
