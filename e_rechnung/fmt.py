"""German number, amount and date formatting for the PDF and the HTML view."""
from .model import money, plain


def de_number(value, digits=2):
    text = f'{value:,.{digits}f}'
    return text.replace(',', 'X').replace('.', ',').replace('X', '.')


def symbol(currency):
    return {'EUR': '€', 'USD': '$', 'GBP': '£'}.get(currency, currency)


def de_amount(value, currency='EUR'):
    return f'{de_number(money(value))} {symbol(currency)}'


def de_price(value, currency='EUR'):
    """Unit price: two decimals, or up to four when the price has them."""
    if value.as_tuple().exponent < -2:
        return f'{de_quantity(value)} {symbol(currency)}'
    return de_amount(value, currency)


def de_quantity(value):
    text = plain(value)
    sign = '-' if text.startswith('-') else ''
    whole, _, decimals = text.lstrip('-').partition('.')
    return sign + de_number(int(whole), 0) + (',' + decimals if decimals else '')


def de_date(value):
    return value.strftime('%d.%m.%Y') if value else ''


def de_rate(rate):
    return '—' if rate is None else f'{de_quantity(rate)} %'


def iban_groups(iban):
    return ' '.join(iban[i:i + 4] for i in range(0, len(iban), 4))
