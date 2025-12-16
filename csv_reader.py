from __future__ import annotations
import csv
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _normalize_value(v: str, null_values: Sequence[str]) -> Optional[str]:
    if v is None:
        return None
    v2 = v.strip()
    if v2 in null_values:
        return None
    return v2


def iter_csv_rows(
    path: str,
    delimiter: str,
    encoding: str,
    null_values: Sequence[str],
) -> Tuple[List[str], Iterable[Dict[str, Optional[str]]]]:
    """
    Ritorna (columns, iterator di dict) dove keys sono le colonne dal CSV header.
    """
    f = open(path, "r", encoding=encoding, newline="")
    reader = csv.DictReader(f, delimiter=delimiter)

    if not reader.fieldnames:
        f.close()
        raise ValueError("CSV senza intestazione (header) o vuoto.")

    columns = [c.strip() for c in reader.fieldnames]

    def gen():
        try:
            for row in reader:
                clean = {}
                for col in columns:
                    clean[col] = _normalize_value(row.get(col, ""), null_values)
                yield clean
        finally:
            f.close()

    return columns, gen()


def chunked(iterable: Iterable[Dict[str, Optional[str]]], size: int):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
