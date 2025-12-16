from __future__ import annotations
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def pick_csv_file(
    title: str = "Seleziona il file CSV",
    initial_dir: str | None = None,
) -> str:
    """
    Apre una finestra UI per scegliere un CSV e ritorna il path.
    Se annulli la selezione, solleva RuntimeError.
    """
    root = tk.Tk()
    root.withdraw()  # non mostrare finestra principale
    root.attributes("-topmost", True)  # porta davanti il dialog

    if initial_dir and Path(initial_dir).exists():
        init = initial_dir
    else:
        init = str(Path.cwd())

    path = filedialog.askopenfilename(
        title=title,
        initialdir=init,
        filetypes=[
            ("CSV files", "*.csv"),
            ("All files", "*.*"),
        ],
    )

    root.destroy()

    if not path:
        raise RuntimeError("Selezione CSV annullata dall'utente.")
    return path
