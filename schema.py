from __future__ import annotations
import pyodbc
from typing import Dict


def get_table_types(cn: pyodbc.Connection, schema: str, table: str) -> Dict[str, str]:
    sql = """
    SELECT COLUMN_NAME, DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
    """
    cur = cn.cursor()
    rows = cur.execute(sql, (schema, table)).fetchall()
    return {r[0]: r[1] for r in rows}
