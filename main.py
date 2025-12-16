from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

from logging_setup import setup_logging
from ui_picker import pick_csv_file
from csv_reader import iter_csv_rows, chunked
from db import connect_sqlserver, build_insert_statement, insert_batch
from schema import get_table_types
from converters import build_converters, convert_row


def load_config(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config non trovato: {path}")
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config YAML non valido: deve essere una mappa (key:value).")
    return cfg


def parse_args():
    ap = argparse.ArgumentParser(description="CSV -> SQL Server injector")
    ap.add_argument("--config", default="config.yaml", help="Path del file di configurazione YAML")
    ap.add_argument("--pick-csv", action="store_true", help="Apri UI per scegliere il CSV")
    ap.add_argument("--csv", default=None, help="Override path CSV (sovrascrive config)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    # --- Config sections ---
    app_cfg = cfg.get("app", {})
    csv_cfg = cfg.get("csv", {})
    sql_cfg = cfg.get("sqlserver", {})

    logger = setup_logging(
        log_dir=str(app_cfg.get("log_dir", "logs")),
        level=str(app_cfg.get("log_level", "INFO")),
    )

    dry_run = bool(app_cfg.get("dry_run", False))

    # --- Determine CSV path (priority: --csv > --pick-csv > config) ---
    csv_path = args.csv or csv_cfg.get("path")

    if args.pick_csv:
        try:
            init_dir = None
            if csv_path:
                try:
                    init_dir = str(Path(csv_path).expanduser().resolve().parent)
                except Exception:
                    init_dir = None

            csv_path = pick_csv_file(initial_dir=init_dir)
            logger.info("CSV selezionato da UI: %s", csv_path)
        except Exception:
            logger.exception("Selezione CSV via UI fallita/annullata.")
            return 2

    if not csv_path:
        logger.error("CSV non specificato. Usa --pick-csv oppure imposta csv.path in config.yaml")
        return 2

    csv_path = str(Path(csv_path).expanduser())
    if not Path(csv_path).exists():
        logger.error("CSV non trovato: %s", csv_path)
        return 2

    # --- SQL config sanity ---
    conn_str = sql_cfg.get("connection_string")
    schema = sql_cfg.get("schema", "dbo")
    table = sql_cfg.get("table")
    if not conn_str or not table:
        logger.error("Config SQL Server incompleta: servono sqlserver.connection_string e sqlserver.table")
        return 2

    logger.info("Avvio import CSV -> SQL Server")
    logger.info("CSV: %s", csv_path)
    logger.info("Destinazione: %s.%s", schema, table)
    logger.info("dry_run=%s", dry_run)

    # --- Read CSV header + stream rows ---
    delimiter = str(csv_cfg.get("delimiter", ";"))
    encoding = str(csv_cfg.get("encoding", "utf-8-sig"))
    null_values = csv_cfg.get("null_values", ["", "NULL", "null", "None"])

    columns, rows_iter = iter_csv_rows(
        path=csv_path,
        delimiter=delimiter,
        encoding=encoding,
        null_values=null_values,
    )

    logger.info("Colonne CSV rilevate (%d): %s", len(columns), columns)

    cn = None
    total = 0
    try:
        cn = connect_sqlserver(
            conn_str=conn_str,
            timeout_seconds=int(sql_cfg.get("timeout_seconds", 30)),
            logger=logger,
        )

        # --- Read table schema/types and build converters ---
        col_types = get_table_types(cn, schema, table)

        missing_in_table = [c for c in columns if c not in col_types]
        if missing_in_table:
            logger.error("Colonne del CSV non presenti nella tabella %s.%s: %s", schema, table, missing_in_table)
            logger.error("Correggi header CSV o tabella (nomi colonne devono combaciare).")
            return 3

        converters = build_converters(
            col_types=col_types,
            decimal_sep=str(csv_cfg.get("decimal_separator", ",")),
            thousands_sep=str(csv_cfg.get("thousands_separator", ".")),
            datetime_formats=csv_cfg.get("datetime_formats"),
        )

        insert_sql = build_insert_statement(schema=schema, table=table, columns=columns)

        chunksize = int(csv_cfg.get("chunksize", 2000))
        fast_executemany = bool(sql_cfg.get("fast_executemany", True))

        for i, batch in enumerate(chunked(rows_iter, chunksize), start=1):
            # Convert row values to match SQL Server column types (avoid 22018)
            try:
                batch_converted = [convert_row(r, converters) for r in batch]
            except Exception:
                logger.exception("Errore conversione tipi (batch #%d).", i)
                raise

            inserted = insert_batch(
                cn=cn,
                insert_sql=insert_sql,
                columns=columns,
                rows=batch_converted,
                fast_executemany=fast_executemany,
                logger=logger,
                dry_run=dry_run,
            )
            total += inserted
            logger.info("Batch #%d: %d righe inserite (totale=%d)", i, inserted, total)

        logger.info("Import completato. Totale righe: %d", total)
        return 0

    except Exception:
        logger.exception("Errore durante l'import.")
        return 1

    finally:
        if cn is not None:
            try:
                cn.close()
                logger.info("Connessione chiusa.")
            except Exception:
                logger.warning("Errore chiusura connessione.", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
