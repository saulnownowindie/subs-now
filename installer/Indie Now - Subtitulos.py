"""
Lanzador de Indie Now - Subtitulos para el menu Scripts de DaVinci Resolve.

Este archivo no hace el trabajo: sólo abre la aplicación de escritorio y vuelve,
para que Resolve no se quede esperando. El instalador lo copia a:

    %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility

Después de copiarlo, reiniciá Resolve y va a aparecer en:
    Workspace -> Scripts -> Utility -> Indie Now - Subtitulos
"""

import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "IndieDL"
SCRIPT = APP_DIR / "subs" / "subtitulos.py"
PYTHONW = APP_DIR / "venv" / "Scripts" / "pythonw.exe"


def avisar(mensaje):
    try:
        import tkinter as tk
        from tkinter import messagebox
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("Indie Now - Subtitulos", mensaje)
        raiz.destroy()
    except Exception:
        print(mensaje)


def main():
    if not SCRIPT.exists():
        avisar(
            "No encuentro la aplicacion instalada en:\n\n"
            + str(SCRIPT)
            + "\n\nEjecuta INSTALAR.bat del repositorio subs-now y volve a intentar."
        )
        return 1

    exe = str(PYTHONW) if PYTHONW.exists() else sys.executable
    banderas = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [exe, str(SCRIPT)],
            cwd=str(SCRIPT.parent),
            creationflags=banderas,
            close_fds=True,
        )
    except Exception as exc:
        avisar("No pude abrir Indie Now - Subtitulos:\n\n%s" % (exc,))
        return 1
    return 0


if __name__ == "__main__":
    main()
