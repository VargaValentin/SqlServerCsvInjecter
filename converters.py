from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal, InvalidOperation
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
    # prova anche int
    return 1 if int(s2) != 0 else 0


def parse_datetime(s: str, formats: Iterable[str]) -> datetime:
    # prova ISO first (supporta anche "2025-12-16 11:39:45.836")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
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
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S.%f",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d",
        ]

    conv: Dict[str, Any] = {}

    for col, t in col_types.items():
        t2 = t.lower()

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
            # varchar/nvarchar/char/nchar/text ecc: lascio stringa
            conv[col] = lambda v: _clean_str(v)

    return conv


def convert_row(row: Dict[str, Any], converters: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if k in converters:
            out[k] = converters[k](v)
        else:
            out[k] = v
    return out
