"""Code lists from EN 16931 / XRechnung, with German labels and the aliases the input accepts."""

XRECHNUNG_ID = 'urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0'
EN16931_ID = 'urn:cen.eu:en16931:2017'
PEPPOL_PROCESS = 'urn:fdc:peppol.eu:2017:poacc:billing:01:1.0'

# Invoice type code (BT-3), restricted to the codes XRechnung allows (BR-DE-17).
TYPES = {
    '380': 'Rechnung',
    '381': 'Rechnungskorrektur / Gutschrift (Credit Note)',
    '384': 'Korrigierte Rechnung',
    '389': 'Gutschrift (Selbstfakturierung)',
    '326': 'Teilrechnung',
    '875': 'Abschlagsrechnung (Bau)',
    '876': 'Teilschlussrechnung (Bau)',
    '877': 'Schlussrechnung (Bau)',
}
TYPE_TITLES = {'380': 'Rechnung', '381': 'Rechnungskorrektur', '384': 'Korrigierte Rechnung',
               '389': 'Gutschrift', '326': 'Teilrechnung', '875': 'Abschlagsrechnung',
               '876': 'Teilschlussrechnung', '877': 'Schlussrechnung'}
TYPE_ALIASES = {
    'rechnung': '380', 'invoice': '380',
    'gutschrift': '389', 'selbstfakturierung': '389',
    'rechnungskorrektur': '381', 'storno': '381', 'stornorechnung': '381', 'credit_note': '381',
    'creditnote': '381', 'korrektur': '384', 'korrigierte_rechnung': '384',
    'teilrechnung': '326', 'abschlagsrechnung': '875', 'teilschlussrechnung': '876',
    'schlussrechnung': '877',
}

# VAT category code (BT-151 / BT-118). L and M (Canary Islands, Ceuta/Melilla) are left out.
VAT_CATEGORIES = ['S', 'Z', 'E', 'AE', 'K', 'G', 'O']
VAT_CATEGORY_LABELS = {
    'S': 'Normalsatz / ermäßigter Satz',
    'Z': 'Nullsatz',
    'E': 'Steuerbefreit (z. B. § 4 UStG, Kleinunternehmer § 19 UStG)',
    'AE': 'Umkehr der Steuerschuldnerschaft (Reverse Charge, § 13b UStG)',
    'K': 'Innergemeinschaftliche Lieferung',
    'G': 'Ausfuhr in Drittland',
    'O': 'Nicht steuerbar',
    'L': 'Kanarische Inseln (IGIC)',
    'M': 'Ceuta und Melilla (IPSI)',
}
ALL_VAT_CATEGORIES = VAT_CATEGORIES + ['L', 'M']   # L and M are only read from received invoices
VAT_CATEGORY_ALIASES = {
    'standard': 'S', 'normal': 'S', 'null': 'Z', 'nullsatz': 'Z', 'zero': 'Z',
    'befreit': 'E', 'steuerfrei': 'E', 'exempt': 'E', 'kleinunternehmer': 'E',
    'reverse_charge': 'AE', 'reverse-charge': 'AE', '13b': 'AE',
    'innergemeinschaftlich': 'K', 'ig': 'K', 'export': 'G', 'ausfuhr': 'G',
    'nicht_steuerbar': 'O', 'not_subject': 'O',
}
# Exemption reason code (BT-121) and German text (BT-120) used when the input gives no own text.
EXEMPTIONS = {
    'E': ('', ''),
    'AE': ('VATEX-EU-AE', 'Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)'),
    'K': ('VATEX-EU-IC', 'Steuerfreie innergemeinschaftliche Lieferung'),
    'G': ('VATEX-EU-G', 'Steuerfreie Ausfuhrlieferung'),
    'O': ('VATEX-EU-O', 'Nicht steuerbare Leistung'),
}
SMALL_BUSINESS_REASON = 'Gemäß § 19 UStG wird keine Umsatzsteuer berechnet (Kleinunternehmerregelung).'

# Payment means (BT-81).
PAYMENT_MEANS = {
    '58': 'SEPA-Überweisung',
    '30': 'Überweisung (nicht SEPA)',
    '59': 'SEPA-Lastschrift',
    '10': 'Barzahlung',
    '1': 'Nicht definiert',
    '97': 'Verrechnung',
    'ZZZ': 'Sonstige',
}
CREDIT_TRANSFER = ('58', '30')
DIRECT_DEBIT = ('59',)
PAYMENT_ALIASES = {
    'ueberweisung': '58', 'überweisung': '58', 'sepa': '58', 'transfer': '58',
    'lastschrift': '59', 'direct_debit': '59', 'sepa_lastschrift': '59',
    'bar': '10', 'cash': '10', 'verrechnung': '97',
}

# Unit of measure (BT-130), UN/ECE Recommendation 20 / 21, the ones invoices usually need.
UNITS = {
    'H87': 'Stück', 'C62': 'Einheit', 'HUR': 'Stunde', 'MIN': 'Minute', 'DAY': 'Tag',
    'WEE': 'Woche', 'MON': 'Monat', 'ANN': 'Jahr', 'LS': 'Pauschale', 'SET': 'Satz',
    'KGM': 'kg', 'GRM': 'g', 'TNE': 't', 'MTR': 'm', 'KMT': 'km', 'MTK': 'm²', 'MTQ': 'm³',
    'LTR': 'l', 'KWH': 'kWh', 'XPK': 'Packung', 'XPP': 'Packstück', 'P1': 'Prozent',
}
UNIT_ALIASES = {
    'stk': 'H87', 'stück': 'H87', 'stueck': 'H87', 'st': 'H87', 'piece': 'H87', 'pcs': 'H87',
    'einheit': 'C62', 'unit': 'C62', 'x': 'C62',
    'h': 'HUR', 'std': 'HUR', 'std.': 'HUR', 'stunde': 'HUR', 'stunden': 'HUR', 'hour': 'HUR',
    'min': 'MIN', 'minute': 'MIN', 'minuten': 'MIN',
    'tag': 'DAY', 'tage': 'DAY', 'day': 'DAY', 'woche': 'WEE', 'wochen': 'WEE',
    'monat': 'MON', 'monate': 'MON', 'month': 'MON', 'jahr': 'ANN', 'jahre': 'ANN', 'year': 'ANN',
    'pauschal': 'LS', 'pauschale': 'LS', 'psch': 'LS', 'psch.': 'LS', 'lump_sum': 'LS',
    'satz': 'SET', 'kg': 'KGM', 'g': 'GRM', 't': 'TNE', 'tonne': 'TNE', 'm': 'MTR', 'meter': 'MTR',
    'km': 'KMT', 'm2': 'MTK', 'm²': 'MTK', 'qm': 'MTK', 'm3': 'MTQ', 'm³': 'MTQ', 'cbm': 'MTQ',
    'l': 'LTR', 'liter': 'LTR', 'kwh': 'KWH', 'packung': 'XPK', '%': 'P1', 'prozent': 'P1',
}

COUNTRY_ALIASES = {
    'deutschland': 'DE', 'germany': 'DE', 'österreich': 'AT', 'oesterreich': 'AT', 'austria': 'AT',
    'schweiz': 'CH', 'switzerland': 'CH', 'frankreich': 'FR', 'niederlande': 'NL', 'belgien': 'BE',
    'luxemburg': 'LU', 'italien': 'IT', 'spanien': 'ES', 'polen': 'PL', 'dänemark': 'DK',
    'tschechien': 'CZ',
}
EU_COUNTRIES = {'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI', 'FR', 'GR', 'HR', 'HU',
                'IE', 'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK'}
