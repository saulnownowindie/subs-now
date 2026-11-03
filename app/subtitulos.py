"""
Indie Now - Subtitulos desde guion
DaVinci Resolve Studio - Windows
...
"""

import os
import re
import sys
import json
import math
import stat
import unicodedata
import subprocess
import tempfile
import threading
import difflib
import inspect
import wave
import time
import traceback
import gc
import tkinter as tk
from tkinter import messagebox

import numpy as np

import indie_ui
import mezcla_audio
import gpu_runtime
import traduccion_google as tg
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
    "graciasporverelvideo", "graciasporver", "suscribete", "subtitulosrealizadospor",
    "subtitulosporlacomunidad", "amaraorg", "graciasporvernos",
]
UMBRAL = 0.6
CARPETA_PLANTILLAS = ("plantillas", "snap captions")
SOLO = "(SRT como pista de subtítulos)"

NOMBRES_IDIOMA = {
    "es": "español", "en": "inglés", "pt": "portugués",
    "fr": "francés", "it": "italiano", "de": "alemán",
    "ja": "japonés", "ko": "coreano", "zh": "chino",
    "ru": "ruso", "ar": "árabe", "hi": "hindi",
    "tr": "turco", "id": "indonesio", "nl": "neerlandés",
    "pl": "polaco", "sv": "sueco", "uk": "ucraniano",
    "vi": "vietnamita", "th": "tailandés", "he": "hebreo",
    "el": "griego", "cs": "checo", "ro": "rumano",
    "hu": "húngaro", "da": "danés", "fi": "finés", "no": "noruego",
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
            try: proceso.kill()
            except Exception: pass
            raise RuntimeError(f"El proceso excedió el tiempo máximo ({timeout}s).")
        if _cancelar_procesos.is_set():
            raise RuntimeError("Proceso cancelado.")
        if proceso.returncode != 0:
            detalle = stderr.strip() if stderr else ""
            raise subprocess.CalledProcessError(proceso.returncode, args, output=stdout, stderr=detalle)
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
    pistas = datos.get("pistas")
    if not isinstance(pistas, (list, tuple)):
        pistas = []
        vieja = datos.get("pista")
        if vieja:
            try: pistas = [int(str(vieja).upper().lstrip("A"))]
            except Exception: pistas = []
    limpias = []
    for p in pistas:
        try:
            n = int(str(p).upper().lstrip("A"))
            if n > 0 and n not in limpias: limpias.append(n)
        except Exception: continue
    cfg["pistas"] = sorted(limpias) or [1]
    cfg.pop("pista", None)
    if cfg.get("plantilla") is None: cfg["plantilla"] = SOLO
    if cfg.get("idioma") not in IDIOMAS_VALIDOS: cfg["idioma"] = "auto"
    if cfg.get("idioma_destino") not in NOMBRES_IDIOMA: cfg["idioma_destino"] = "es"
    if cfg.get("estilo_trad") not in ESTILOS_TRAD: cfg["estilo_trad"] = "palabras"
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
    if mod not in sys.path: sys.path.insert(0, mod)
    try:
        os.add_dll_directory(os.path.dirname(lib))
    except Exception:
        pass


_preparar_resolve()

import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
if not resolve: raise RuntimeError("No se pudo conectar con DaVinci Resolve.")
project = resolve.GetProjectManager().GetCurrentProject()
if not project: raise RuntimeError("No hay ningún proyecto abierto en DaVinci Resolve.")
mp = project.GetMediaPool()
timeline = project.GetCurrentTimeline()
if not timeline: raise RuntimeError("No hay timeline abierta en Resolve.")

FPS = float(timeline.GetSetting("timelineFrameRate"))
TL_START = timeline.GetStartFrame()
TL_NAME = timeline.GetName()


def leer_timeline_actual():
    global project, mp, timeline, FPS, TL_START, TL_NAME
    try:
        p = resolve.GetProjectManager().GetCurrentProject()
        t = p.GetCurrentTimeline() if p else None
        if not p or not t: return None
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
    if l.startswith("=====") or l.startswith("Page"):
        return True
    return ("—" in l or " – " in l) and bool(re.search(r"[“\"‘]|álbum|album|EP", l))


def tokenizar_guion(texto):
    toks = []
    g = 0
    for linea in texto.splitlines():
        l = linea.strip()
        if not l or l.isupper() or es_cabecera(l) or l.startswith("AA "):
            continue
        for m in re.finditer(r"[«“\"](.+?)[»”\"]|(\S+)", l):
            if m.group(1):
                g += 1
                for w in m.group(1).split():
                    w = w.strip(".,;:¡¿()")
                    if w: toks.append((w, g))
            else:
                w = m.group(2).strip(".,;:¡!¿?()“”«»\"")
                if w: toks.append((w, None))
    return _extender_grupos(toks)


def _extender_grupos(toks):
    grupos = {}
    for w, g in toks:
        if g is not None:
            grupos.setdefault(g, []).append(w)
    if not grupos:
        return toks
    cands = {}
    for ws in grupos.values():
        cands[(tuple(ws), len(ws) < 3)] = ws
        if len(ws) > 3:
            for L in range(len(ws) - 1, 2, -1):
                for k in range(len(ws) - L + 1):
                    cands[(tuple(ws[k:k + L]), False)] = ws[k:k + L]
    orden = sorted(cands.items(), key=lambda kv: -len(kv[1]))
    out = [list(t) for t in toks]
    nuevo = max(grupos) + 1
    for (_, exacto), ws in orden:
        L = len(ws)
        clave = tuple(ws) if exacto else tuple(norm(x) for x in ws)
        i = 0
        while i + L <= len(out):
            ven = out[i:i + L]
            if all(v[1] is None for v in ven):
                cand = tuple(v[0] for v in ven) if exacto else tuple(norm(v[0]) for v in ven)
                if cand == clave:
                    for v in ven: v[1] = nuevo
                    nuevo += 1
                    i += L
                    continue
            i += 1
    return [(w, g) for w, g in out]


def glosario_del_guion(texto):
    """Nombres propios y títulos (secuencias con mayúscula) para ayudar a Whisper.
    No incluye frases del guion: así no se contamina lo que dice el audio."""
    vistos, out = set(), []
    patron = re.compile(r"(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*|\bII\b)(?:\s+(?:(?:de|la|del|el|y)\s+)*(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ]*))*")
    for linea in texto.splitlines():
        l = linea.strip()
        if not l or l.isupper() or es_cabecera(l): continue
        for m in patron.finditer(l):
            frag = m.group(0).strip(" .,;:¡!¿?«»“”\"")
            ini = m.start()
            previo = l[:ini].rstrip()
            inicio_frase = (not previo) or previo[-1] in ".!?¡¿:«“\""
            if " " not in frag and inicio_frase: continue
            if len(frag) < 3 or frag.lower() in vistos: continue
            vistos.add(frag.lower()); out.append(frag)
    return out


def nombres_del_guion(guion):
    nombres, vistos = [], set()
    g_ant, cur = None, []
    for w, g in list(guion) + [("", None)]:
        if g != g_ant and cur:
            n = " ".join(cur)
            if n.lower() not in vistos:
                vistos.add(n.lower()); nombres.append(n)
            cur = []
        if g is not None: cur.append(w)
        g_ant = g
    return nombres


def norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if unicodedata.category(c) != "Mn"))


# ────────────────────────────────────────────────────────────
# EXTRACCIÓN DE AUDIO (Lógica idéntica a cortes.py)
# ────────────────────────────────────────────────────────────
def cargar_wav(path):
    try:
        with wave.open(path, "rb") as w:
            canales = w.getnchannels()
            datos = w.readframes(w.getnframes())
            a = np.frombuffer(datos, dtype=np.int16).astype(np.float32) / 32768.0
            if canales > 1:
                a = a.reshape(-1, canales).mean(axis=1)
            return a
    except Exception:
        return np.zeros(16000, dtype=np.float32)

def _factor_velocidad_item(item):
    """Devuelve (factor positivo, reversa, disponible) del retime del clip."""
    dato = None
    try:
        dato = item.GetSpeed()
    except Exception:
        pass
    reversa = False
    valor = None
    if isinstance(dato, dict):
        valor = dato.get("Percentage", dato.get("Speed", dato.get("speed")))
        reversa = bool(dato.get("Reverse", dato.get("reverse", False)))
    else:
        valor = dato
    try:
        if isinstance(valor, str):
            texto = valor.strip().replace("%", "")
            valor = float(texto)
            porcentaje = "%" in str(dato)
        else:
            valor = float(valor)
            porcentaje = isinstance(dato, dict) or abs(valor) > 10.0
        if porcentaje:
            valor /= 100.0
        if valor < 0:
            reversa = True
            valor = abs(valor)
        if not math.isfinite(valor) or valor <= 0:
            raise ValueError("velocidad de retime no positiva")
        return valor, reversa, True
    except (TypeError, ValueError):
        return 1.0, reversa, False


def _mapear_intervalo_audio(item, a, b, fps):
    """Mapea un intervalo de timeline a tiempo fuente, contemplando retime.

    `GetSourceStartTime/EndTime` están en segundos de la fuente; `GetSpeed`
    devuelve el porcentaje de reproducción de Resolve. Si el API no expone los
    tiempos fuente, se conserva el cálculo compatible con clips a 1x.
    """
    fps = float(fps)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("FPS de timeline inválidos")
    clip_start = float(item.GetStart())
    clip_end = float(item.GetEnd())
    timeline_delta = max(0.0, (float(a) - clip_start) / fps)
    timeline_duration = max(0.0, (float(b) - float(a)) / fps)
    if timeline_duration <= 0:
        raise ValueError("intervalo de audio vacío")

    speed, reverse, speed_api = _factor_velocidad_item(item)
    try:
        source_start = float(item.GetSourceStartTime())
    except Exception:
        source_start = float(item.GetLeftOffset()) / fps
    try:
        source_end = float(item.GetSourceEndTime())
        if source_end < source_start:
            reverse = True
    except Exception:
        source_end = None

    # Si una versión del API no expone GetSpeed, se infiere el factor medio
    # del tramo fuente. Si GetSpeed sí lo proporciona, ese valor es autoridad.
    clip_duration = max(0.0, (clip_end - clip_start) / fps)
    if not speed_api and source_end is not None and clip_duration > 0:
        span_speed = abs(source_end - source_start) / clip_duration
        if math.isfinite(span_speed) and span_speed > 0:
            speed = span_speed

    if reverse:
        source_seek = source_start - (timeline_delta + timeline_duration) * speed
    else:
        source_seek = source_start + timeline_delta * speed
    source_duration = timeline_duration * speed
    if source_seek < 0:
        # En retimes inversos/handles raros, no inventar un offset negativo.
        raise ValueError(f"retime apunta antes del inicio de la fuente ({source_seek:.3f}s)")
    return source_seek, source_duration, timeline_duration, speed, reverse


def _filtro_tempo_retime(speed, reverse=False):
    """FFmpeg tempo para que el tramo fuente dure lo mismo que en la timeline."""
    tempo = float(speed)
    if not math.isfinite(tempo) or tempo <= 0:
        raise ValueError("factor de tempo inválido")
    filtros = ["areverse"] if reverse else []
    while tempo < 0.5:
        filtros.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        filtros.append("atempo=2.0")
        tempo /= 2.0
    if abs(tempo - 1.0) > 1e-7:
        filtros.append(f"atempo={tempo:.9f}")
    return ",".join(filtros)


def segmentos_audio(pistas, f_ini, f_fin, tmp, progreso=None):
    """Extrae audio en tiempo de timeline, incluyendo el retime de cada clip."""
    por_pista = []
    trabajos = []
    for pista in pistas:
        try:
            items = timeline.GetItemListInTrack("audio", pista) or []
        except Exception:
            items = []
        for i, item in enumerate(items):
            try:
                a = max(float(item.GetStart()), float(f_ini))
                b = min(float(item.GetEnd()), float(f_fin))
            except Exception:
                continue
            if b > a:
                trabajos.append((pista, i, item, a, b))

    total = len(trabajos)
    segmentos_por_pista = {int(p): [] for p in pistas}
    cada = max(1, total // 20)
    for indice, (pista, i, item, a, b) in enumerate(trabajos):
        if progreso and (indice % cada == 0 or indice == total - 1):
            pct = 5 + 15 * indice / max(1, total)
            progreso(pct, f"Extrayendo audio A{pista}, clip {indice + 1}/{total}…")
        try:
            mpi = item.GetMediaPoolItem()
            if not mpi:
                continue
            src = mpi.GetClipProperty("File Path")
            if not src or not os.path.exists(src):
                continue

            source_seek, source_duration, timeline_duration, speed, reverse = _mapear_intervalo_audio(
                item, a, b, FPS
            )
            wav = os.path.join(tmp, f"p{pista}_seg{i}.wav")
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{source_seek:.6f}", "-t", f"{source_duration:.6f}",
                "-i", src,
            ]
            filtro = _filtro_tempo_retime(speed, reverse)
            if filtro:
                cmd.extend(["-af", filtro])
            # El límite de salida garantiza que el WAV representa exactamente
            # la duración de timeline, aunque FFmpeg redondee muestras/frames.
            cmd.extend(["-t", f"{timeline_duration:.6f}", "-ac", "1", "-ar", "16000", wav])
            if abs(speed - 1.0) > 0.005:
                _log(f"[retime audio] A{pista} clip {i}: {speed * 100:.2f}% "
                     f"fuente {source_seek:.3f}s + {source_duration:.3f}s "
                     f"→ timeline {timeline_duration:.3f}s" + (" (reverse)" if reverse else ""))
            ejecutar_proceso(cmd, capture_output=True)

            if os.path.exists(wav) and os.path.getsize(wav) > 44:
                segmentos_por_pista[int(pista)].append((wav, (a - f_ini) / FPS))
        except Exception as exc:
            _log(f"Error procesando audio A{pista}, clip {i}: {type(exc).__name__}: {exc}")
            continue

    if progreso and total:
        progreso(20, f"Audio extraído y alineado ({total} segmentos revisados).")
    por_pista = [(int(p), segmentos_por_pista[int(p)]) for p in pistas]
    duracion_total = (f_fin - f_ini) / FPS
    return mezcla_audio.mezclar(por_pista, duracion_total, tmp)


# ────────────────────────────────────────────────────────────
# FORMATO DE SUBTÍTULOS
# ────────────────────────────────────────────────────────────
_PUNT_FINAL = ",.;:!?…"
_COMILLAS_PARES = {
    '“': '”', '‘': '’', '"': '"', "'": "'", '«': '»', '‹': '›',
}


def limpiar_puntuacion_final(t):
    if not t: return t
    abre, cierra = None, None
    if len(t) >= 2 and t[0] in _COMILLAS_PARES and t[-1] == _COMILLAS_PARES[t[0]]:
        abre, cierra = t[0], t[-1]
        t = t[1:-1]
    t = t.rstrip(_PUNT_FINAL)
    if abre is not None: t = abre + t + cierra
    return t


def _envolver_comillas(texto):
    if len(texto) >= 2 and texto[0] in _COMILLAS_PARES and texto[-1] == _COMILLAS_PARES[texto[0]]:
        return "“" + texto[1:-1] + "”"
    return "“" + texto + "”"


# ────────────────────────────────────────────────────────────
# CORRECCIÓN CON EL GUION (EL AUDIO ES EL REY)
# ────────────────────────────────────────────────────────────
def construir_envolvente(segs):
    trozos, fin = [], 0.0
    for wav, t0 in segs:
        a = cargar_wav(wav)
        n = len(a) // 160
        if n < 2: continue
        fr = a[:n * 160].reshape(n, 160)
        db = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)
        trozos.append((t0, db))
        fin = max(fin, t0 + n * 0.01)
    if not trozos: return None
    total = int(fin / 0.01) + 1
    env = np.full(total, -120.0, dtype=np.float32)
    for t0, db in trozos:
        k = int(round(t0 / 0.01))
        m = min(len(db), total - k)
        if m > 0: env[k:k + m] = np.maximum(env[k:k + m], db[:m])
    validos = env[env > -119]
    if len(validos) == 0: return None
    return env, float(np.percentile(validos, 10) + 9)


def quitar_palabras_sin_voz(palabras, env):
    """Solo elimina palabras inventadas en silencio real (nunca voz suave)."""
    if not env: return palabras
    arr, umbral = env
    out = []
    for p in palabras:
        i0, i1 = max(0, int(p[1] / 0.01)), min(len(arr), int(p[2] / 0.01) + 1)
        if i1 - i0 >= 8 and not (arr[i0:i1] > umbral).any():
            _log(f"[sin voz] descartada '{p[0]}' {p[1]:.2f}-{p[2]:.2f}s (pico {float(arr[i0:i1].max()):.1f} dB <= umbral {umbral:.1f} dB)")
            continue
        out.append(p)
    return out


def ajustar_a_voz(palabras, env, margen=0.03, cola=0.05):
    """Whisper estira palabras sobre las pausas. Recorta inicio/fin a donde hay voz real."""
    if not env: return palabras
    arr, umbral = env
    out = []
    for p in palabras:
        s0, e0 = p[1], p[2]
        i0, i1 = max(0, int(s0 / 0.01)), min(len(arr), int(e0 / 0.01) + 1)
        if i1 - i0 >= 4:
            vivos = np.nonzero(arr[i0:i1] > umbral)[0]
            if len(vivos):
                s1 = max(s0, (i0 + int(vivos[0])) * 0.01 - margen)
                e1 = min(e0, (i0 + int(vivos[-1]) + 1) * 0.01 + cola)
                if e1 - s1 >= 0.08: s0, e0 = s1, e1
        q = list(p); q[1], q[2] = s0, e0
        out.append(q)
    return out


def construir_audio(segs):
    partes, fin = [], 0
    for wav, t0 in segs:
        a = cargar_wav(wav)
        k = int(round(t0 * 16000))
        partes.append((k, a)); fin = max(fin, k + len(a))
    if not partes: return None
    mix = np.zeros(fin, dtype=np.float32)
    for k, a in partes: mix[k:k + len(a)] += a
    return np.clip(mix, -1.0, 1.0)


def recuperar_huecos(palabras, segs, modelo, language, glosario="", min_hueco=0.25, min_voz=0.20, velocidad=1.0):
    """Busca tramos con voz y reescala timestamps si el audio estaba acelerado."""
    try:
        r = construir_envolvente(segs)
        audio = construir_audio(segs)
    except Exception as exc:
        _log(f"[recuperar] no se pudo preparar el audio: {exc}"); return palabras
    if not r or audio is None: return palabras
    env, umbral = r
    total = len(audio) / 16000.0
    ocupado = sorted((p[1], p[2]) for p in palabras)
    libres, cur = [], 0.0
    for a, b in ocupado:
        if a - cur >= min_hueco: libres.append((cur, a))
        cur = max(cur, b)
    if total - cur >= min_hueco: libres.append((cur, total))
    nuevas = []
    for g0, g1 in libres:
        i0, i1 = max(0, int(g0 / 0.01)), min(len(env), int(g1 / 0.01) + 1)
        if i1 - i0 < 3: continue
        voz = env[i0:i1] > umbral
        runs, ini, hueco = [], None, 0
        for k, v in enumerate(voz):
            if v:
                if ini is None: ini = k
                fin_run, hueco = k, 0
            elif ini is not None:
                hueco += 1
                if hueco > 15:
                    runs.append((ini, fin_run)); ini = None
        if ini is not None: runs.append((ini, fin_run))
        for a, b in runs:
            r0, r1 = (i0 + a) * 0.01, (i0 + b + 1) * 0.01
            if r1 - r0 < min_voz: continue
            w0, w1 = max(0.0, r0 - 0.3), min(total, r1 + 0.3)
            trozo = audio[int(w0 * 16000):int(w1 * 16000)]
            if len(trozo) < 3200: continue
            try:
                factor_t = 1.0
                if velocidad > 1.005:
                    # La recuperación usa el audio original; se convierte a
                    # un factor temporal coherente con la pasada acelerada.
                    factor_t = 1.0 / float(velocidad)
                kw = dict(language=language, word_timestamps=True, vad_filter=False, beam_size=5,
                          condition_on_previous_text=False, initial_prompt=glosario or None)
                it, _ = modelo.transcribe(trozo, **kw)
                for seg in it:
                    if any(x in norm(seg.text) for x in ALUCINACIONES): continue
                    if seg.no_speech_prob > 0.6: continue
                    for pw in seg.words or []:
                        ws = w0 + pw.start * factor_t
                        we = w0 + pw.end * factor_t
                        if we < ws: ws, we = we, ws
                        centro = (ws + we) / 2
                        txt = pw.word.strip()
                        if not norm(txt) or not (g0 <= centro <= g1): continue
                        if getattr(pw, "probability", 1.0) < 0.3: continue
                        nuevas.append([txt, max(ws, g0), min(we, g1)])
                        _log(f"[recuperada] '{txt}' {ws:.2f}-{we:.2f}s (hueco {g0:.2f}-{g1:.2f}s)")
            except Exception as exc:
                _log(f"[recuperar] fallo en {r0:.2f}-{r1:.2f}s: {type(exc).__name__}: {exc}")
    if not nuevas: return palabras
    return sorted(list(palabras) + nuevas, key=lambda p: p[1])


def _parecido(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def corregir(palabras, guion, umbral_nombre=0.72, umbral=0.9):
    if not guion or not palabras:
        return [[w[0], w[1], w[2], None] for w in palabras]
    A = [norm(w[0]) for w in palabras]
    B = [norm(t[0]) for t in guion]
    voc_nom = sorted({norm(t[0]) for t in guion if t[1] is not None})
    voc_gen = sorted(set(B))
    vs, cache, Am = set(B), {}, list(A)
    for k, a in enumerate(A):
        if a and a not in vs and len(a) > 3:
            if a not in cache:
                m = difflib.get_close_matches(a, voc_nom, n=1, cutoff=0.75) \
                    or difflib.get_close_matches(a, voc_gen, n=1, cutoff=0.9)
                cache[a] = m[0] if m else a
            Am[k] = cache[a]
    asign = [None] * len(palabras)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, Am, B, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1): asign[i1 + k] = j1 + k
        elif tag == "replace":
            ptr = j1
            for i in range(i1, i2):
                mejor, mj = 0.0, None
                for j in range(ptr, j2):
                    r = _parecido(A[i], B[j])
                    if r > mejor: mejor, mj = r, j
                if mj is not None and mejor >= (umbral_nombre if guion[mj][1] is not None else umbral):
                    asign[i] = mj; ptr = mj + 1

    res = []
    for i, w in enumerate(palabras):
        j = asign[i]
        texto_final = w[0]  # El audio manda.
        
        if j is not None:
            # El guión SOLO corrige si la palabra es exactamente la misma (tildes, mayúsculas)
            a_n, b_n = norm(w[0]), norm(guion[j][0])
            if a_n == b_n:
                texto_final = guion[j][0]
            elif a_n not in vs and len(a_n) >= 4 and len(b_n) >= 4:
                # Whisper oyó mal un nombre/título (p. ej. "Patria" por "partida"): se corrige.
                # Una palabra que ya existe en el guion o que se parece poco NO se toca: el audio manda.
                r = _parecido(a_n, b_n)
                if (guion[j][1] is not None and r >= 0.72) or r >= 0.9:
                    texto_final = guion[j][0]
                
        res.append([texto_final, w[1], w[2], guion[j][1] if j is not None else None, j])

    for g in sorted({t[1] for t in guion if t[1] is not None}):
        js = [j for j, t in enumerate(guion) if t[1] == g]
        pos = [p for p, r in enumerate(res) if r[4] is not None and js[0] <= r[4] <= js[-1]]
        if len(pos) < max(1, 0.5 * len(js)) or pos[-1] - pos[0] + 1 > len(js) + 3:
            for p in pos: res[p][3] = None
            continue
        for p in range(pos[0], pos[-1] + 1): res[p][3] = g
    return [r[:4] for r in res]


_ultimos = []
_idioma_detectado = ""


def _sin_habla(texto):
    """Línea que no es habla: [Música], (risas), ♪ o solo signos.

    Concat descarta estas líneas antes de armar los subtítulos: Whisper las
    inventa sobre música y silencio y ensucian el SRT.
    """
    t = (texto or "").strip()
    if not t:
        return True
    if (t.startswith("[") and t.endswith("]")) or (t.startswith("(") and t.endswith(")")):
        return True
    return not any(c.isalnum() for c in t)


# Concat: un tramo que se repite 3 veces o más y cubre 6 palabras o más es
# un bucle de Whisper, no algo que se haya dicho. Se deja la última vuelta.
_LOOP_UNIDAD = 8
_LOOP_MINIMO = 6


def quitar_loops(palabras):
    """Quita frases que Whisper repite en bucle dentro de una misma línea."""
    if not palabras:
        return palabras
    planos = [norm(p[0]) for p in palabras]
    total = len(palabras)
    salida, i = [], 0
    while i < total:
        mejor = None
        for unidad in range(1, min(_LOOP_UNIDAD, total - i) + 1):
            veces = 1
            while i + (veces + 1) * unidad <= total and all(
                    planos[i + k] == planos[i + veces * unidad + k]
                    for k in range(unidad)):
                veces += 1
            cubre = veces * unidad
            if veces >= 3 and cubre >= _LOOP_MINIMO and (
                    mejor is None or cubre > mejor[0] * mejor[1]):
                mejor = (unidad, veces)
        if mejor:
            unidad, veces = mejor
            ultimo = i + (veces - 1) * unidad
            salida.extend(palabras[ultimo:ultimo + unidad])
            i += veces * unidad
        else:
            salida.append(palabras[i])
            i += 1
    return salida


def es_alucinacion(seg, estricto=True):
    t = norm(seg.text)
    _ultimos.append(t)
    del _ultimos[:-3]
    if _sin_habla(seg.text): return True
    if any(b in t for b in ALUCINACIONES): return True
    if not estricto: return False
    if t == "gracias": return True
    if len(_ultimos) == 3 and t and _ultimos[0] == _ultimos[1] == _ultimos[2]: return True
    return seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8


def _dlls_cuda():
    return gpu_runtime.preparar_cuda()


def transcribir(segs, prompt, progreso, modelo=None, p_ini=30, p_fin=90, idioma="auto", con_guion=False, hotwords=""):
    global _ultimos, _idioma_detectado
    _ultimos = []
    _idioma_detectado = "" if idioma in ("", "auto") else idioma
    language = None if idioma in ("", "auto") else idioma
    _dlls_cuda()
    from faster_whisper import WhisperModel

    total = 0.0
    for wav, _ in segs:
        salida = ejecutar_proceso(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav], capture_output=True)
        try: total += float(salida.strip())
        except Exception: pass
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

    if language is None:
        try:
            mejor = max(segs, key=lambda sg: os.path.getsize(sg[0]))
            # Concat: el idioma se escucha sobre varias ventanas de voz, no
            # sobre los primeros segundos (que suelen ser música o intro).
            muestra = cargar_wav(mejor[0])[:16000 * 90]
            try:
                det = model.detect_language(muestra, language_detection_segments=3)
            except TypeError:
                det = model.detect_language(muestra)
            if det and det[0]:
                language = str(det[0]); _idioma_detectado = language
                _log(f"Idioma fijado para todos los clips: {language}")
        except Exception as exc:
            _log(f"No se pudo fijar el idioma, queda en auto: {type(exc).__name__}: {exc}")

    def ejecutar(modelo_actual, dispositivo_actual):
        global _ultimos, _idioma_detectado
        _ultimos = []
        _idioma_detectado = "" if idioma in ("", "auto") else idioma
        hecho = 0.0
        palabras = []
        for wav, t0 in segs:
            if _cancelar_procesos.is_set(): raise RuntimeError("Proceso cancelado.")
            kw = dict(language=language, word_timestamps=True, initial_prompt=prompt or None,
                      vad_filter=False, beam_size=5, condition_on_previous_text=False)
            if hotwords and "hotwords" in inspect.signature(modelo_actual.transcribe).parameters:
                kw["hotwords"] = hotwords
            it, info = modelo_actual.transcribe(cargar_wav(wav), **kw)
            if not _idioma_detectado: _idioma_detectado = str(getattr(info, "language", "") or "")
            for segmento in it:
                if _cancelar_procesos.is_set(): raise RuntimeError("Proceso cancelado.")
                avance = (hecho + segmento.end) / total
                progreso(p_ini + (p_fin - p_ini) * avance, f"Transcribiendo en {dispositivo_actual}…")
                if es_alucinacion(segmento, estricto=not con_guion): continue
                for palabra in segmento.words or []:
                    inicio = t0 + palabra.start
                    fin = t0 + palabra.end
                    palabras.append([palabra.word.strip(), inicio, fin])
            hecho += info.duration
        progreso(p_fin, "Buscando palabras que faltan…")
        try: palabras = recuperar_huecos(palabras, segs, modelo_actual, language, prompt or "", velocidad=1.0)
        except Exception as exc: _log(f"[recuperar] error: {type(exc).__name__}: {exc}")
        try: palabras = quitar_loops(palabras)
        except Exception as exc: _log(f"[loops] error: {type(exc).__name__}: {exc}")
        return palabras

    try:
        etiqueta = (f"GPU NVIDIA ({nombre_gpu}, CUDA)" if nombre_gpu else "GPU (CUDA)")
        return ejecutar(model, etiqueta if dispositivo == "cuda" else "CPU")
    except Exception as exc:
        if _cancelar_procesos.is_set() or dispositivo != "cuda": raise
        progreso(p_ini, f"Falló CUDA ({type(exc).__name__}); repito la transcripción completa en CPU…")
        _log(f"Falló CUDA al transcribir; se reintenta en CPU: {type(exc).__name__}: {exc}")
        try:
            model_cpu = WhisperModel(MODEL, device="cpu", compute_type="int8")
            return ejecutar(model_cpu, "CPU (respaldo tras fallo de CUDA)")
        except Exception as exc_cpu:
            if _cancelar_procesos.is_set(): raise
            raise RuntimeError(f"Falló la GPU y tampoco se pudo completar en CPU. Error CUDA: {type(exc).__name__}: {exc}. Error CPU: {type(exc_cpu).__name__}: {exc_cpu}") from exc_cpu


def _factor_tempo_seguro(factor):
    factor = float(factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("El factor de velocidad debe ser positivo")
    tempo = 1.0 / factor
    partes = []
    while tempo < 0.5:
        partes.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2.0:
        partes.append("atempo=2.0")
        tempo /= 2.0
    partes.append(f"atempo={tempo:.9f}")
    return ",".join(partes)


def armar_bloques(palabras):
    """Agrupa palabras y sanea timestamps tras reescalar audio acelerado."""
    normalizadas = []
    ultimo_fin = 0.0
    for palabra in palabras:
        if len(palabra) < 4:
            continue
        texto, inicio, fin, grupo = palabra[:4]
        try:
            inicio = max(0.0, float(inicio))
            fin = max(inicio, float(fin))
        except (TypeError, ValueError):
            continue
        inicio = max(inicio, ultimo_fin)
        fin = max(fin, inicio)
        normalizadas.append([texto, inicio, fin, grupo])
        ultimo_fin = fin
    palabras = normalizadas
    bl = []
    i = 0
    while i < len(palabras):
        t, s, e, g = palabras[i]
        if g is not None:
            j = i
            while j + 1 < len(palabras) and palabras[j + 1][3] == g:
                j += 1
            partes = [limpiar_puntuacion_final(x[0]) for x in palabras[i:j + 1]]
            texto = " ".join(partes).strip()
            texto = _envolver_comillas(texto)
            bl.append([texto, s, palabras[j][2]])
            i = j + 1
        else:
            bl.append([limpiar_puntuacion_final(t), s, e])
            i += 1

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
    return (f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}")


def _reemplazar_reintentando(origen, destino, timeout=2.5):
    """os.replace() tolerando bloqueos transitorios de Windows.

    Pasa cuando Google Drive, el antivirus, OnlyOffice o Resolve tienen el
    archivo abierto un instante. Reintentamos unos segundos y, si sigue
    bloqueado, devolvemos False para guardar con otro nombre.
    """
    demora = 0.08
    fin = time.time() + timeout
    while True:
        try:
            os.replace(origen, destino)
            return True
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32) and not isinstance(exc, PermissionError):
                return False
            if time.time() >= fin:
                return False
            time.sleep(demora)
            demora = min(demora * 1.6, 0.5)


def _guardar_texto(path, contenido):
    """Guarda texto de forma atómica y tolerante a archivos bloqueados.

    Escribe en un temporal dentro de la misma carpeta y reemplaza el destino.
    Si el destino está abierto por otro programa, guarda una copia con fecha
    en vez de fallar con PermissionError.
    """
    path = os.path.abspath(path)
    carpeta = os.path.dirname(path) or "."
    os.makedirs(carpeta, exist_ok=True)

    # Si el archivo quedó con atributo de solo lectura, lo destrabamos.
    try:
        if os.path.exists(path) and not (os.stat(path).st_mode & stat.S_IWRITE):
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except OSError:
        pass

    try:
        fd, tmp = tempfile.mkstemp(prefix=".indie-", suffix=".tmp", dir=carpeta)
    except OSError as exc:
        # Una carpeta elegida en un volumen externo o protegida por Windows
        # puede permitir leer pero no crear temporales. Guardamos en Documentos
        # para no perder el SRT y devolvemos la ruta real al usuario.
        fallback = os.path.join(os.path.expanduser("~"), "Documents")
        if os.path.isdir(fallback) and os.path.normcase(fallback) != os.path.normcase(carpeta):
            return _guardar_texto(os.path.join(fallback, os.path.basename(path)), contenido)
        raise RuntimeError(
            f"No se pudo escribir en la carpeta '{carpeta}'. "
            f"Revisá permisos o si la carpeta está sincronizada/bloqueada: {exc}"
        ) from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())

        if _reemplazar_reintentando(tmp, path):
            return path

        # Si no se pudo reemplazar el destino, probamos Documentos antes de
        # devolver un error: el archivo generado nunca debe perderse.
        fallback = os.path.join(os.path.expanduser("~"), "Documents", os.path.basename(path))
        if os.path.normcase(fallback) != os.path.normcase(path):
            if _reemplazar_reintentando(tmp, fallback):
                return fallback

        # El destino sigue bloqueado: no perdemos el trabajo, guardamos copia.
        base, ext = os.path.splitext(path)
        sello = time.strftime("%Y%m%d-%H%M%S")
        for intento in range(1, 100):
            sufijo = f".{sello}" if intento == 1 else f".{sello}-{intento}"
            alterno = f"{base}{sufijo}{ext}"
            if os.path.exists(alterno):
                continue
            if _reemplazar_reintentando(tmp, alterno):
                return alterno
            break
        raise RuntimeError(
            f"No se pudo guardar '{os.path.basename(path)}' porque está abierto "
            f"o bloqueado por otro programa (Resolve, OnlyOffice, Google Drive o "
            f"el antivirus). Cerrá ese archivo y reintentá."
        )
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _envolver_srt(texto, ancho=42):
    """Parte el texto en dos líneas legibles, como Concat (42 caracteres).

    Una línea de 80 caracteres en pantalla se sale del cuadro. Se corta en
    un espacio cerca de la mitad; si hace falta, se envuelve por ancho.
    """
    texto = " ".join(str(texto or "").split())
    if len(texto) <= ancho:
        return texto
    if len(texto) <= 2 * ancho + 1:
        mitad = len(texto) / 2.0
        mejor = None
        for i, ch in enumerate(texto):
            if ch == " " and (mejor is None or abs(i - mitad) < abs(mejor - mitad)):
                mejor = i
        if mejor is not None and 0 < mejor < len(texto) - 1:
            return texto[:mejor].rstrip() + "\n" + texto[mejor + 1:].lstrip()
    lineas, actual = [], ""
    for palabra in texto.split():
        if not actual:
            actual = palabra
        elif len(actual) + 1 + len(palabra) <= ancho:
            actual += " " + palabra
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return "\n".join(lineas)


def escribir_srt(bloques, path):
    partes = []
    for n, (t, s, e) in enumerate(bloques, 1):
        partes.append(f"{n}\n{tc(s)} --> {tc(e)}\n<b>{_envolver_srt(t)}</b>\n\n")
    return _guardar_texto(path, "".join(partes))


_FIN_FRASE = re.compile(r"[.!?…][\"'”’)\]]*$")


_TG = tg.TraductorGoogle(cache_path=CACHE_TRAD_FILE, timeout=25)


def _cache_trad_cargar():
    return _TG.cache()


def _cache_trad_guardar():
    _TG.guardar()


def traducir_lineas(lineas, origen, destino, progreso=None, terminos=()):
    return _TG.traducir(lineas, origen, destino, terminos=list(terminos or ()), progreso=progreso)


def agrupar_frases(palabras, max_palabras=14, max_dur=7.0, pausa=0.85):
    unidades, actual = [], []
    for i, w in enumerate(palabras):
        actual.append(w)
        texto = str(w[0]).strip()
        nxt = palabras[i + 1] if i + 1 < len(palabras) else None
        hueco = (nxt[1] - w[2]) if nxt else 99.0
        fin = bool(_FIN_FRASE.search(texto)) or hueco >= pausa
        if len(actual) >= max_palabras or (w[2] - actual[0][1]) >= max_dur: fin = True
        if fin and actual:
            unidades.append(actual)
            actual = []
    if actual: unidades.append(actual)
    fusion = []
    for u in unidades:
        if (fusion and len(fusion[-1]) <= 2 and not _FIN_FRASE.search(str(fusion[-1][-1][0]).strip()) and (u[0][1] - fusion[-1][-1][2]) < 1.0):
            fusion[-1].extend(u)
        else: fusion.append(list(u))
    return fusion


def _partir_frases(texto, limite=84):
    texto = str(texto).strip()
    if len(texto) <= limite: return [texto] if texto else []
    partes, resto = [], texto
    while len(resto) > limite:
        corte = resto.rfind(", ", 0, limite + 1)
        if corte < int(limite * 0.45): corte = resto.rfind(" ", 0, limite + 1)
        if corte < int(limite * 0.45): corte = limite
        trozo = resto[:corte].strip(" ,;")
        if trozo: partes.append(trozo)
        resto = resto[corte:].strip(" ,;")
    if resto: partes.append(resto)
    return partes


def _partir_palabras(texto, duracion):
    palabras = str(texto).split()
    if len(palabras) <= 2: return [texto]
    objetivo = max(1, min(len(palabras), int(round(duracion / 0.85))))
    tam = max(1, math.ceil(len(palabras) / objetivo))
    return [" ".join(palabras[i:i + tam]) for i in range(0, len(palabras), tam)]


def _agrupar_oraciones(unidades, textos, max_chars=320, max_dur=12.0, max_gap=1.2):
    """Une fragmentos de una misma oración para traducir con contexto.

    Traducir "Me dijo que" / "no iba a venir" por separado destroza la
    gramática: Google no sabe quién habla ni en qué tiempo. Concat no
    traduce (transcribe en el idioma pedido), pero la lección es la misma:
    el texto se procesa en oraciones completas y después se reparte en el
    tiempo de cada fragmento.
    """
    grupos, cur_u, cur_t = [], [], []
    ini = fin = None
    for unidad, texto in zip(unidades, textos):
        if not unidad:
            continue
        u_ini, u_fin = float(unidad[0][1]), float(unidad[-1][2])
        if cur_u:
            anterior = cur_t[-1].strip()
            hueco = u_ini - fin
            dura = u_fin - ini
            if (_FIN_FRASE.search(anterior) or hueco >= max_gap
                    or len(" ".join(cur_t)) >= max_chars or dura >= max_dur):
                grupos.append((cur_u, " ".join(cur_t)))
                cur_u, cur_t, ini = [], [], None
        if not cur_u:
            ini = u_ini
        cur_u.append(unidad)
        cur_t.append(texto)
        fin = u_fin
    if cur_u:
        grupos.append((cur_u, " ".join(cur_t)))
    return grupos


def _repartir_texto(texto, n):
    """Parte una traducción en n trozos en límites de palabra.

    Los cortes se buscan cerca de la posición proporcional, así cada trozo
    cae sobre el fragmento de audio que le corresponde sin partir palabras.
    """
    texto = " ".join(str(texto or "").split())
    if n <= 1 or not texto:
        return [texto] * max(1, n)
    if len(texto) <= n:
        return [texto] + [""] * (n - 1)
    cortes, inicio = [], 0
    for k in range(1, n):
        objetivo = round(len(texto) * k / n)
        ventana = max(2, len(texto) // (n * 2))
        lo = max(inicio + 1, objetivo - ventana)
        hi = min(len(texto) - 1, objetivo + ventana)
        mejor = None
        for j in range(lo, hi + 1):
            if texto[j] in " ,;:":
                if mejor is None or abs(j - objetivo) < abs(mejor - objetivo):
                    mejor = j
        if mejor is None:
            mejor = texto.rfind(" ", inicio + 1, objetivo)
            if mejor <= inicio:
                mejor = texto.find(" ", objetivo)
            if mejor <= inicio or mejor >= len(texto):
                mejor = objetivo
        cortes.append(mejor)
        inicio = mejor + 1
    partes, previo = [], 0
    for c in cortes:
        partes.append(texto[previo:c].strip(" ,;:"))
        previo = c + 1
    partes.append(texto[previo:].strip(" ,;:"))
    return partes


def ajustar_ritmo(bloques, cps=17.0, min_dur=0.6, max_dur=8.0):
    """Da tiempo de lectura: un subtítulo largo no puede durar 0,3 s.

    Igual que Concat, se calcula lo que lleva leerlo (17 caracteres por
    segundo) y se estira hasta el inicio del siguiente subtítulo.
    """
    total = len(bloques)
    for i, b in enumerate(bloques):
        try:
            dur = float(b[2]) - float(b[1])
            necesario = min(max_dur, max(min_dur, len(str(b[0])) / cps))
        except (TypeError, ValueError):
            continue
        if dur >= necesario:
            continue
        limite = (float(bloques[i + 1][1]) - 0.04) if i + 1 < total else float(b[1]) + necesario
        b[2] = max(float(b[2]), min(limite, float(b[1]) + necesario))
    return bloques


def bloques_traduccion(grupos, traducciones, estilo="palabras"):
    """Reparte cada oración traducida sobre el audio de sus fragmentos."""
    bloques = []
    for unidades, texto in zip(grupos, traducciones):
        texto = (texto or "").strip()
        if not texto or not unidades:
            continue
        partes = _repartir_texto(texto, len(unidades))
        for unidad, parte in zip(unidades, partes):
            parte = (parte or "").strip(" ,;:-")
            if not parte:
                continue
            inicio = float(unidad[0][1])
            final = float(unidad[-1][2])
            if final <= inicio: final = inicio + 0.4
            dur = max(0.3, final - inicio)
            trozos = (_partir_palabras(parte, dur) if estilo == "palabras"
                      else _partir_frases(parte))
            if not trozos:
                continue
            pesos = [max(1, len(p)) for p in trozos]
            total = sum(pesos)
            t = inicio
            for k, trozo in enumerate(trozos):
                if k == len(trozos) - 1: fin = final
                else: fin = min(final, t + dur * pesos[k] / total)
                if fin <= t: fin = t + 0.15
                bloques.append([trozo, t, fin])
                t = fin
    for k in range(len(bloques) - 1):
        if bloques[k][2] > bloques[k + 1][1]:
            bloques[k][2] = max(bloques[k][1] + 0.1, bloques[k + 1][1])
    for b in bloques:
        if b[2] <= b[1]: b[2] = b[1] + 0.15
    return ajustar_ritmo(bloques)


def _respaldar(ruta):
    """Mueve el archivo que ya existía a un respaldo CON FECHA.

    Antes se guardaba como `.bak` y se borraba el `.bak` anterior, así que cada
    corrida destruía la transcripción previa. Ahora cada versión queda con su
    sello: `nombre.20261027-153012.srt.bak`, y nunca se pierde nada.
    """
    if not ruta or not os.path.exists(ruta): return
    try:
        if os.path.getsize(ruta) == 0: return
        base, ext = os.path.splitext(ruta)
        sello = time.strftime("%Y%m%d-%H%M%S")
        bak = f"{base}.{sello}{ext}.bak"
        n = 1
        while os.path.exists(bak):
            n += 1
            bak = f"{base}.{sello}-{n}{ext}.bak"
        if not _reemplazar_reintentando(ruta, bak, timeout=0.5):
            raise OSError("archivo bloqueado")
    except Exception: pass


def _propiedad_clip(clip, nombre):
    try:
        valor = clip.GetClipProperty(nombre)
        if isinstance(valor, dict): valor = valor.get(nombre, "")
        if valor not in (None, ""): return str(valor).strip()
    except Exception: pass
    try:
        propiedades = clip.GetClipProperty() or {}
        return str(propiedades.get(nombre, "") or "").strip()
    except Exception: return ""


def _props_clip(clip):
    """Todas las propiedades del clip en UNA sola llamada.

    Pedir `GetClipProperty("Type")` cuesta ~5 ms; el diccionario completo
    ~1 ms. Recorriendo el Media Pool esa diferencia se siente en la ventana,
    así que se lee todo junto una vez por clip.
    """
    try:
        datos = clip.GetClipProperty() or {}
    except Exception:
        datos = {}
    props = {str(k).strip().lower(): str(v or "").strip() for k, v in datos.items()}
    if not props:
        for clave in ("Type", "File Path", "Clip Name"):
            valor = _propiedad_clip(clip, clave)
            if valor:
                props[clave.lower()] = valor
    return props


def _tiene_textplus(clip):
    try: cantidad = int(clip.GetFusionCompCount() or 0)
    except Exception: cantidad = 1
    for indice in range(1, min(max(cantidad, 1), 64) + 1):
        try:
            comp = clip.GetFusionCompByIndex(indice)
            if comp and comp.GetToolList(False, "TextPlus"): return True
        except Exception: continue
    return False


# Tipos que nunca son plantilla de subtítulos (audio, video, líneas,
# generadores de ajuste, etc.).
_TIPOS_NO_TEXTO = ("subtitle", "timeline", "multicam", "compound", "still",
                   "audio", "video", "matte", "solid color")

# Tipos que en Resolve sí llevan texto adentro.
_TIPOS_CON_TEXTO = ("fusion title", "fusion titles", "title", "titulo", "título",
                    "titre", "titel", "titolo", "text+", "text plus")

_NOMBRES_CON_TEXTO = ("text+", "text plus", "texto", "text", "title", "titulo",
                      "título", "titre", "titel", "titolo", "subtitle",
                      "subtítulo", "subtitulo", "lower third", "caption",
                      "crédito", "credito")

_NOMBRES_NO_TEXTO = ("adjustment", "ajuste", "solid color", "color generator",
                     "color bars", "noise", "blank", "transparent", "matte")


def _es_plantilla_media_pool(clip, en_carpeta_plantillas=False, props=None):
    """True sólo si el elemento puede llevar texto (Fusion Title o Text+).

    Antes entraba cualquier cosa de tipo "generator" o cualquier clip que
    estuviera dentro de una carpeta llamada "plantillas": por eso aparecían
    capas de ajuste, luces y zooms que después no tienen dónde escribir el
    subtítulo.
    """
    p = props if props is not None else _props_clip(clip)
    tipo = p.get("type", "").lower()
    ruta = p.get("file path", "").lower()
    nombre = " ".join((p.get("clip name", ""), p.get("file name", ""),
                       str(clip.GetName() or ""))).lower()
    if any(x in tipo for x in _TIPOS_NO_TEXTO): return False
    if any(x in nombre for x in _NOMBRES_NO_TEXTO): return False
    if _tiene_textplus(clip): return True
    if any(x in tipo for x in _TIPOS_CON_TEXTO): return True
    if "generator" in tipo:
        # "Text+" del Effects Library también es un Generator: sólo se acepta
        # si el nombre dice que es texto.
        return any(x in nombre for x in _NOMBRES_CON_TEXTO)
    if "fusion" in tipo and en_carpeta_plantillas:
        return True
    if en_carpeta_plantillas:
        if ruta.endswith((".setting", ".comp", ".drfx")): return True
        return any(x in nombre for x in _NOMBRES_CON_TEXTO)
    return False


def _clips(carpeta, en_carpeta_plantillas, out, ruta=()):
    for c in carpeta.GetClipList() or []:
        props = _props_clip(c)
        if not _es_plantilla_media_pool(c, en_carpeta_plantillas, props): continue
        tipo = props.get("type", "").lower()
        ruta_archivo = props.get("file path", "").lower()
        if "subtitle" in tipo or ruta_archivo.endswith(".srt"): continue
        etiqueta = " / ".join((*ruta, c.GetName()))
        out[etiqueta] = c
    for f in carpeta.GetSubFolderList() or []:
        nombre = f.GetName().strip()
        marcada = (en_carpeta_plantillas or nombre.lower() in CARPETA_PLANTILLAS or nombre.lower() in ("plantilla", "templates", "template"))
        _clips(f, marcada, out, (*ruta, nombre))


_PLANTILLAS_CACHE = {}
_PLANTILLAS_T = 0.0
PLANTILLAS_TTL = 20.0


def plantillas(forzar=False):
    """Lista de plantillas del Media Pool, con caché corta.

    El recorrido completo tarda ~2,5 s en un pool grande. La app lo pedía
    cada 5 segundos y eso se sentía como tirones; ahora se guarda 20 s y
    sólo se recalcula al tocar ACTUALIZAR o cuando vence.
    """
    global _PLANTILLAS_CACHE, _PLANTILLAS_T
    ahora = time.monotonic()
    if (not forzar and _PLANTILLAS_CACHE
            and (ahora - _PLANTILLAS_T) < PLANTILLAS_TTL):
        return dict(_PLANTILLAS_CACHE)
    out = {}
    _clips(mp.GetRootFolder(), False, out)
    _PLANTILLAS_CACHE = dict(out)
    _PLANTILLAS_T = ahora
    return out


def _pista_activa(tipo, indice, activa):
    try: return bool(timeline.SetTrackEnable(tipo, int(indice), bool(activa)))
    except Exception: return False


_LOTE_INSERCION = 4


def _textplus_de_item(item):
    try:
        comp = item.GetFusionCompByIndex(1)
    except Exception:
        comp = None
    if not comp:
        return None
    try:
        tools = comp.GetToolList(False, "TextPlus") or {}
    except Exception:
        return None
    return next(iter(tools.values()), None)


def _borrar_items(items):
    items = [i for i in (items or []) if i is not None]
    if not items:
        return True
    try:
        timeline.DeleteClips(items)
        return True
    except Exception as exc:
        _log(f"[plantilla] no pude retirar clips parciales: {type(exc).__name__}: {exc}")
        return False


def _limpiar_pista_insercion(pista):
    """Retira sólo la pista nueva que creó aplicar_plantilla, si quedó vacía."""
    try:
        actuales = timeline.GetItemListInTrack("video", pista) or []
        if actuales:
            _borrar_items(actuales)
        restantes = timeline.GetItemListInTrack("video", pista) or []
        if not restantes and timeline.GetTrackCount("video") == pista:
            return bool(timeline.DeleteTrack("video", pista))
        return not restantes
    except Exception as exc:
        _log(f"[plantilla] no pude limpiar pista parcial {pista}: {type(exc).__name__}: {exc}")
        return False


def aplicar_plantilla(tpl, bloques, f_ini, progreso, p_ini=92, p_fin=100):
    """Inserta títulos en lotes, informa antes de cada llamada a Resolve y
    revierte la pista nueva si un lote falla o se cancela.
    """
    if not bloques:
        return True
    fps = FPS or 24.0
    total = len(bloques)
    num_lotes = 1 + max(0, math.ceil((total - 1) / _LOTE_INSERCION))
    progreso(p_ini, "Creando la pista de subtítulos en Resolve…")
    inicio_track = time.monotonic()
    try:
        timeline.AddTrack("video")
    except Exception as exc:
        raise RuntimeError(f"Resolve no pudo crear una pista de subtítulos: {exc}") from exc
    pista = timeline.GetTrackCount("video")
    _log(f"[plantilla] pista {pista} creada; {total} títulos en {num_lotes} lotes")

    insertados = []
    hechos = 0
    primera = True
    lote = 0

    while hechos < total:
        if _cancelar_procesos.is_set():
            limpiada = _limpiar_pista_insercion(pista)
            estado = "La pista temporal se retiró." if limpiada else "Revisá la pista: Resolve no pudo retirar todos los clips parciales."
            raise RuntimeError(f"Proceso cancelado durante la inserción. {estado}")

        lote += 1
        tam = 1 if primera else min(_LOTE_INSERCION, total - hechos)
        tramo = bloques[hechos:hechos + tam]
        infos = [{"mediaPoolItem": tpl, "startFrame": 0,
                  "endFrame": max(1, round((e - s) * fps)),
                  "trackIndex": pista, "recordFrame": f_ini + round(s * fps)}
                 for _, s, e in tramo]
        pct = p_ini + (p_fin - p_ini) * hechos / total
        progreso(pct, f"Insertando lote {lote}/{num_lotes} ({hechos}/{total} subtítulos)…")
        t0 = time.monotonic()
        _log(f"[plantilla] AppendToTimeline lote {lote}/{num_lotes} ({len(tramo)} clips), inicio")
        try:
            items = mp.AppendToTimeline(infos) or []
        except Exception as exc:
            _log(f"[plantilla] AppendToTimeline lote {lote} falló tras {time.monotonic()-t0:.1f}s: {type(exc).__name__}: {exc}")
            limpiada = _limpiar_pista_insercion(pista)
            extra = "Se retiró la pista temporal." if limpiada else "Quedaron clips parciales; revisá la pista nueva antes de reintentar."
            raise RuntimeError(f"Resolve no terminó de insertar los subtítulos (lote {lote}/{num_lotes}). {extra}") from exc
        _log(f"[plantilla] AppendToTimeline lote {lote} retornó {len(items)}/{len(tramo)} en {time.monotonic()-t0:.1f}s")
        if len(items) != len(tramo):
            limpiada = _limpiar_pista_insercion(pista)
            extra = "Se retiró la pista temporal." if limpiada else "Quedaron clips parciales; revisá la pista nueva antes de reintentar."
            raise RuntimeError(f"Resolve insertó {len(items)}/{len(tramo)} clips en el lote {lote}/{num_lotes}. {extra}")
        insertados.extend(items)

        for indice, (item, (texto, _, _)) in enumerate(zip(items, tramo), 1):
            _log(f"[plantilla] configurando Text+ del lote {lote}, clip {indice}/{len(tramo)}")
            tool = _textplus_de_item(item)
            if tool is None:
                limpiada = _limpiar_pista_insercion(pista)
                extra = "Se retiró la pista temporal." if limpiada else "Quedaron clips parciales; revisá la pista nueva."
                raise RuntimeError(
                    "El elemento elegido no contiene un nodo Text+ editable. "
                    "Elegí una plantilla de tipo Fusion Title o Text+. " + extra)
            tool.StyledText = texto

        hechos += tam
        primera = False
        pct = p_ini + (p_fin - p_ini) * hechos / total
        progreso(pct, f"Lote {lote}/{num_lotes} listo ({hechos}/{total} subtítulos).")
        # Deja que Resolve y Tkinter respiren entre tandas.
        time.sleep(0.005)

    _log(f"[plantilla] insertados {len(insertados)}/{total} títulos en {time.monotonic()-inicio_track:.1f}s")
    return len(insertados) == total


def carpeta_subtitulos():
    root = mp.GetRootFolder()
    for f in root.GetSubFolderList() or []:
        if f.GetName().strip().lower() == "subtitulos": return f
    return mp.AddSubFolder(root, "SUBTITULOS")


def carpeta_de_timeline():
    uid = None
    try:
        mpi = timeline.GetMediaPoolItem()
        uid = mpi.GetUniqueId() if mpi else None
    except Exception: pass
    por_nombre = []
    def buscar(carpeta):
        for c in carpeta.GetClipList() or []:
            try:
                if uid and c.GetUniqueId() == uid: return carpeta
                if (c.GetName() == TL_NAME and "timeline" in (c.GetClipProperty("Type") or "").lower()): por_nombre.append(carpeta)
            except Exception: pass
        for f in carpeta.GetSubFolderList() or []:
            r = buscar(f)
            if r: return r
        return None
    exacta = buscar(mp.GetRootFolder())
    return exacta or (por_nombre[0] if por_nombre else None)


def importar_srt(srt):
    previa = mp.GetCurrentFolder()
    try:
        destino = carpeta_de_timeline() or carpeta_subtitulos()
        if (destino and destino.GetName().strip().lower() in CARPETA_PLANTILLAS): destino = carpeta_subtitulos()
        if destino: mp.SetCurrentFolder(destino)
        return mp.ImportMedia([srt]) or []
    finally:
        if previa: mp.SetCurrentFolder(previa)


def agregar_srt_a_linea(items, f_ini=None):
    if not items: return False
    record = f_ini if f_ini is not None else TL_START
    for intento in ([{"mediaPoolItem": items[0], "recordFrame": record}], items):
        try:
            if mp.AppendToTimeline(intento): return True
        except Exception: pass
    return False


SILENCIO_MAX = 0.5


def _nombre_seguro(t):
    return re.sub(r'[<>:"/\\|?*]', "-", str(t)).strip() or "timeline"


def carpeta_salida(cfg):
    for ruta in (cfg.get("srt"), cfg.get("guion")):
        if ruta:
            d = os.path.dirname(ruta)
            if d and os.path.isdir(d): return d
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
    if cur: frases.append(cur)

    partes = [f"{titulo}\n{len(pal)} palabras\n\n", "TEXTO\n",
              " ".join(w[0] for w in pal) + "\n\n",
              "CON TIEMPOS (min:seg desde el inicio de la timeline)\n"]
    for fr in frases:
        partes.append(f"[{mmss(fr[0][1] + off)}] " + " ".join(w[0] for w in fr) + "\n")
    return _guardar_texto(ruta, "".join(partes))


def comparar_con_guion(pal, guion, segs, f_ini, cfg):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try: import verificacion
    except ImportError: raise RuntimeError("Falta verificacion.py: tiene que estar en la misma carpeta que subtitulos.py.")
    return verificacion.verificar(pal, guion, segs, f_ini, timeline, FPS, TL_START, cargar_wav, SILENCIO_MAX, cfg["guion"], TL_NAME)


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
    aviso_srt = ""
    pistas = [int(p) for p in (cfg.get("pistas") or [cfg.get("pista") or 1])] or [1]
    detalle_pistas = " + ".join(f"A{p}" for p in pistas)

    if not (hacer_subs or hacer_txt or hacer_comp): raise RuntimeError("Marcá al menos una opción.")
    if hacer_comp and not tiene_guion: raise RuntimeError("Para comparar con el guion necesito el guion.")

    progreso(2, "Leyendo guion..." if tiene_guion else "Preparando...")
    texto_guion = leer_guion(cfg["guion"]) if tiene_guion else ""
    guion = tokenizar_guion(texto_guion) if texto_guion else []
    if tiene_guion and not guion: raise RuntimeError("No pude sacar palabras del guion.")

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
        segs = segmentos_audio(pistas, f_ini, f_fin, tmp, progreso=progreso)
        if not segs: raise RuntimeError(f"No hay audio en {detalle_pistas} dentro de ese rango.")
        if len(pistas) > 1: progreso(20, f"Mezclando {len(pistas)} pistas en un solo audio...")
        
        nombres = nombres_del_guion(guion) if guion else []
        glos = glosario_del_guion(texto_guion) if texto_guion else []
        for n in nombres:
            if n.lower() not in {x.lower() for x in glos}: glos.append(n)
        prompt = ", ".join(glos)[:400]
        terminos = traduccion_google.normalizar_terminos(glos, texto_guion, cfg.get("terminos", ""))
        pal = transcribir(segs, prompt, progreso, p_ini=30, p_fin=90, idioma=idioma_audio,
                          con_guion=bool(guion), hotwords=", ".join(glos)[:200])
        
        # Liberación de memoria para evitar el congelamiento post-transcripción
        gc.collect()
        
        pal_trad = pal
        if hacer_subs:
            try: env = construir_envolvente(segs)
            except Exception: env = None
            pal = quitar_palabras_sin_voz(pal, env)
            pal = ajustar_a_voz(pal, env)
            pal_trad = pal
            if guion:
                pal_trad = corregir(pal, guion)
                bloques = armar_bloques(pal_trad)
            else:
                bloques = armar_bloques([[w[0], w[1], w[2], None] for w in pal])
        if traducir and pal_trad:
            progreso(83, "Preparando traducción...")
            unidades = agrupar_frases(pal_trad)
            pares = []
            for u in unidades:
                texto = re.sub(r"\s+", " ", " ".join(str(w[0]).strip() for w in u)).strip()
                if texto: pares.append((u, texto))
            if not pares: aviso_trad = "No hay texto para traducir."
            else:
                origen = idioma_audio if idioma_audio != "auto" else (_idioma_detectado or "auto")
                if origen and origen != "auto" and origen == destino:
                    aviso_trad = (f"El audio ya está en {NOMBRES_IDIOMA.get(destino, destino)}: "
                                  "no hace falta una pista traducida.")
                else:
                    # Traducir oraciones completas: da gramática y pronombres
                    # coherentes; después cada oración se reparte en su audio.
                    oraciones = _agrupar_oraciones([u for u, _ in pares],
                                                   [t for _, t in pares])
                    try:
                        traducciones = traducir_lineas([t for _, t in oraciones], origen,
                                                       destino, progreso, terminos=terminos)
                        bloques_trad = bloques_traduccion([u for u, _ in oraciones],
                                                          traducciones, estilo_trad)
                    except Exception as ex: aviso_trad = f"No pude traducir: {ex}"
        if hacer_comp:
            progreso(88, "Comparando con el guion...")
            try: resumen_comp = comparar_con_guion(pal, guion, segs, f_ini, cfg)
            except Exception as ex: resumen_comp = f"No pude comparar con el guion: {ex}"

    partes = []
    if hacer_txt:
        progreso(90, "Guardando transcripción...")
        # La transcripción sigue el nombre/destino elegido para el SRT: si
        # elegiste "black parade.srt", el texto es "black parade_transcripcion.txt".
        # Antes siempre caía en "<timeline>_transcripcion.txt" y pisaba la anterior.
        if cfg.get("srt"):
            raiz_txt = os.path.splitext(cfg["srt"])[0]
        else:
            raiz_txt = os.path.join(carpeta_salida(cfg), _nombre_seguro(TL_NAME))
        ruta_txt = f"{raiz_txt}_transcripcion.txt"
        _respaldar(ruta_txt)
        ruta_txt = escribir_transcripcion(pal, off, ruta_txt, TL_NAME) or ruta_txt
        partes.append(f"TRANSCRIPCIÓN GUARDADA ({len(pal)} palabras)\n{ruta_txt}")
    if hacer_comp and resumen_comp: partes.append(resumen_comp)

    if hacer_subs:
        srt = cfg["srt"] or os.path.join(carpeta_salida(cfg), _nombre_seguro(TL_NAME) + ".srt")
        os.makedirs(os.path.dirname(srt) or ".", exist_ok=True)
        ruta_trad = ""
        if bloques_trad:
            raiz = os.path.splitext(srt)[0]
            ruta_trad = f"{raiz}_{destino.upper()}.srt"

        _respaldar(srt)
        if ruta_trad: _respaldar(ruta_trad)

        for b in bloques:
            b[0] = limpiar_puntuacion_final(b[0])
            if not b[0]: b[0] = "…"

        progreso(91, "Guardando SRT...")
        srt_pedido = srt
        srt = escribir_srt([[t, s + off, e + off] for t, s, e in bloques], srt) or srt
        if os.path.normcase(os.path.abspath(srt)) != os.path.normcase(os.path.abspath(srt_pedido)):
            aviso_srt = (f"El archivo SRT original estaba bloqueado por otro programa.\n"
                         f"Se guardó una copia como:\n{srt}\n"
                         f"Si querés reemplazar el original, cerralo y volvé a generar.")
        if ruta_trad:
            ruta_trad_pedida = ruta_trad
            ruta_trad = escribir_srt([[t, s + off, e + off] for t, s, e in bloques_trad], ruta_trad) or ruta_trad
            if os.path.normcase(os.path.abspath(ruta_trad)) != os.path.normcase(os.path.abspath(ruta_trad_pedida)):
                aviso_srt += (f"\n\nLa traducción también estaba bloqueada; se guardó como:\n{ruta_trad}")
        if solo_srt:
            msg = (f"SRT GENERADO CORRECTAMENTE\n{len(bloques)} subtítulos originales\n{srt}")
            if ruta_trad: msg += (f"\n\nTRADUCCIÓN ({NOMBRES_IDIOMA.get(destino, destino).upper()})\n{len(bloques_trad)} subtítulos\n{ruta_trad}")
            if aviso_srt: msg += f"\n\n{aviso_srt}"
            if aviso_trad: msg += f"\n\n{aviso_trad}"
            partes.insert(0, msg)
            progreso(100, "\n\n".join(partes))
            return
        items = importar_srt(srt)
        items_trad = importar_srt(ruta_trad) if ruta_trad else []
        nombre_destino = NOMBRES_IDIOMA.get(destino, destino)
        if cfg["plantilla"]:
            if ruta_trad:
                ok = aplicar_plantilla(cfg["plantilla"], [[b[0], b[1], b[2]] for b in bloques], f_ini, progreso, 92, 96)
                pista_original = timeline.GetTrackCount("video")
                ok2 = aplicar_plantilla(cfg["plantilla"], [[b[0], b[1], b[2]] for b in bloques_trad], f_ini, progreso, 96, 100)
                oculta = False
                if ok and ok2:
                    oculta = _pista_activa("video", pista_original, False)
                    msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}\nDos pistas de video con plantilla")
                    if oculta: msg += "\nLa pista original quedó desactivada: activala en Resolve si la querés ver."
                    else: msg += "\nDejá visible la pista que quieras (hay dos superpuestas)."
                else: msg = (f"SUBTÍTULOS GENERADOS PARCIALMENTE\n{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}.\nRevisá la línea: algo no se colocó.")
            else:
                ok = aplicar_plantilla(cfg["plantilla"], [[b[0], b[1], b[2]] for b in bloques], f_ini, progreso, 92, 100)
                if ok: msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n{len(bloques)} subtítulos con plantilla")
                else: msg = (f"SUBTÍTULOS GENERADOS\n{len(bloques)} subtítulos.\nRevisá la línea: algo no se colocó.")
        else:
            progreso(95, "Agregando SRT a la línea...")
            ok = agregar_srt_a_linea(items, f_ini)
            ok2 = agregar_srt_a_linea(items_trad, f_ini) if ruta_trad else False
            if ok and (not ruta_trad or ok2):
                if ruta_trad: msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n{len(bloques)} originales + {len(bloques_trad)} en {nombre_destino}\nDos pistas de subtítulos en la línea")
                else: msg = (f"SUBTÍTULOS GENERADOS CORRECTAMENTE\n{len(bloques)} subtítulos en la línea")
            else: msg = (f"SRT GENERADO CORRECTAMENTE\n{len(bloques)} subtítulos.\nEl SRT fue guardado e importado, pero no pude agregarlo automáticamente a la línea.")
        partes.insert(0, msg)
        if aviso_srt: partes.append(aviso_srt)
        if ruta_trad and ruta_trad not in msg: partes.append(f"TRADUCCIÓN: {ruta_trad}")
        if aviso_trad: partes.append(aviso_trad)

    progreso(100, "\n\n".join(partes) if partes else "LISTO")


def herramientas_faltantes():
    faltan = []
    for exe in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([exe, "-version"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10, check=True)
        except Exception:
            faltan.append(exe)
    return faltan


def verificar_herramientas(root=None, app=None):
    """Comprueba ffmpeg/ffprobe en segundo plano, sin frenar la apertura."""

    def avisar(msg):
        def dialogo():
            try:
                messagebox.showerror("Indie Now - Subtítulos", msg, parent=root)
            except Exception:
                print("ERROR:", msg)
            if app is not None:
                app.cerrar()
            else:
                sys.exit(1)
        if root is not None:
            try:
                root.after(0, dialogo)
                return
            except Exception:
                pass
        dialogo()

    def trabajo():
        faltan = herramientas_faltantes()
        if not faltan:
            return
        avisar("No encontré: " + ", ".join(faltan) + ".\n\n"
               "Instalalos (winget install Gyan.FFmpeg) y reiniciá esta app.")

    if root is None:
        trabajo()
        return
    threading.Thread(target=trabajo, daemon=True).start()


class Backend:
    def config(self): return cargar_config()
    def guardar(self, cfg): guardar_config(cfg)
    def existe(self, ruta): return bool(ruta) and os.path.exists(ruta)
    def info_timeline(self):
        info = leer_timeline_actual()
        if not info: return None
        proyecto, nombre = info
        return {"nombre": nombre, "proyecto": proyecto, "fps": float(FPS)}
    def pistas(self):
        try: total = int(timeline.GetTrackCount("audio") or 0)
        except Exception: total = 0
        salida = []
        for n in range(1, total + 1):
            try: items = timeline.GetItemListInTrack("audio", n) or []
            except Exception: items = []
            nombres, primer_inicio = [], None
            for it in items:
                try:
                    mpi = it.GetMediaPoolItem()
                    nombre = (mpi.GetName() if mpi else "") or ""
                except Exception: nombre = ""
                if nombre and nombre not in nombres: nombres.append(nombre)
                try:
                    inicio = int(it.GetStart())
                    if primer_inicio is None or inicio < primer_inicio: primer_inicio = inicio
                except Exception: pass
            if len(nombres) > 2: titulo = ", ".join(nombres[:2]) + f" +{len(nombres) - 2}"
            else: titulo = ", ".join(nombres)
            if primer_inicio is not None:
                seg = max(0.0, (primer_inicio - TL_START) / FPS)
                cuando = f"{int(seg // 60):02d}:{int(seg % 60):02d}"
                detalle = (f"{len(items)} clip{'s' if len(items) != 1 else ''} · desde {cuando}")
            else: detalle = "sin clips"
            salida.append({"n": n, "label": f"A{n}", "clips": len(items), "titulo": titulo or "Sin clips", "detalle": detalle, "derecha": ("VACÍA" if not items else f"{len(items)} CLIP{'S' if len(items) != 1 else ''}")})
        return salida
    def plantillas(self, forzar=False):
        nombres = sorted(plantillas(forzar=forzar).keys(), key=lambda s: s.lower())
        return [SOLO] + nombres
    def correr(self, cfg, progreso):
        # Una cancelación anterior no debe impedir el próximo trabajo.
        _cancelar_procesos.clear()
        cfg = dict(cfg)
        nombre_tpl = cfg.get("plantilla") or SOLO
        item = None
        if nombre_tpl != SOLO:
            try: item = plantillas().get(nombre_tpl)
            except Exception: item = None
            if item is None:
                # Puede ser que la caché todavía no tenga la plantilla nueva.
                try: item = plantillas(forzar=True).get(nombre_tpl)
                except Exception: item = None
            if item is None:
                raise RuntimeError(
                    "La plantilla elegida ya no está en el Media Pool. "
                    "Tocá ACTUALIZAR y elegí otra.")
        cfg["plantilla"] = item
        generar(cfg, progreso)
    def cancelar(self): cerrar_procesos()
    def registrar_error(self, exc, tb): _log("ERROR\n" + (tb or repr(exc)))


def main():
    global _cancelar_procesos
    _cancelar_procesos.clear()

    backend = Backend()
    root = tk.Tk()

    # La ventana se arma completa fuera de pantalla y aparece de una sola vez:
    # nada de primer fotograma vacío ni textos de espera.
    root.withdraw()
    alpha_ok = True
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        alpha_ok = False
    indie_ui.init_fonts(root)
    app = SubtitulosApp(root, backend)
    app._alpha_ok = alpha_ok
    root.protocol("WM_DELETE_WINDOW", app.cerrar)

    # ffmpeg/ffprobe se comprueban después, para no demorar la apertura.
    root.after(900, lambda: verificar_herramientas(root, app))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        _log(f"fatal: {ex!r}\n{traceback.format_exc()}")
        try: messagebox.showerror("Indie Now - Subtítulos", str(ex))
        except Exception: print("ERROR:", ex)
