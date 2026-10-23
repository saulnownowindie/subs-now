"""Detección del dispositivo y preparación opcional de CUDA para faster-whisper."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _app_dir() -> Path:
    return Path(__file__).resolve().parent


def preparar_cuda() -> list[str]:
    """Registra DLL de CUDA del instalador o de los paquetes NVIDIA de Python."""
    app = _app_dir()
    bases = [app / "cuda-runtime", app.parent / "cuda-runtime"]
    bases.extend(Path(p) / "nvidia" for p in sys.path if p and Path(p).is_dir())
    # El ejecutable empaquetado no conserva el sys.path de Python; en la PC
    # donde se instala, CUDA puede estar en la instalación de Python 3.12.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        bases.append(Path(local) / "Programs" / "Python" / "Python312"
                     / "Lib" / "site-packages" / "nvidia")

    rutas = []
    for base in bases:
        if not base.is_dir():
            continue
        if base.name.lower() == "nvidia":
            rutas.extend(p / "bin" for p in base.iterdir()
                         if p.is_dir() and (p / "bin").is_dir()
                         and "cu11" not in p.name.lower())
        else:
            rutas.append(base)
            rutas.extend(p for p in base.rglob("*") if p.is_dir())
    rutas = list(dict.fromkeys(p for p in rutas if p.is_dir()))
    if not rutas:
        return []
    cargadores = []
    for ruta in rutas:
        try:
            cargadores.append(os.add_dll_directory(str(ruta)))
        except (AttributeError, OSError):
            pass
    os.environ["PATH"] = os.pathsep.join(
        [str(p) for p in rutas] + [os.environ.get("PATH", "")]
    )
    # Mantiene vivos los objetos de Windows que agregan directorios DLL.
    global _DLL_HANDLES
    _DLL_HANDLES = cargadores
    return [str(p) for p in rutas]


def nvidia_info() -> str | None:
    """Devuelve el nombre de una GPU NVIDIA disponible o None si no se detecta."""
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        candidatos = [
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ]
        exe = next((str(p) for p in candidatos if p.is_file()), None)
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        nombre = next((line.strip() for line in result.stdout.splitlines()
                       if line.strip()), "")
        return nombre or None
    except (OSError, subprocess.SubprocessError):
        return None


_DLL_HANDLES = []
