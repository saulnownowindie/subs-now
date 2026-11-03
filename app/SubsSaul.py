r"""Lanzador único de Indie Now para DaVinci Resolve (Windows).

Orden de búsqueda:
  1. Ejecutable compilado nuevo (si existe).
  2. Ejecutable viejo (si existe).
  3. Entorno virtual de la app:  IndieDL\venv  o  IndieDL\subs\.venv.
  4. Python del sistema (último recurso).

Deja registro en  subs\lanzador.log  para diagnosticar desde Resolve.
"""

import os
import subprocess
import sys
import time
from pathlib import Path


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IndieDL" / "subs"
LOG = APP_DIR / "lanzador.log"

EXE_NUEVO = APP_DIR / "dist" / "IndieNow-Subtitulos" / "IndieNow-Subtitulos.exe"
EXE_VIEJO = APP_DIR / "IndieNow-Subtitulos-Ancho" / "IndieNow-Subtitulos-Ancho.exe"
SCRIPT = APP_DIR / "subtitulos.py"

PYTHONW_CANDIDATES = [
    APP_DIR.parent / "venv" / "Scripts" / "pythonw.exe",  # instalación actual
    APP_DIR / ".venv" / "Scripts" / "pythonw.exe",        # instalación vieja
    APP_DIR / "venv" / "Scripts" / "pythonw.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "pythonw.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "pythonw.exe",
]


def _log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _pythonw():
    for candidate in PYTHONW_CANDIDATES:
        if candidate.is_file():
            return candidate
    # Si el que ejecuta este script es un Python con Tk, sirve como último recurso.
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return None


def main():
    if EXE_NUEVO.is_file():
        target, cwd = EXE_NUEVO, EXE_NUEVO.parent
    elif EXE_VIEJO.is_file():
        target, cwd = EXE_VIEJO, EXE_VIEJO.parent
    else:
        pythonw = _pythonw()
        if pythonw and SCRIPT.is_file():
            target, cwd = pythonw, APP_DIR
        else:
            buscados = [EXE_NUEVO, EXE_VIEJO, SCRIPT, *PYTHONW_CANDIDATES]
            detalle = "\n".join(f"  - {p}" for p in buscados)
            _log("ERROR: no se encontró ejecutable ni Python.\n" + detalle)
            raise FileNotFoundError(
                "Indie Now no está instalado o no se encontró cómo iniciarlo.\n"
                "Buscado en:\n" + detalle
            )

    args = [str(target)]
    if target == SCRIPT or target.name.lower().startswith("python"):
        args.append(str(SCRIPT))

    _log(f"Lanzando: {target} (cwd={cwd})")
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _log(f"Proceso iniciado: {proc.pid}")


if __name__ == "__main__":
    main()
