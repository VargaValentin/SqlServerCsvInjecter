from __future__ import annotations
import logging
from typing import Dict, List, Optional, Sequence, Tuple

import pyodbc


def connect_sqlserver(conn_str: str, timeout_seconds: int, logger: logging.Logger) -> pyodbc.Connection:
    logger.info("Connessione a SQL Server...")
    cn = pyodbc.connect(conn_str, timeout=timeout_seconds)
    logger.info("Connessione OK.")
    return cn


def _quote_ident(name: str) -> str:
    # SQL Server: [col]
    return f"[{name.replace(']', ']]')}]"


def build_insert_statement(schema: str, table: str, columns: Sequence[str]) -> str:
    cols_sql = ", ".join(_quote_ident(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    full_table = f"{_quote_ident(schema)}.{_quote_ident(table)}"
    return f"INSERT INTO {full_table} ({cols_sql}) VALUES ({placeholders})"


def insert_batch(
    cn: pyodbc.Connection,
    insert_sql: str,
    columns: Sequence[str],
    rows: List[Dict[str, Optional[str]]],
    fast_executemany: bool,
    logger: logging.Logger,
    dry_run: bool = False,
) -> int:
    values: List[Tuple[Optional[str], ...]] = [
        tuple(r.get(c) for c in columns) for r in rows
    ]

    if dry_run:
        logger.info("[DRY RUN] Inserirei %d righe. SQL=%s", len(values), insert_sql)
        return len(values)

    cur = cn.cursor()
    cur.fast_executemany = bool(fast_executemany)

    cur.executemany(insert_sql, values)
    cn.commit()

    return len(values)
