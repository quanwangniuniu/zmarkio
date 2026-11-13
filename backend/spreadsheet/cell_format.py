"""Web-editor cell formats shared by the Excel and Google exporters.

The web editor stores formatting as SpreadsheetCellFormat fields: bold,
italic, strikethrough, text_color, font_family, font_size and number_format.
This module holds the lookup tables every exporter shares, plus
FormatStrategy, which applies those fields to an export target with one
strategy function per field.
"""

FONT_NAMES = {
    'Arial, sans-serif': 'Arial',
    'Helvetica, Arial, sans-serif': 'Helvetica',
    'Georgia, serif': 'Georgia',
    '"Times New Roman", Times, serif': 'Times New Roman',
    'monospace': 'Consolas',
}

CURRENCY_LOCALES = {
    'USD': '[$$-409]',
    'GBP': '[$£-809]',
    'EUR': '[$€-407]',
    'JPY': '[$¥-411]',
}


def font_name(value):
    """Excel/Google font name for a web font_family value, or None when unsupported."""
    if value is None:
        return None
    return FONT_NAMES.get(str(value).strip())


def number_pattern(places):
    """Grouped number pattern, e.g. #,##0 or #,##0.00."""
    return '#,##0' if places == 0 else '#,##0.' + ('0' * places)


def percent_pattern(places):
    """Percent pattern, e.g. 0% or 0.00%."""
    return '0%' if places == 0 else '0.' + ('0' * places) + '%'


class FormatStrategy:
    """Apply web format fields to an export target, one strategy function per field.

    Subclasses set ``strategies`` to {web field: function(target, value) -> bool}.
    A function returns True when it changed the target. Fields run in the order
    of ``strategies`` (Google field masks depend on it). Fields with no strategy
    are ignored.

    Rebuild callers pass every field the web cell has. Patch callers pass only
    the fields the diff changed, so an untouched field is never written and the
    original file keeps its formatting.
    """

    strategies = {}

    @classmethod
    def apply(cls, target, fields) -> bool:
        changed = False
        for field, strategy in cls.strategies.items():
            if field in fields and strategy(target, fields[field]):
                changed = True
        return changed







