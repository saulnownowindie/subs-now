"""
Indie Now - Subtitulos desde guion
DaVinci Resolve Studio - Windows

Uso:
    abrir Resolve con la timeline lista y ejecutar:
    python subtitulos.py

Interfaz con la estética de IndieDL (indie_ui.py + subtitulos_ui.py).
Audio: se pueden elegir una o varias pistas (A1, A2, ...); si hay más de
una se mezclan en un solo audio antes de transcribir (mezcla_audio.py).
Traducción opcional: genera un SRT traducido y su propia pista, agrupando
frases para conservar el contexto (Google Translate, requiere internet).

Reglas de formato de subtítulos (fijas):
  - NUNCA hay , . ; : ! ? … al final de una palabra ni de un subtítulo.
  - Todo lo que el guion enmarca entre comillas (canciones, álbumes, EPs,
    singles, lanzamientos, etc.) se muestra en un único bloque entre “ ”,
    con el texto exacto del guion y sin puntuación final.
"""

import os
import re
import sys
import json
import math
import unicodedata
import subprocess
import tempfile
import threading
import difflib
import wave
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

import tkinter as tk
from tkinter import messagebox

import numpy as np

import indie_ui
import mezcla_audio
import gpu_runtime
from subtitulos_ui import SubtitulosApp

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitulos_config.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitulos.log")
CACHE_TRAD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "traducciones_cache.json")

MODEL = "large-v3-turbo"
ALUCINACIONES = [
    "graciasporverelvideo",
    "graciasporver",
    "suscribete",
    "subtitulosrealizadospor",
    "subtitulosporlacomunidad",
    "amaraorg",
    "graciasporvernos",
]
UMBRAL = 0.6
CARPETA_PLANTILLAS = ("plantillas", "snap captions")
SOLO = "(SRT como pista de subtítulos)"

NOMBRES_IDIOMA = {
    "es": "español", "en": "inglés", "pt": "portugués",
    "fr": "francés", "it": "italiano", "de": "alemán",
}
IDIOMAS_VALIDOS = ("auto",) + tuple(NOMBRES_IDIOMA)
ESTILOS_TRAD = ("palabras", "frases")


def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_procesos_activos = []
_procesos_lock = threading.Lock()
_cancelar_procesos = threading.Event()


def registrar_proceso(proceso):
    with _procesos_lock:
        _procesos_activos.append(proceso)


def quitar_proceso(proceso):
    with _procesos_lock:
        if proceso in _procesos_activos:
            _procesos_activos.remove(proceso)


def cerrar_procesos():
    _cancelar_procesos.set()
    with _procesos_lock:
        procesos = list(_procesos_activos)
    for proceso in procesos:
        try:
            if proceso.poll() is None:
                proceso.terminate()
        except Exception:
            pass
    for proceso in procesos:
        try:
            if proceso.poll() is None:
                proceso.kill()
        except Exception:
            pass


def ejecutar_proceso(args, capture_output=False, timeout=3600):
    if _cancelar_procesos.is_set():
        raise RuntimeError("Proceso cancelado.")

    kwargs = {"creationflags": CREATE_NO_WINDOW}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
        kwargs["text"] = True
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"

    proceso = subprocess.Popen(args, **kwargs)
    registrar_proceso(proceso)

    try:
        try:
            stdout, stderr = proceso.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proceso.kill()
            except Exception:
                pass
            raise RuntimeError(f"El proceso excedió el tiempo máximo ({timeout}s).")

        if _cancelar_procesos.is_set():
            raise RuntimeError("Proceso cancelado.")

        if proceso.returncode != 0:
            detalle = stderr.strip() if stderr else ""
            raise subprocess.CalledProcessError(
                proceso.returncode, args, output=stdout, stderr=detalle
            )
        return stdout
    finally:
        quitar_proceso(proceso)


def cargar_config():
    cfg = {
        "guion": "", "srt": "", "plantilla": SOLO,
        "pistas": [1], "rango": "inout",
        "subs": True, "txt": False, "comparar": False, "solo_srt": False,
        "idioma": "auto", "traducir": False, "idioma_destino": "es",
        "estilo_trad": "palabras",
    }
    datos = {}
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                datos = json.load(f) or {}
            cfg.update(datos)
    except Exception:
        datos = {}

    # Migración: versiones anteriores guardaban una sola pista como "A1".
    pistas = datos.get("pistas")
    if not isinstance(pistas, (list, tuple)):
        pistas = []
        vieja = datos.get("pista")
        if vieja:
            try:
                pistas = [int(str(vieja).upper().lstrip("A"))]
            except Exception:
                pistas = []
    limpias = []
    for p in pistas:
        try:
            n = int(str(p).upper().lstrip("A"))
            if n > 0 and n not in limpias:
                limpias.append(n)
        except Exception:
            continue
    cfg["pistas"] = sorted(limpias) or [1]
    cfg.pop("pista", None)
    if cfg.get("plantilla") is None:
        cfg["plantilla"] = SOLO
    if cfg.get("idioma") not in IDIOMAS_VALIDOS:
        cfg["idioma"] = "auto"
    if cfg.get("idioma_destino") not in NOMBRES_IDIOMA:
        cfg["idioma_destino"] = "es"
    if cfg.get("estilo_trad") not in ESTILOS_TRAD:
        cfg["estilo_trad"] = "palabras"
    cfg["traducir"] = bool(cfg.get("traducir"))
    return cfg


def guardar_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _preparar_resolve():
    prog_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    api = os.environ.get("RESOLVE_SCRIPT_API") or os.path.join(
        prog_data, "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting"
    )
    lib = os.environ.get("RESOLVE_SCRIPT_LIB") or os.path.join(
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll"
    )
    os.environ["RESOLVE_SCRIPT_API"] = api
    os.environ["RESOLVE_SCRIPT_LIB"] = lib
    mod = os.path.join(api, "Modules")
    if mod not in sys.path:
        sys.path.insert(0, mod)
    try:
        os.add_dll_directory(os.path.dirname(lib))
    except Exception:
        pass


_preparar_resolve()

import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
if not resolve:
    raise RuntimeError("No se pudo conectar con DaVinci Resolve.")

project = resolve.GetProjectManager().GetCurrentProject()
if not project:
    raise RuntimeError("No hay ningún proyecto abierto en DaVinci Resolve.")

mp = project.GetMediaPool()
timeline = project.GetCurrentTimeline()
if not timeline:
    raise RuntimeError("No hay timeline abierta en Resolve.")

FPS = float(timeline.GetSetting("timelineFrameRate"))
TL_START = timeline.GetStartFrame()
TL_NAME = timeline.GetName()


def leer_timeline_actual():
    global project, mp, timeline, FPS, TL_START, TL_NAME
    try:
        p = resolve.GetProjectManager().GetCurrentProject()
        t = p.GetCurrentTimeline() if p else None
        if not p or not t:
            return None
        project, mp, timeline = p, p.GetMediaPool(), t
        FPS = float(t.GetSetting("timelineFrameRate"))
        TL_START = t.GetStartFrame()
        TL_NAME = t.GetName()
        return p.GetName(), TL_NAME
    except Exception:
        return None


def leer_guion(path):
    if path.lower().endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def es_cabecera(l):
    return ("—" in l or " – " in l) and bool(
        re.search(r"[“\"‘]|\b(álbum|album|EP)\b", l)
    )


def tokenizar_guion(texto):
    toks = []
    g = 0
    for linea in texto.splitlines():
        l = linea.strip()
        if not l:
            continue
        if l.isupper():
            continue
        if es_cabecera(l):
            continue
        if l.startswith("AA "):
            continue
        for m in re.finditer(r"[‘“\"](.+?)[’”\"]|(\S+)", l):
            if m.group(1):
                g += 1
                for w in m.group(1).split():
                    w = w.strip(".,;:¡¿()")
                    if w:
                        toks.append((w, g))
            else:
                w = m.group(2).strip(".,;:¡!¿?()“”‘’\"")
                if w:
                    toks.append((w, None))
    return toks


def norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "",
                  "".join(c for c in w if unicodedata.category(c) != "Mn"))


# ────────────────────────────────────────────────────────────
# FORMATO DE SUBTÍTULOS
# ────────────────────────────────────────────────────────────
_PUNT_FINAL = ",.;:!?…"
_COMILLAS_PARES = {
    '“': '”', '‘': '’', '"': '"', "'": "'", '«': '»', '‹': '›',
}


def limpiar_puntuacion_final(t):
    """Quita , . ; : ! ? … al final de una palabra o bloque.
    Si el bloque está entre comillas, limpia DENTRO de ellas y las conserva.
    Se aplica SIEMPRE: los subtítulos nunca terminan en puntuación final."""
    if not t:
        return t
    abre, cierra = None, None
    if len(t) >= 2 and t[0] in _COMILLAS_PARES and t[-1] == _COMILLAS_PARES[t[0]]:
        abre, cierra = t[0], t[-1]
        t = t[1:-1]
    t = t.rstrip(_PUNT_FINAL)
    if abre is not None:
        t = abre + t + cierra
    return t


def _envolver_comillas(texto):
    """Envuelve el texto en “ ” si no lo está. Si ya lo está con otras comillas,
    las normaliza a “ ”. Garantiza el formato visual del bloque nombre."""
    if len(texto) >= 2 and texto[0] in _COMILLAS_PARES and texto[-1] == _COMILLAS_PARES[texto[0]]:
        return "“" + texto[1:-1] + "”"
    return "“" + texto + "”"


# ────────────────────────────────────────────────────────────
# NOMBRES PROPIOS
# ────────────────────────────────────────────────────────────
_STOP_NAMES = {"y", "o", "pero", "que", "el", "la", "los", "las", "un", "una",
               "es", "son", "de", "del", "al", "en", "con", "por", "para"}


def nombres_propios(texto):
    nombres = []
    vistos = set()

    def agregar(ws, comillas):
        ws = [w for w in ws if w]
        key = tuple(norm(w) for w in ws)
        texto_key = "".join(key)
        # Los títulos entre comillas son señal fuerte: alcanza con 2 letras.
        # Los nombres sueltos ("Bad", "Ian", "Rey") se aceptan desde 3.
        minimo = 2 if comillas else 3
        if ws and texto_key and key not in vistos and len(texto_key) >= minimo:
            vistos.add(key)
            nombres.append((ws, comillas))

    for linea in texto.splitlines():
        l = linea.strip()
        if not l:
            continue
        if l.isupper() and not l.startswith("AA "):
            continue
        if l.startswith("AA "):
            continue
        cab = es_cabecera(l)
        actual = []
        inicio = True
        for m in re.finditer(r"[‘“\"](.+?)[’”\"]|(\S+)", l):
            if m.group(1):
                agregar(actual, False)
                actual = []
                agregar([w.strip(".,;:¡¿()") for w in m.group(1).split()], True)
                inicio = False
                continue
            raw = m.group(2)
            w = raw.strip(".,;:¡!¿?()“”‘’\"")
            if w in ("—", "–", "-"):
                agregar(actual, False)
                actual = []
                inicio = True
                continue
            if w and w[0].isupper() and (cab or not inicio):
                if norm(w) in _STOP_NAMES:
                    agregar(actual, False)
                    actual = []
                else:
                    actual.append(w)
            else:
                agregar(actual, False)
                actual = []
            inicio = raw[-1:] in ".!?"
        agregar(actual, False)
    return nombres


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _alinear(A, B):
    n = len(A); m = len(B); salto = 0.2
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] - salto
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] - salto
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            r = _sim(A[i - 1], B[j - 1])
            dp[i][j] = max(
                dp[i - 1][j - 1] + (r if r >= UMBRAL else -9),
                dp[i - 1][j] - salto,
                dp[i][j - 1] - salto,
            )
    par = {}
    i, j = n, m
    while i > 0 and j > 0:
        r = _sim(A[i - 1], B[j - 1])
        if r >= UMBRAL and abs(dp[i][j] - (dp[i - 1][j - 1] + r)) < 1e-9:
            par[i - 1] = j - 1
            i -= 1; j -= 1
        elif abs(dp[i][j] - (dp[i - 1][j] - salto)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return par


def ajustar_nombres(pal, nombres):
    if not nombres:
        return pal
    out = []; i = 0; gid = 100000
    while i < len(pal):
        mejor = None
        for ws, com in nombres:
            objetivo = norm("".join(ws))
            for L in {max(1, len(ws) - 1), len(ws), len(ws) + 1}:
                if i + L > len(pal):
                    continue
                ven = pal[i:i + L]
                cand = norm("".join(x[0] for x in ven))
                if not cand:
                    continue
                if (_sim(norm(ven[0][0]), norm(ws[0])) < UMBRAL or
                        _sim(norm(ven[-1][0]), norm(ws[-1])) < UMBRAL):
                    continue
                r = difflib.SequenceMatcher(None, cand, objetivo).ratio()
                if r >= 0.8 and (mejor is None or r > mejor[0]):
                    mejor = (r, L, ws, com)
        if mejor is None:
            out.append(pal[i]); i += 1; continue
        _, L, ws, com = mejor
        ventana = pal[i:i + L]
        if [x[0] for x in ventana] == ws and (not com or ventana[0][3] is not None):
            out.extend(ventana); i += L; continue
        s0 = ventana[0][1]; e0 = ventana[-1][2]
        total = sum(len(w) for w in ws) or 1
        g = None
        if com:
            gid += 1; g = gid
        t = s0
        for w in ws:
            d = (e0 - s0) * len(w) / total
            out.append([w, t, t + d, g])
            t += d
        i += L
    return out


def extraer_pista(pista, f_ini, f_fin, tmp):
    """Extrae cada clip de una pista a WAV, con su posición dentro del rango."""
    segs = []
    for i, it in enumerate(timeline.GetItemListInTrack("audio", pista) or []):
        if _cancelar_procesos.is_set():
            raise RuntimeError("Proceso cancelado.")
        a = max(it.GetStart(), f_ini)
        b = min(it.GetEnd(), f_fin)
        if b <= a:
            continue
        mpi = it.GetMediaPoolItem()
        src = mpi.GetClipProperty("File Path") if mpi else None
        if not src:
            continue
        off = (it.GetLeftOffset() + (a - it.GetStart())) / FPS
        dur = (b - a) / FPS
        wav = os.path.join(tmp, f"a{pista}_{i}.wav")
        ejecutar_proceso([
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{off:.3f}", "-t", f"{dur:.3f}", "-i", src,
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav,
        ])
        segs.append((wav, (a - f_ini) / FPS))
    return segs


def segmentos_audio(pistas, f_ini, f_fin, tmp):
    """Audio listo para transcribir: una pista tal cual, o varias mezcladas."""
    pistas = [int(p) for p in pistas] or [1]
    por_pista = [(p, extraer_pista(p, f_ini, f_fin, tmp)) for p in pistas]
    if len(pistas) == 1:
        return por_pista[0][1]
    if not any(segs for _, segs in por_pista):
        return []
    duracion = max(0.0, (f_fin - f_ini) / FPS)
    return mezcla_audio.mezclar(por_pista, duracion, tmp)


def cargar_wav(path):
    with wave.open(path, "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


_ultimos = []
_idioma_detectado = ""


def es_alucinacion(seg):
    t = norm(seg.text)
    _ultimos.append(t)
    del _ultimos[:-3]
    if any(b in t for b in ALUCINACIONES):
        return True
    if t == "gracias":
        return True
    if len(_ultimos) == 3 and t and _ultimos[0] == _ultimos[1] == _ultimos[2]:
        return True
    return seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8


def _dlls_cuda():
    return gpu_runtime.preparar_cuda()


def transcribir(segs, prompt, progreso, modelo=None, p_ini=30, p_fin=90,
                idioma="auto"):
    """Transcribe los segmentos. `modelo` permite reusar el modelo entre pistas.

    `idioma` puede ser "auto" (detección de Whisper) o un código ISO ("es", "en"…).
    """
    global _ultimos, _idioma_detectado
    _ultimos = []
    _idioma_detectado = "" if idioma in ("", "auto") else idioma
    language = None if idioma in ("", "auto") else idioma
    _dlls_cuda()
    from faster_whisper import WhisperModel

    total = 0.0
    for wav, _ in segs:
        salida = ejecutar_proceso(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", wav],
            capture_output=True,
        )
        try:
            total += float(salida.strip())
        except Exception:
            pass
    total = total or 1.0

    nombre_gpu = gpu_runtime.nvidia_info()
    dispositivo = "cuda"
    if modelo is not None:
        model = modelo
        dispositivo = "cuda"
    else:
        etiqueta_gpu = f"GPU NVIDIA: {nombre_gpu}" if nombre_gpu else "GPU CUDA"
        progreso(p_ini, f"Probando {etiqueta_gpu}; cargando Whisper…")
        try:
            model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        except Exception as exc:
            dispositivo = "cpu"
            _log(f"No se pudo iniciar CUDA; se cambia a CPU: {type(exc).__name__}: {exc}")
            progreso(p_ini, f"CUDA no inició ({type(exc).__name__}); continúo en CPU.")
            model = WhisperModel(MODEL, device="cpu", compute_type="int8")

    def ejecutar(modelo_actual, dispositivo_actual):
        global _ultimos, _idioma_detectado
        _ultimos = []
        _idioma_detectado = "" if idioma in ("", "auto") else idioma
        hecho = 0.0
        palabras = []
        for wav, t0 in segs:
            if _cancelar_procesos.is_set():
                raise RuntimeError("Proceso cancelado.")
            it, info = modelo_actual.transcribe(
                cargar_wav(wav),
                language=language,
                word_timestamps=True,
                initial_prompt=prompt,
                vad_filter=False,
                beam_size=5,
            )
            if not _idioma_detectado:
                _idioma_detectado = str(getattr(info, "language", "") or "")
            for segmento in it:
                if _cancelar_procesos.is_set():
                    raise RuntimeError("Proceso cancelado.")
                avance = (hecho + segmento.end) / total
                progreso(p_ini + (p_fin - p_ini) * avance,
                         f"Transcribiendo en {dispositivo_actual}…")
                if es_alucinacion(segmento):
                    continue
                for palabra in segmento.words or []:
                    palabras.append([palabra.word.strip(),
                                     t0 + palabra.start, t0 + palabra.end])
            hecho += info.duration
        return palabras

    try:
        etiqueta = (f"GPU NVIDIA ({nombre_gpu}, CUDA)" if nombre_gpu
                    else "GPU (CUDA)")
        return ejecutar(model, etiqueta if dispositivo == "cuda" else "CPU")
    except Exception as exc:
        if _cancelar_procesos.is_set() or dispositivo != "cuda":
            raise
        progreso(p_ini, f"Falló CUDA ({type(exc).__name__}); repito la transcripción completa en CPU…")
        _log(f"Falló CUDA al transcribir; se reintenta en CPU: {type(exc).__name__}: {exc}")
        try:
            model_cpu = WhisperModel(MODEL, device="cpu", compute_type="int8")
            return ejecutar(model_cpu, "CPU (respaldo tras fallo de CUDA)")
        except Exception as exc_cpu:
            if _cancelar_procesos.is_set():
                raise
            raise RuntimeError(
                "Falló la GPU y tampoco se pudo completar en CPU. "
                f"Error CUDA: {type(exc).__name__}: {exc}. "
                f"Error CPU: {type(exc_cpu).__name__}: {exc_cpu}"
            ) from exc_cpu


def corregir(palabras, guion, nombres=None):
    A = [norm(w[0]) for w in palabras]
    B = [norm(t[0]) for t in guion]
    limpio = lambda w: w.strip(".,;:¡!¿?")

    en_nombre = set()
    for ws, _ in nombres or []:
        clave = [norm(w) for w in ws]
        n = len(clave)
        for p in range(len(B) - n + 1):
            if B[p:p + n] == clave:
                en_nombre.update(range(p, p + n))

    res = []
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                res.append([guion[j1 + k][0], palabras[i1 + k][1],
                            palabras[i1 + k][2], guion[j1 + k][1]])
        elif tag == "replace":
            if (en_nombre
                    and all(j in en_nombre for j in range(j1, j2))
                    and i2 - i1 <= 6 and j2 - j1 <= 6):
                s0 = palabras[i1][1]; e0 = palabras[i2 - 1][2]
                total = sum(len(guion[j][0]) for j in range(j1, j2)) or 1
                t = s0
                for j in range(j1, j2):
                    d = (e0 - s0) * len(guion[j][0]) / total
                    res.append([guion[j][0], t, t + d, guion[j][1]])
                    t += d
                continue
            if i2 - i1 == j2 - j1:
                par = {k: k for k in range(i2 - i1)
                       if _sim(A[i1 + k], B[j1 + k]) >= UMBRAL}
            else:
                par = _alinear(A[i1:i2], B[j1:j2])
            for k in range(i2 - i1):
                w = palabras[i1 + k]
                if k in par:
                    g = guion[j1 + par[k]]
                    res.append([g[0], w[1], w[2], g[1]])
                else:
                    res.append([limpio(w[0]), w[1], w[2], None])
        elif tag == "delete":
            for k in range(i1, i2):
                res.append([limpio(palabras[k][0]),
                            palabras[k][1], palabras[k][2], None])
    return res


def armar_bloques(palabras):
    """Una palabra por subtítulo, salvo:
       - Los nombres entre comillas en el guion (canción, álbum, EP, single,
         lanzamiento…): van en UN bloque, entre “ ”, con el texto exacto del
         guion y sin puntuación final.
       - Cada palabra simple pierde también su puntuación final.
    """
    bl = []
    i = 0
    while i < len(palabras):
        t, s, e, g = palabras[i]
        if g is not None:
            j = i
            while j + 1 < len(palabras) and palabras[j + 1][3] == g:
                j += 1
            # Limpiar la puntuación de cada palabra del nombre y unir con espacios.
            partes = [limpiar_puntuacion_final(x[0]) for x in palabras[i:j + 1]]
            texto = " ".join(partes).strip()
            texto = _envolver_comillas(texto)
            bl.append([texto, s, palabras[j][2]])
            i = j + 1
        else:
            bl.append([limpiar_puntuacion_final(t), s, e])
            i += 1

    # Ajuste de duración (cap más laxo para nombres entre comillas).
    for b in bl:
        es_nombre = b[0].startswith("“")
        factor = 0.15 if es_nombre else 0.08
        b[2] = min(b[2], b[1] + 0.6 + factor * len(b[0]))
    for k in range(len(bl) - 1):
        if 0 <= bl[k + 1][1] - bl[k][2] < 0.35:
            bl[k][2] = bl[k + 1][1]
    for b in bl:
        b[2] = max(b[2], b[1] + 0.08)
    for k in range(len(bl) - 1):
        if bl[k + 1][1] < bl[k][2]:
            bl[k + 1][1] = bl[k][2]
            bl[k + 1][2] = max(bl[k + 1][2], bl[k + 1][1] + 0.08)
    return bl


def tc(t):
    ms = int(round(max(0.0, t) * 1000))
    return (f"{ms // 3600000:02}:{ms // 60000 % 60:02}:"
            f"{ms // 1000 % 60:02},{ms % 1000:03}")


def escribir_srt(bloques, path):
    with open(path, "w", encoding="utf-8") as f:
        for n, (t, s, e) in enumerate(bloques, 1):
            f.write(f"{n}\n{tc(s)} --> {tc(e)}\n<b>{t}</b>\n\n")


# ────────────────────────────────────────────────────────────
# TRADUCCIÓN (pista aparte)
# ────────────────────────────────────────────────────────────
_FIN_FRASE = re.compile(r"[.!?…][\"'”’)\]]*$")
_TRAD_CACHE = None


def _cache_trad_cargar():
    global _TRAD_CACHE
    if _TRAD_CACHE is None:
        _TRAD_CACHE = {}
        try:
            if os.path.exists(CACHE_TRAD_FILE):
                with open(CACHE_TRAD_FILE, encoding="utf-8") as f:
                    datos = json.load(f)
                if isinstance(datos, dict):
                    _TRAD_CACHE = {str(k): str(v) for k, v in datos.items()}
        except Exception:
            _TRAD_CACHE = {}
    return _TRAD_CACHE


def _cache_trad_guardar():
    if _TRAD_CACHE is None:
        return
    try:
        # Guardar solo lo último para que no crezca sin control.
        recorte = dict(list(_TRAD_CACHE.items())[-8000:])
        with open(CACHE_TRAD_FILE, "w", encoding="utf-8") as f:
            json.dump(recorte, f, ensure_ascii=False)
    except Exception:
        pass


def _google_pedir(texto, origen, destino, timeout=25):
    """POST al endpoint público de Google Translate (sin dependencias extra)."""
    params = urllib.parse.urlencode({
        "client": "gtx", "sl": origen or "auto", "tl": destino, "dt": "t",
    })
    datos = urllib.parse.urlencode({"q": texto}).encode("utf-8")
    req = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + params,
        data=datos,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _google_una(texto, origen, destino, timeout=25):
    ultimo = None
    for intento in range(3):
        try:
            data = _google_pedir(texto, origen, destino, timeout)
            partes = [seg[0] for seg in (data[0] or []) if seg and seg[0]]
            salida = "".join(partes).strip("\n")
            if salida:
                return salida
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code == 429:
                time.sleep(1.2 * (intento + 1))
                continue
        except Exception as exc:
            ultimo = exc
        if intento < 2:
            time.sleep(0.7 * (intento + 1))
    raise RuntimeError(f"Google Translate no respondió: {ultimo}")


def _google_lote(lineas, origen, destino, timeout=25):
    """Traduce varias líneas en una sola consulta (mantiene el contexto)."""
    q = "\n".join(lineas)
    ultimo = None
    for intento in range(3):
        try:
            data = _google_pedir(q, origen, destino, timeout)
            texto = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
            salida = texto.split("\n")
            while salida and not salida[-1].strip():
                salida.pop()
            if len(salida) == len(lineas):
                return [x.strip() for x in salida]
            # Si Google juntó o partió líneas, traducir una por una.
            return [_google_una(l, origen, destino, timeout) for l in lineas]
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code == 429:
                time.sleep(1.2 * (intento + 1))
                continue
        except Exception as exc:
            ultimo = exc
        if intento < 2:
            time.sleep(0.7 * (intento + 1))
    raise RuntimeError(f"Google Translate no respondió: {ultimo}")


def traducir_lineas(lineas, origen, destino, progreso=None):
    """Traduce textos usando Google Translate, con caché y contexto por lote."""
    limpio = [re.sub(r"\s+", " ", str(x)).strip() for x in lineas]
    cache = _cache_trad_cargar()
    salida = [None] * len(limpio)
    pendientes = []
    for i, texto in enumerate(limpio):
        if not texto:
            salida[i] = ""
            continue
        clave = f"{origen}>{destino}|{texto}"
        if clave in cache:
            salida[i] = cache[clave]
        else:
            pendientes.append((i, texto))
    total = len(pendientes)
    hechos = 0
    i = 0
    while i < total:
        lote = []
        caracteres = 0
        while i < total and len(lote) < 12 and (caracteres < 1400 or not lote):
            item = pendientes[i]
            lote.append(item)
            caracteres += len(item[1]) + 1
            i += 1
        resultado = _google_lote([t for _, t in lote], origen, destino)
        if len(resultado) != len(lote):
            raise RuntimeError("La traducción no devolvió la misma cantidad de líneas.")
        for (indice, texto), trad in zip(lote, resultado):
            trad = (trad or "").strip() or texto
            cache[f"{origen}>{destino}|{texto}"] = trad
            salida[indice] = trad
        hechos += len(lote)
        if progreso:
            progreso(84 + 4 * hechos / max(1, total),
                     f"Traduciendo al {NOMBRES_IDIOMA.get(destino, destino)}...")
        if i < total:
            time.sleep(0.25)
    _cache_trad_guardar()
    return [s if s is not None else limpio[k] for k, s in enumerate(salida)]


def agrupar_frases(palabras, max_palabras=14, max_dur=7.0, pausa=0.85):
    """Agrupa palabras en frases para traducir con contexto."""
    unidades, actual = [], []
    for i, w in enumerate(palabras):
        actual.append(w)
        texto = str(w[0]).strip()
        nxt = palabras[i + 1] if i + 1 < len(palabras) else None
        hueco = (nxt[1] - w[2]) if nxt else 99.0
        fin = bool(_FIN_FRASE.search(texto)) or hueco >= pausa
        if len(actual) >= max_palabras or (w[2] - actual[0][1]) >= max_dur:
            fin = True
        if fin and actual:
            unidades.append(actual)
            actual = []
    if actual:
        unidades.append(actual)

    # Unir fragmentos sueltos ("you know", "I mean") con la frase siguiente.
    fusion = []
    for u in unidades:
        if (fusion and len(fusion[-1]) <= 2
                and not _FIN_FRASE.search(str(fusion[-1][-1][0]).strip())
                and (u[0][1] - fusion[-1][-1][2]) < 1.0):
            fusion[-1].extend(u)
        else:
            fusion.append(list(u))
    return fusion


def _partir_frases(texto, limite=84):
    texto = str(texto).strip()
    if len(texto) <= limite:
        return [texto] if texto else []
    partes, resto = [], texto
    while len(resto) > limite:
        corte = resto.rfind(", ", 0, limite + 1)
        if corte < int(limite * 0.45):
            corte = resto.rfind(" ", 0, limite + 1)
        if corte < int(limite * 0.45):
            corte = limite
        trozo = resto[:corte].strip(" ,;")
        if trozo:
            partes.append(trozo)
        resto = resto[corte:].strip(" ,;")
    if resto:
        partes.append(resto)
    return partes


def _partir_palabras(texto, duracion):
    palabras = str(texto).split()
    if len(palabras) <= 2:
        return [texto]
    objetivo = max(1, min(len(palabras), int(round(duracion / 0.85))))
    tam = max(1, math.ceil(len(palabras) / objetivo))
    return [" ".join(palabras[i:i + tam]) for i in range(0, len(palabras), tam)]


def bloques_traduccion(unidades, traducciones, estilo="palabras"):
    """Convierte frases traducidas en subtítulos con tiempos del original."""
    bloques = []
    for unidad, texto in zip(unidades, traducciones):
        texto = (texto or "").strip()
        if not texto or not unidad:
            continue
        inicio = float(unidad[0][1])
        final = float(unidad[-1][2])
        if final <= inicio:
            final = inicio + 0.4
        dur = max(0.3, final - inicio)
        partes = (_partir_palabras(texto, dur) if estilo == "palabras"
                  else _partir_frases(texto))
        if not partes:
            continue
        pesos = [max(1, len(p)) for p in partes]
        total = sum(pesos)
        t = inicio
        for k, parte in enumerate(partes):
            if k == len(partes) - 1:
                fin = final
            else:
                fin = min(final, t + dur * pesos[k] / total)
            if fin <= t:
                fin = t + 0.15
            bloques.append([parte, t, fin])
            t = fin
    for k in range(len(bloques) - 1):
        if bloques[k][2] > bloques[k + 1][1]:
            bloques[k][2] = bloques[k + 1][1]
    for b in bloques:
        if b[2] <= b[1]:
            b[2] = b[1] + 0.15
    return bloques


def _respaldar(ruta):
    if not ruta or not os.path.exists(ruta):
        return
    try:
        bak = ruta + ".bak"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(ruta, bak)
    except Exception:
        pass


def _es_plantilla_media_pool(clip, en_carpeta_plantillas=False):
    """MediaPoolItem no expone siempre su FusionComp; usa su tipo/ruta para listar candidatos."""
    try:
        tipo = str(clip.GetClipProperty("Type") or "").strip().lower()
        ruta = str(clip.GetClipProperty("File Path") or "").strip().lower()
    except Exception:
        tipo, ruta = "", ""
    return (
        en_carpeta_plantillas
        or "fusion" in tipo
        or "title" in tipo
        or "text" in tipo
        or ruta.endswith((".setting", ".comp"))
    )


def _clips(carpeta, en_carpeta_plantillas, out, ruta=()):
    for c in carpeta.GetClipList() or []:
        if not _es_plantilla_media_pool(c, en_carpeta_plantillas):
            continue
        try:
            tipo = str(c.GetClipProperty("Type") or "").lower()
            ruta_archivo = str(c.GetClipProperty("File Path") or "").lower()
        except Exception:
            tipo, ruta_archivo = "", ""
        if "subtitle" in tipo or ruta_archivo.endswith(".srt"):
            continue
        etiqueta = " / ".join((*ruta, c.GetName()))
        out[etiqueta] = c
    for f in carpeta.GetSubFolderList() or []:
        nombre = f.GetName().strip()
        marcada = (en_carpeta_plantillas
                   or nombre.lower() in CARPETA_PLANTILLAS)
        _clips(f, marcada, out, (*ruta, nombre))


def plantillas():
    out = {}
    _clips(mp.GetRootFolder(), False, out)
    return out


def _pista_activa(tipo, indice, activa):
    """Intenta activar/desactivar una pista (si la versión de Resolve lo permite)."""
    try:
        return bool(timeline.SetTrackEnable(tipo, int(indice), bool(activa)))
    except Exception:
        return False


def aplicar_plantilla(tpl, bloques, f_ini, progreso, p_ini=92, p_fin=100):
    timeline.AddTrack("video")
    pista = timeline.GetTrackCount("video")
    infos = []
    for t, s, e in bloques:
        if _cancelar_procesos.is_set():
            raise RuntimeError("Proceso cancelado.")
        dur = max(1, round((e - s) * FPS))
        infos.append({
            "mediaPoolItem": tpl,
            "startFrame": 0, "endFrame": dur,
            "trackIndex": pista,
            "recordFrame": f_ini + round(s * FPS),
        })
    items = mp.AppendToTimeline(infos) or []
    if len(items) != len(bloques):
        if items:
            try:
                timeline.DeleteClips(items)
            except Exception:
                pass
        raise RuntimeError("Resolve no pudo insertar todos los títulos de la plantilla seleccionada.")
    comps_tools = []
    for it in items:
        comp = it.GetFusionCompByIndex(1)
        tools = comp.GetToolList(False, "TextPlus") if comp else {}
        tool = next(iter((tools or {}).values()), None)
        if tool is None:
            try:
                timeline.DeleteClips(items)
            except Exception:
                pass
            raise RuntimeError(
                "El elemento elegido no contiene un nodo Text+ editable. "
                "Elegí un clip de tipo Fusion Composition/Text+ del Media Pool."
            )
        comps_tools.append(tool)
    for n, (tool, b) in enumerate(zip(comps_tools, bloques)):
        tool.StyledText = b[0]
        progreso(p_ini + (p_fin - p_ini) * n / max(1, len(items)),
                 "Aplicando plantilla...")
    return len(items) == len(bloques)


def carpeta_subtitulos():
    root = mp.GetRootFolder()
    for f in root.GetSubFolderList() or []:
        if f.GetName().strip().lower() == "subtitulos":
            return f
    return mp.AddSubFolder(root, "SUBTITULOS")


def carpeta_de_timeline():
    uid = None
    try:
        mpi = timeline.GetMediaPoolItem()
        uid = mpi.GetUniqueId() if mpi else None
    except Exception:
        pass
    por_nombre = []

    def buscar(carpeta):
        for c in carpeta.GetClipList() or []:
            try:
                if uid and c.GetUniqueId() == uid:
                    return carpeta
                if (c.GetName() == TL_NAME
                        and "timeline" in (c.GetClipProperty("Type") or "").lower()):
                    por_nombre.append(carpeta)
            except Exception:
                pass
        for f in carpeta.GetSubFolderList() or []:
            r = buscar(f)
            if r:
                return r
        return None

    exacta = buscar(mp.GetRootFolder())
    return exacta or (por_nombre[0] if por_nombre else None)


def importar_srt(srt):
    previa = mp.GetCurrentFolder()
    try:
        destino = carpeta_de_timeline() or carpeta_subtitulos()
        if (destino and destino.GetName().strip().lower() in CARPETA_PLANTILLAS):
            destino = carpeta_subtitulos()
        if destino:
            mp.SetCurrentFolder(destino)
        return mp.ImportMedia([srt]) or []
    finally:
        if previa:
            mp.SetCurrentFolder(previa)


def agregar_srt_a_linea(items, f_ini=None):
    if not items:
        return False
    record = f_ini if f_ini is not None else TL_START
    for intento in ([{"mediaPoolItem": items[0], "recordFrame": record}], items):
        try:
            if mp.AppendToTimeline(intento):
                return True
        except Exception:
            pass
    return False


SILENCIO_MAX = 0.5


def _nombre_seguro(t):
    return re.sub(r'[<>:"/\\|?*]', "-", str(t)).strip() or "timeline"


def carpeta_salida(cfg):
    for ruta in (cfg.get("srt"), cfg.get("guion")):
        if ruta:
            d = os.path.dirname(ruta)
            if d and os.path.isdir(d):
                return d
    d = os.path.join(os.path.expanduser("~"), "Documents")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def escribir_transcripcion(pal, off, ruta, titulo):
    def mmss(t):
        t = max(0.0, t)
        return f"{int(t // 60)}:{int(t % 60):02d}"
    frases, cur = [], []
    for w in pal:
        if cur and (w[1] - cur[-1][2] > 0.7 or w[2] - cur[0][1] > 12):
            frases.append(cur); cur = []
        cur.append(w)
    if cur:
        frases.append(cur)
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(f"{titulo}\n{len(pal)} palabras\n\n")
        f.write("TEXTO\n")
        f.write(" ".join(w[0] for w in pal) + "\n\n")
        f.write("CON TIEMPOS (min:seg desde el inicio de la timeline)\n")
        for fr in frases:
            f.write(f"[{mmss(fr[0][1] + off)}] " + " ".join(w[0] for w in fr) + "\n")


def comparar_con_guion(pal, guion, segs, f_ini, cfg):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import verificacion
    except ImportError:
        raise RuntimeError("Falta verificacion.py: tiene que estar en la misma carpeta que subtitulos.py.")
    return verificacion.verificar(
        pal, guion, segs, f_ini, timeline, FPS, TL_START, cargar_wav,
        SILENCIO_MAX, cfg["guion"], TL_NAME
    )


def generar(cfg, progreso):
    solo_srt = cfg.get("solo_srt", False)
    hacer_subs = cfg.get("subs", True) or solo_srt
    hacer_txt = cfg.get("txt", False)
    hacer_comp = cfg.get("comparar", False)
    tiene_guion = bool(cfg.get("guion"))
    idioma_audio = cfg.get("idioma", "auto")
    traducir = bool(cfg.get("traducir")) and hacer_subs
    destino = cfg.get("idioma_destino", "es")
    estilo_trad = cfg.get("estilo_trad", "palabras")
    bloques_trad = None
    aviso_trad = ""
    pistas = [int(p) for p in (cfg.get("pistas") or [cfg.get("pista") or 1])]
    if not pistas:
        pistas = [1]
    detalle_pistas = " + ".join(f"A{p}" for p in pistas)

    if not (hacer_subs or hacer_txt or hacer_comp):
        raise RuntimeError("Marcá al menos una opción.")
    if hacer_comp and not tiene_guion:
        raise RuntimeError("Para comparar con el guion necesito el guion.")

    progreso(2, "Leyendo guion..." if tiene_guion else "Preparando...")
    texto_guion = leer_guion(cfg["guion"]) if tiene_guion else ""
    guion = tokenizar_guion(texto_guion) if texto_guion else []
    nombres = nombres_propios(texto_guion) if texto_guion else []
    if tiene_guion and not guion:
        raise RuntimeError("No pude sacar palabras del guion.")

    mi = timeline.GetMarkInOut() or {}
    ai = mi.get("audio") or mi.get("video") or {}
    if cfg["rango"] == "inout" and ai:
        f_ini = ai["in"]; f_fin = ai["out"]
    else:
        f_ini = TL_START; f_fin = timeline.GetEndFrame()

    off = (f_ini - TL_START) / FPS
    bloques = None
    resumen_comp = ""

    with tempfile.TemporaryDirectory() as tmp:
        progreso(5, f"Extrayendo audio de {detalle_pistas}...")
        segs = segmentos_audio(pistas, f_ini, f_fin, tmp)
        if not segs:
            raise RuntimeError(f"No hay audio en {detalle_pistas} dentro de ese rango.")
        if len(pistas) > 1:
            progreso(20, f"Mezclando {len(pistas)} pistas en un solo audio...")
        lista_nombres = ", ".join(" ".join(ws) for ws, _ in nombres)[:350]
        prompt = (" ".join(t[0] for t in guion)[:350] + ". " + lista_nombres).strip(". ")[:700]
        pal = transcribir(segs, prompt, progreso, p_ini=30, p_fin=90,
                          idioma=idioma_audio)
        pal_trad = pal
        if hacer_subs:
            if guion:
                pal_trad = ajustar_nombres(corregir(pal, guion, nombres), nombres)
                bloques = armar_bloques(pal_trad)
            else:
                bloques = armar_bloques([[w[0], w[1], w[2], None] for w in pal])
        if traducir and pal_trad:
            progreso(83, "Preparando traducción...")
            unidades = agrupar_frases(pal_trad)
            pares = []
            for u in unidades:
                texto = re.sub(r"\s+", " ", " ".join(str(w[0]).strip() for w in u)).strip()
                if texto:
                    pares.append((u, texto))
            if not pares:
                aviso_trad = "No hay texto para traducir."
            else:
                origen = idioma_audio if idioma_audio != "auto" else (_idioma_detectado or "auto")
                try:
                    traducciones = traducir_lineas([t for _, t in pares], origen,
                                                   destino, progreso)
                    bloques_trad = bloques_traduccion([u for u, _ in pares],
                                                      traducciones, estilo_trad)
                except Exception as ex:
                    aviso_trad = f"No pude traducir: {ex}"
        if hacer_comp:
            progreso(88, "Comparando con el guion...")
            try:
                resumen_comp = comparar_con_guion(pal, guion, segs, f_ini, cfg)
            except Exception as ex:
                resumen_comp = f"No pude comparar con el guion: {ex}"

    partes = []
    if hacer_txt:
        progreso(90, "Guardando transcripción...")
        ruta_txt = os.path.join(carpeta_salida(cfg),
                                _nombre_seguro(TL_NAME) + "_transcripcion.txt")
        escribir_transcripcion(pal, off, ruta_txt, TL_NAME)
        partes.append(f"TRANSCRIPCIÓN GUARDADA ({len(pal)} palabras)\n{ruta_txt}")
    if hacer_comp and resumen_comp:
        partes.append(resumen_comp)

    if hacer_subs:
        srt = cfg["srt"] or os.path.join(carpeta_salida(cfg),
                                          _nombre_seguro(TL_NAME) + ".srt")
        os.makedirs(os.path.dirname(srt) or ".", exist_ok=True)
        ruta_trad = ""
        if bloques_trad:
            raiz = os.path.splitext(srt)[0]
            ruta_trad = f"{raiz}_{destino.upper()}.srt"

        # Backup de los SRT previos antes de sobrescribir.
        _respaldar(srt)
        if ruta_trad:
            _respaldar(ruta_trad)

        # RED DE SEGURIDAD del original: ningún subtítulo con puntuación final,
        # y los nombres entre “ ” intactos. La traducción conserva su puntuación.
        for b in bloques:
            b[0] = limpiar_puntuacion_final(b[0])
            if not b[0]:
                b[0] = "…"   # por si quedara vacío (no debería)

        progreso(91, "Guardando SRT...")
        escribir_srt([[t, s + off, e + off] for t, s, e in bloques], srt)
        if ruta_trad:
            escribir_srt([[t, s + off, e + off] for t, s, e in bloques_trad],
                         ruta_trad)
        if solo_srt:
            msg = (f"SRT GENERADO CORRECTAMENTE\n"
                   f"{len(bloques)} subtítulos originales\n{srt}")
            if ruta_trad:
                msg += (f"\n\nTRADUCCIÓN ({NOMBRES_IDIOMA.get(destino, destino).upper()})\n"
                        f"{len(bloques_trad)} subtítulos\n{ruta_trad}")
            if aviso_trad:
                msg += f"\n\n{aviso_trad}"
            partes.insert(0, msg)
            progreso(100, "\n\n".join(partes))
            return
        items = importar_srt(srt)
        items_trad = importar_srt(ruta_trad) if ruta_trad else []
        nombre_destino = NOMBRES_IDIOMA.get(destino, destino)
        if cfg["plantilla"]:
            if ruta_trad:
                ok = aplicar_plantilla(cfg["plantilla"],
                                       [[b[0], b[1], b[2]] for b in bloques],
                                       f_ini, progreso, 92, 96)
                pista_original = timeline.GetTrackCount("video")
                ok2 = aplicar_plantilla(cfg["plantilla"],
                                        [[b[0], b[1], b[2]] for b in bloques_trad],
                                        f_ini, progreso, 96, 100)
                oculta = False
                if ok and ok2:
                    oculta = _pista_activa("video", pista_original, False)
                    msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n"
                           f"{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}\n"
                           f"Dos pistas de video con plantilla")
                    if oculta:
                        msg += "\nLa pista original quedó desactivada: activala en Resolve si la querés ver."
                    else:
                        msg += "\nDejá visible la pista que quieras (hay dos superpuestas)."
                else:
                    msg = (f"SUBTÍTULOS GENERADOS PARCIALMENTE\n"
                           f"{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}.\n"
                           f"Revisá la línea: algo no se colocó.")
            else:
                ok = aplicar_plantilla(cfg["plantilla"],
                                       [[b[0], b[1], b[2]] for b in bloques],
                                       f_ini, progreso, 92, 100)
                if ok:
                    msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n"
                           f"{len(bloques)} subtítulos con plantilla")
                else:
                    msg = (f"SUBTÍTULOS GENERADOS\n"
                           f"{len(bloques)} subtítulos.\nRevisá la línea: algo no se colocó.")
        else:
            progreso(95, "Agregando SRT a la línea...")
            ok = agregar_srt_a_linea(items, f_ini)
            ok2 = agregar_srt_a_linea(items_trad, f_ini) if ruta_trad else False
            if ok and (not ruta_trad or ok2):
                if ruta_trad:
                    msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n"
                           f"{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}\n"
                           f"Dos pistas de subtítulos en la línea")
                else:
                    msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n"
                           f"{len(bloques)} subtítulos en la línea")
            else:
                msg = (f"SRT GENERADO CORRECTAMENTE\n"
                       f"{len(bloques)} subtítulos.\n"
                       f"El SRT fue guardado e importado, "
                       f"pero no pude agregarlo automáticamente a la línea.")
        partes.insert(0, msg)
        if ruta_trad and ruta_trad not in msg:
            partes.append(f"TRADUCCIÓN: {ruta_trad}")
        if aviso_trad:
            partes.append(aviso_trad)

    progreso(100, "\n\n".join(partes) if partes else "LISTO")

def verificar_herramientas():
    faltan = []
    for exe in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([exe, "-version"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10, check=True)
        except Exception:
            faltan.append(exe)
    if faltan:
        msg = ("No encontré: " + ", ".join(faltan) + ".\n\n"
               "Instalalos (winget install Gyan.FFmpeg) y reiniciá esta app.")
        try:
            messagebox.showerror("Indie Now - Subtítulos", msg)
        except Exception:
            print("ERROR:", msg)
        sys.exit(1)


class Backend:
    """Puente entre la interfaz y Resolve / Whisper."""

    def config(self):
        return cargar_config()

    def guardar(self, cfg):
        guardar_config(cfg)

    def existe(self, ruta):
        return bool(ruta) and os.path.exists(ruta)

    def info_timeline(self):
        info = leer_timeline_actual()
        if not info:
            return None
        proyecto, nombre = info
        return {"nombre": nombre, "proyecto": proyecto, "fps": float(FPS)}

    def pistas(self):
        try:
            total = int(timeline.GetTrackCount("audio") or 0)
        except Exception:
            total = 0
        salida = []
        for n in range(1, total + 1):
            try:
                items = timeline.GetItemListInTrack("audio", n) or []
            except Exception:
                items = []
            nombres, primer_inicio = [], None
            for it in items:
                try:
                    mpi = it.GetMediaPoolItem()
                    nombre = (mpi.GetName() if mpi else "") or ""
                except Exception:
                    nombre = ""
                if nombre and nombre not in nombres:
                    nombres.append(nombre)
                try:
                    inicio = int(it.GetStart())
                    if primer_inicio is None or inicio < primer_inicio:
                        primer_inicio = inicio
                except Exception:
                    pass
            if len(nombres) > 2:
                titulo = ", ".join(nombres[:2]) + f" +{len(nombres) - 2}"
            else:
                titulo = ", ".join(nombres)
            if primer_inicio is not None:
                seg = max(0.0, (primer_inicio - TL_START) / FPS)
                cuando = f"{int(seg // 60):02d}:{int(seg % 60):02d}"
                detalle = (f"{len(items)} clip{'s' if len(items) != 1 else ''}"
                           f" · desde {cuando}")
            else:
                detalle = "sin clips"
            salida.append({
                "n": n,
                "label": f"A{n}",
                "clips": len(items),
                "titulo": titulo or "Sin clips",
                "detalle": detalle,
                "derecha": ("VACÍA" if not items else
                            f"{len(items)} CLIP{'S' if len(items) != 1 else ''}"),
            })
        return salida

    def plantillas(self):
        try:
            nombres = sorted(plantillas().keys(), key=lambda s: s.lower())
        except Exception:
            nombres = []
        return [SOLO] + nombres

    def correr(self, cfg, progreso):
        cfg = dict(cfg)
        nombre_tpl = cfg.get("plantilla") or SOLO
        item = None
        if nombre_tpl != SOLO:
            try:
                item = plantillas().get(nombre_tpl)
            except Exception:
                item = None
        cfg["plantilla"] = item
        generar(cfg, progreso)

    def cancelar(self):
        cerrar_procesos()

    def registrar_error(self, exc, tb):
        _log("ERROR\n" + (tb or repr(exc)))


def main():
    global _cancelar_procesos
    _cancelar_procesos.clear()
    verificar_herramientas()

    backend = Backend()
    root = tk.Tk()
    indie_ui.init_fonts(root)
    app = SubtitulosApp(root, backend)
    root.protocol("WM_DELETE_WINDOW", app.cerrar)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        _log(f"fatal: {ex!r}\n{traceback.format_exc()}")
        try:
            messagebox.showerror("Indie Now - Subtítulos", str(ex))
        except Exception:
            print("ERROR:", ex)
