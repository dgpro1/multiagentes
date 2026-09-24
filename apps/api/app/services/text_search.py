"""Text search that ignores case, accents and apostrophes.

Searching `gomez`, `GOMEZ` and `Gómez` all find "Gómez", and `obrien` finds
"O'Brien" or "O’Brien". Both sides fold text the same way: the Python side
(`fold`) for the search term and the SQL side (`sql_fold`) for the column, and
both are built from one table (`_ACCENTS`, `_DROPPED`) so they cannot drift.

The SQL side uses `translate()`, which is core Postgres, instead of the
`unaccent` extension: creating an extension needs a privilege that managed
databases often do not grant. Uppercase accented letters are mapped straight to
lowercase ASCII so the result does not depend on how the database locale's
`lower()` treats non-ASCII (a C-locale database would not lowercase `Á`).
"""

import unicodedata

from sqlalchemy import func

# Accented letter -> unaccented lowercase ASCII, both cases listed.
_ACCENTS: dict[str, str] = {}
for _base, _variants in {
    "a": "áàäâãåāăąÁÀÄÂÃÅĀĂĄ",
    "c": "çćčĉċÇĆČĈĊ",
    "d": "ďđĎĐ",
    "e": "éèëêēĕėęěÉÈËÊĒĔĖĘĚ",
    "g": "ğĝġģĞĜĠĢ",
    "h": "ĥħĤĦ",
    "i": "íìïîĩīĭįıÍÌÏÎĨĪĬĮ",
    "j": "ĵĴ",
    "k": "ķĶ",
    "l": "ĺļľŀłĹĻĽĿŁ",
    "n": "ñńņňÑŃŅŇ",
    "o": "óòöôõøōŏőÓÒÖÔÕØŌŎŐ",
    "r": "ŕŗřŔŖŘ",
    "s": "śŝşšŚŜŞŠ",
    "t": "ţťŧŢŤŦ",
    "u": "úùüûũūŭůűųÚÙÜÛŨŪŬŮŰŲ",
    "w": "ŵŴ",
    "y": "ýÿŷÝŸŶ",
    "z": "źżžŹŻŽ",
}.items():
    for _char in _variants:
        _ACCENTS[_char] = _base

# Apostrophe-like characters are removed, so "O'Brien" and "obrien" meet.
_DROPPED = "'’‘ʼ´`"

_SQL_FROM = "".join(_ACCENTS) + _DROPPED
_SQL_TO = "".join(_ACCENTS.values())  # shorter than FROM: the tail is deleted

_LIKE_ESCAPE = "\\"


def fold(text: str | None) -> str:
    """Lowercase, strip accents and drop apostrophes. `None` folds to ''.

    Only the letters in the table are unaccented, exactly what `sql_fold` does
    in the database, so a term always meets the column it was typed from.
    Input is composed first (NFC) so a letter typed as `o` + combining accent
    still meets its precomposed form.
    """
    if not text:
        return ""
    out: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        if char in _DROPPED:
            continue
        out.append(_ACCENTS.get(char, char))
    return "".join(out).lower()


def sql_fold(column):
    """The SQL expression matching `fold(column)`. NULL becomes ''."""
    return func.translate(func.lower(func.coalesce(column, "")), _SQL_FROM, _SQL_TO)


def _escape_like(term: str) -> str:
    return term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2).replace("%", _LIKE_ESCAPE + "%").replace("_", _LIKE_ESCAPE + "_")


def folded_like(column, term: str | None):
    """`column` contains `term`, ignoring case, accents and apostrophes."""
    return sql_fold(column).like(f"%{_escape_like(fold(term.strip() if term else term))}%", escape=_LIKE_ESCAPE)
