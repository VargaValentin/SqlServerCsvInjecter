from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

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


def _format_value_for_log(v: Any) -> str:
    """Log-friendly value preview (non esplode su roba strana)."""
    try:
        if v is None:
            return "None"
        if isinstance(v, str):
            s = v
            preview = s if len(s) <= 120 else (s[:117] + "...")
            return f"str(len={len(s)})={preview!r}"
        return f"{type(v).__name__}={v!r}"
    except Exception:
        return f"{type(v).__name__}=<unprintable>"


def _diagnose_truncation_row_by_row(
    cn,
    insert_sql: str,
    columns: List[str],
    rows_converted: List[Dict[str, Any]],
    logger,
    dry_run: bool,
) -> None:
    """
    Diagnostica: prova a inserire riga-per-riga per identificare quale colonna/valore
    causa il problema di truncation/buffer.
    """
    if dry_run:
        logger.info("dry_run=True: salto diagnostica row-by-row.")
        return

    # build tuple values (ordine colonne)
    values: List[Tuple[Any, ...]] = []
    for r in rows_converted:
        values.append(tuple(r.get(c) for c in columns))

    # prova riga per riga
    cur = cn.cursor()
    try:
        for i, row in enumerate(values):
            try:
                cur.execute(insert_sql, row)
            except Exception as e:
                logger.error("DIAGNOSTICA: fallimento alla riga batch_index=%d: %s", i, e)

                # stampa dettagli colonna-per-colonna
                for j, col in enumerate(columns):
                    v = row[j]
                    logger.error("  col=%s  value=%s", col, _format_value_for_log(v))

                # alza eccezione per fermare tutto
                raise
        cn.commit()
        logger.info("DIAGNOSTICA: row-by-row completata senza errori (strano se prima falliva).")
    finally:
        try:
            cur.close()
        except Exception:
            pass


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

        # log dei tipi DB delle colonne che arrivano dal CSV
        for c in columns:
            if c in col_types:
                logger.info("Tipo DB colonna %-30s -> %s", c, col_types[c])

        # 1) Tieni solo le colonne presenti anche in tabella (match per nome)
        columns_sql = [c for c in columns if c in col_types]
        columns_ignored = [c for c in columns if c not in col_types]

        if columns_ignored:
            logger.warning(
                "Colonne presenti nel CSV ma NON nella tabella %s.%s (IGNORATE): %s",
                schema, table, columns_ignored
            )

        if not columns_sql:
            logger.error(
                "Nessuna colonna del CSV combacia con la tabella %s.%s. Impossibile inserire.",
                schema, table
            )
            return 3

        logger.info("Colonne che verranno INSERITE (%d): %s", len(columns_sql), columns_sql)

        converters = build_converters(
            col_types=col_types,
            decimal_sep=str(csv_cfg.get("decimal_separator", ",")),
            thousands_sep=str(csv_cfg.get("thousands_separator", ".")),
            datetime_formats=csv_cfg.get("datetime_formats"),
        )

        insert_sql = build_insert_statement(schema=schema, table=table, columns=columns_sql)

        chunksize = int(csv_cfg.get("chunksize", 2000))
        fast_executemany = bool(sql_cfg.get("fast_executemany", True))

        for i, batch in enumerate(chunked(rows_iter, chunksize), start=1):
            # Convert row values to match SQL Server column types
            try:
                batch_converted: List[Dict[str, Any]] = []
                for r in batch:
                    r_filtered = {k: r.get(k) for k in columns_sql}
                    batch_converted.append(convert_row(r_filtered, converters))
            except Exception:
                logger.exception("Errore conversione tipi (batch #%d).", i)
                raise

            try:
                inserted = insert_batch(
                    cn=cn,
                    insert_sql=insert_sql,
                    columns=columns_sql,
                    rows=batch_converted,
                    fast_executemany=fast_executemany,
                    logger=logger,
                    dry_run=dry_run,
                )
            except Exception:
                logger.exception("Insert fallita (batch #%d). Avvio diagnostica row-by-row...", i)
                # diagnostica: trova colonna/valore che rompe
                _diagnose_truncation_row_by_row(
                    cn=cn,
                    insert_sql=insert_sql,
                    columns=columns_sql,
                    rows_converted=batch_converted,
                    logger=logger,
                    dry_run=dry_run,
                )
                raise

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
