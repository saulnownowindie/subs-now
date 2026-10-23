"""Lanzador único de Indie Now para DaVinci Resolve (Windows)."""

import os
import subprocess
from pathlib import Path


APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "IndieDL" / "subs"
EXE = APP_DIR / "IndieNow-Subtitulos-Ancho" / "IndieNow-Subtitulos-Ancho.exe"
PYTHONW = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
SCRIPT = APP_DIR / "subtitulos.py"

# En instalaciones que incluyen la versión visual empaquetada, abrir esa
# versión con icono Indie Now. En instalaciones solo de código, usar el venv.
if EXE.is_file():
    subprocess.Popen([str(EXE)], cwd=str(EXE.parent), close_fds=True)
elif PYTHONW.is_file() and SCRIPT.is_file():
    subprocess.Popen([str(PYTHONW), str(SCRIPT)], cwd=str(APP_DIR), close_fds=True)
else:
    raise FileNotFoundError(
        "Indie Now no está instalado. Ejecutá installer\\setup_windows.ps1.\n\n"
        + str(APP_DIR)
    )
