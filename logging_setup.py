from __future__ import annotations
import logging
import os
from datetime import datetime


def setup_logging(log_dir: str, level: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("csv_to_sqlserver")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # evita handler duplicati se rilanci da IDE
    if logger.handlers:
        return logger

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.join(log_dir, f"run_{ts}.log")

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("Logging avviato. File log: %s", logfile)
    return logger
