"""Beträge einer Rechnung nach den Rechenregeln der EN 16931 (BR-CO-xx)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .model import Invoice, Line, exemption_for

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class VatGroup:
    category: str
    rate: Decimal
    taxable: Decimal
    tax: Decimal
    exemption_reason: str | None = None
    exemption_code: str | None = None


@dataclass
class Totals:
    line_total: Decimal            # BT-106 Summe der Positionsnettobeträge
    allowance_total: Decimal       # BT-107 Summe der Abschläge auf Belegebene
    charge_total: Decimal          # BT-108 Summe der Zuschläge auf Belegebene
    tax_basis_total: Decimal       # BT-109 Gesamtbetrag ohne USt
    tax_total: Decimal             # BT-110 Summe USt
    grand_total: Decimal           # BT-112 Gesamtbetrag mit USt
    prepaid: Decimal               # BT-113 Bereits gezahlt
    rounding: Decimal              # BT-114 Rundungsbetrag
    due_payable: Decimal           # BT-115 Fälliger Betrag
    vat_groups: list[VatGroup] = field(default_factory=list)
    line_net: dict[str, Decimal] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "line_total": str(self.line_total),
            "allowance_total": str(self.allowance_total),
            "charge_total": str(self.charge_total),
            "net_total": str(self.tax_basis_total),
            "vat_total": str(self.tax_total),
            "gross_total": str(self.grand_total),
            "prepaid": str(self.prepaid),
            "amount_due": str(self.due_payable),
            "vat_breakdown": [
                {"category": g.category, "rate": str(g.rate), "taxable": str(g.taxable), "tax": str(g.tax),
                 "exemption_reason": g.exemption_reason}
                for g in self.vat_groups
            ],
        }


def line_net_amount(line: Line) -> Decimal:
    """BT-131 = Menge × (Preis / Basismenge) + Zuschläge − Abschläge."""
    base = line.quantity * line.unit_price / line.price_base_quantity
    base += sum((c.amount for c in line.charges), Decimal(0))
    base -= sum((a.amount for a in line.allowances), Decimal(0))
    return money(base)


def compute(invoice: Invoice) -> Totals:
    line_net = {line.id: line_net_amount(line) for line in invoice.lines}
    line_total = money(sum(line_net.values(), Decimal(0)))
    allowance_total = money(sum((a.amount for a in invoice.allowances), Decimal(0)))
    charge_total = money(sum((c.amount for c in invoice.charges), Decimal(0)))
    tax_basis = line_total - allowance_total + charge_total

    groups: dict[tuple[str, Decimal], Decimal] = {}
    for line in invoice.lines:
        key = (line.vat_category, line.vat_rate.normalize())
        groups[key] = groups.get(key, Decimal(0)) + line_net[line.id]
    for ac in invoice.allowances + invoice.charges:
        key = (ac.vat_category, ac.vat_rate.normalize())
        sign = 1 if ac.is_charge else -1
        groups[key] = groups.get(key, Decimal(0)) + sign * money(ac.amount)

    vat_groups = []
    for (cat, rate), taxable in sorted(groups.items(), key=lambda kv: (kv[0][0] != "S", -kv[0][1], kv[0][0])):
        taxable = money(taxable)
        reason, code = exemption_for(invoice, cat)
        vat_groups.append(VatGroup(cat, rate, taxable, money(taxable * rate / 100), reason, code))

    tax_total = money(sum((g.tax for g in vat_groups), Decimal(0)))
    grand_total = tax_basis + tax_total
    prepaid = money(invoice.prepaid_amount)
    rounding = money(invoice.rounding_amount)
    return Totals(
        line_total=line_total,
        allowance_total=allowance_total,
        charge_total=charge_total,
        tax_basis_total=money(tax_basis),
        tax_total=tax_total,
        grand_total=money(grand_total),
        prepaid=prepaid,
        rounding=rounding,
        due_payable=money(grand_total - prepaid + rounding),
        vat_groups=vat_groups,
        line_net=line_net,
    )
