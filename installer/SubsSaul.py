"""Lanzador de subtítulos para DaVinci Resolve (Windows)."""

import os
import subprocess
from pathlib import Path


APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "IndieDL" / "subs"
PYTHONW = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
SCRIPT = APP_DIR / "subtitulos.py"

if not PYTHONW.is_file() or not SCRIPT.is_file():
    raise FileNotFoundError(
        "Indie Now no está instalado. Ejecutá installer\\setup_windows.ps1 "
        "del repositorio.\n\n" + str(APP_DIR)
    )

subprocess.Popen([str(PYTHONW), str(SCRIPT)], cwd=str(APP_DIR), close_fds=True)
