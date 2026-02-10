from __future__ import annotations

import re
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional


def _clean_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" else s


def parse_int(s: str, thousands_sep: str = ".") -> int:
    s = s.replace(" ", "")
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    return int(s)


def parse_decimal(
    s: str,
    decimal_sep: str = ",",
    thousands_sep: str = ".",
) -> Decimal:
    s = s.replace(" ", "")
    if thousands_sep:
        s = s.replace(thousands_sep, "")
    if decimal_sep and decimal_sep != ".":
        s = s.replace(decimal_sep, ".")
    return Decimal(s)


def parse_bit(s: str) -> int:
    s2 = s.strip().lower()
    if s2 in ("1", "true", "t", "yes", "y", "si", "sì"):
        return 1
    if s2 in ("0", "false", "f", "no", "n"):
        return 0
    return 1 if int(s2) != 0 else 0


# =========================
# Oracle-style datetime fix
# =========================

_MONTHS_EN = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

_ORACLE_DT_RE = re.compile(
    r"^(?P<dd>\d{2})-(?P<mon>[A-Z]{3})-(?P<yy>\d{2}) "
    r"(?P<hh>\d{2})\.(?P<mi>\d{2})\.(?P<ss>\d{2})\.(?P<frac>\d{1,9}) "
    r"(?P<ampm>AM|PM)$",
    re.IGNORECASE,
)


def _normalize_oracle_datetime(s: str) -> str:
    """
    Converte:
      09-FEB-26 05.35.09.941000000 AM
    in:
      2026-02-09 05:35:09.941000
    - mese ENG indipendente dal locale
    - frazioni tagliate/paddate a 6 cifre (microsecondi)
    """
    s = s.strip()
    m = _ORACLE_DT_RE.match(s)
    if not m:
        return s

    dd = m.group("dd")
    mon = m.group("mon").upper()
    yy = int(m.group("yy"))
    hh12 = int(m.group("hh"))
    mi = m.group("mi")
    ss = m.group("ss")
    frac = m.group("frac")
    ampm = m.group("ampm").upper()

    # Forzo anno 2000+ (tipico per log/KPI moderni)
    year = 2000 + yy

    # 12h -> 24h
    if ampm == "AM":
        hh24 = 0 if hh12 == 12 else hh12
    else:
        hh24 = hh12 if hh12 == 12 else hh12 + 12

    mm = _MONTHS_EN.get(mon)
    if mm is None:
        return s

    # 1..9 cifre -> microsecondi (6)
    frac6 = (frac + "000000")[:6]

    return f"{year:04d}-{mm}-{dd} {hh24:02d}:{mi}:{ss}.{frac6}"


# =========================
# Datetime parsing
# =========================

def parse_datetime(s: str, formats: Iterable[str]) -> datetime:
    """
    Parse robusto per date/ore:
    - supporta ms con ',' oppure '.'
    - supporta stringhe contaminate (estrae sottostringhe datetime)
    - supporta formati Oracle-like: '09-FEB-26 05.35.09.941000000 AM'
      (normalizzati in ISO con microsecondi)
    """
    s = s.strip()

    # 0) normalizza eventuale oracle datetime (mese ENG + nanos)
    s = _normalize_oracle_datetime(s)

    candidates = [s]

    patterns = [
        # ISO con secondi opzionali e frazioni
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?",
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?(?:[.,]\d{1,9})?",
        # IT con secondi opzionali e frazioni
        r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?(?:[.,]\d{1,9})?",
        r"\d{2}/\d{2}/\d{4}",
    ]

    for pat in patterns:
        for m in re.findall(pat, s):
            candidates.append(m)

    # dedupe mantenendo ordine
    seen = set()
    uniq = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)

    # helper: normalizza separatore ms e taglia a 6 cifre
    def _norm_frac(c: str) -> str:
        c = c.replace(",", ".")
        # se ci sono frazioni oltre 6 cifre, taglia a 6
        c = re.sub(r"(\.\d{6})\d+", r"\1", c)
        return c

    # 1) prova ISO first
    for c in uniq:
        try:
            return datetime.fromisoformat(_norm_frac(c))
        except Exception:
            pass

    # 2) prova formati espliciti
    for c in uniq:
        c2 = _norm_frac(c)
        for fmt in formats:
            try:
                return datetime.strptime(c2, fmt)
            except Exception:
                continue

    raise ValueError(f"Formato data/ora non riconosciuto: {s!r}")


def build_converters(
    col_types: Dict[str, str],
    decimal_sep: str = ",",
    thousands_sep: str = ".",
    datetime_formats: Optional[list[str]] = None,
):
    if datetime_formats is None:
        datetime_formats = [
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S.%f",
            "%d/%m/%Y %H:%M:%S,%f",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S,%f",
            "%Y-%m-%d",
        ]

    conv: Dict[str, Any] = {}

    for col, t in col_types.items():
        t2 = t.strip().lower()
        # ✅ NORMALIZZA: datetime2(3) -> datetime2, nvarchar(20) -> nvarchar, decimal(6,2) -> decimal
        t2 = re.sub(r"\(.*\)", "", t2).strip()

        if t2 in ("int", "bigint", "smallint", "tinyint"):
            conv[col] = lambda v, ts=thousands_sep: None if _clean_str(v) is None else parse_int(_clean_str(v), ts)

        elif t2 in ("decimal", "numeric", "money", "smallmoney", "float", "real"):
            conv[col] = lambda v, ds=decimal_sep, ts=thousands_sep: None if _clean_str(v) is None else parse_decimal(_clean_str(v), ds, ts)

        elif t2 in ("bit",):
            conv[col] = lambda v: None if _clean_str(v) is None else parse_bit(_clean_str(v))

        elif t2 in ("date",):
            conv[col] = lambda v, fmts=datetime_formats: None if _clean_str(v) is None else parse_datetime(_clean_str(v), fmts).date()

        elif t2 in ("datetime", "datetime2", "smalldatetime", "datetimeoffset"):
            conv[col] = lambda v, fmts=datetime_formats: None if _clean_str(v) is None else parse_datetime(_clean_str(v), fmts)

        else:
            conv[col] = lambda v: _clean_str(v)

    return conv


def convert_row(row: Dict[str, Any], converters: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if k in converters:
            try:
                out[k] = converters[k](v)
            except Exception as e:
                raise ValueError(f"Errore conversione colonna={k!r}, valore={v!r}: {e}") from e
        else:
            out[k] = v
    return out
