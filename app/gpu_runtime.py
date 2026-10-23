"""Detección del dispositivo y preparación opcional de CUDA para faster-whisper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _app_dir() -> Path:
    return Path(__file__).resolve().parent


def preparar_cuda() -> list[str]:
    """Agrega al proceso las carpetas DLL locales de CUDA si el instalador las dejó."""
    base = _app_dir() / "cuda-runtime"
    if not base.is_dir():
        return []

    rutas = [base]
    rutas.extend(p for p in base.rglob("*") if p.is_dir())
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
