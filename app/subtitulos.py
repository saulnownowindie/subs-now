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
    "graciasporverelvideo", "graciasporver", "suscribete", "subtitulosrealizadospor",
    "subtitulosporlacomunidad", "amaraorg", "graciasporvernos",
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

def segmentos_audio(pistas, f_ini, f_fin, tmp):
    por_pista = []
    for pista in pistas:
        segmentos = []
        try:
            items = timeline.GetItemListInTrack("audio", pista) or []
        except Exception:
            items = []
            
        for i, it in enumerate(items):
            a = max(it.GetStart(), f_ini)
            b = min(it.GetEnd(), f_fin)
            if b <= a:
                continue
                
            try:
                mpi = it.GetMediaPoolItem()
                if not mpi: continue
                src = mpi.GetClipProperty("File Path")
                if not src or not os.path.exists(src): continue
                
                # Fórmula exacta de cortes.py
                off = (it.GetLeftOffset() + (a - it.GetStart())) / FPS
                dur = (b - a) / FPS
                wav = os.path.join(tmp, f"p{pista}_seg{i}.wav")
                
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error", 
                    "-ss", f"{off:.3f}", "-t", f"{dur:.3f}",
                    "-i", src, "-ac", "1", "-ar", "16000", wav
                ]
                ejecutar_proceso(cmd, capture_output=True)
                
                if os.path.exists(wav) and os.path.getsize(wav) > 44:
                    segmentos.append((wav, (a - f_ini) / FPS))
            except Exception as e:
                _log(f"Error procesando item de audio (pista {pista}, clip {i}): {e}")
                continue
                
        por_pista.append((pista, segmentos))
        
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


def recuperar_huecos(palabras, segs, modelo, language, glosario="", min_hueco=0.25, min_voz=0.20):
    """Busca tramos con voz donde no quedó ninguna palabra y los vuelve a transcribir
    (sin guion en el prompt). Lo que dice el audio manda."""
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
                kw = dict(language=language, word_timestamps=True, vad_filter=False, beam_size=5,
                          condition_on_previous_text=False, initial_prompt=glosario or None)
                it, _ = modelo.transcribe(trozo, **kw)
                for seg in it:
                    if any(x in norm(seg.text) for x in ALUCINACIONES): continue
                    if seg.no_speech_prob > 0.6: continue
                    for pw in seg.words or []:
                        ws, we = w0 + pw.start, w0 + pw.end
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


def es_alucinacion(seg, estricto=True):
    t = norm(seg.text)
    _ultimos.append(t)
    del _ultimos[:-3]
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
            det = model.detect_language(cargar_wav(mejor[0])[:16000 * 30])
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
                    palabras.append([palabra.word.strip(), t0 + palabra.start, t0 + palabra.end])
            hecho += info.duration
        progreso(p_fin, "Buscando palabras que faltan…")
        try: palabras = recuperar_huecos(palabras, segs, modelo_actual, language, prompt or "")
        except Exception as exc: _log(f"[recuperar] error: {type(exc).__name__}: {exc}")
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


def armar_bloques(palabras):
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


def escribir_srt(bloques, path):
    with open(path, "w", encoding="utf-8") as f:
        for n, (t, s, e) in enumerate(bloques, 1):
            f.write(f"{n}\n{tc(s)} --> {tc(e)}\n<b>{t}</b>\n\n")


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
                if isinstance(datos, dict): _TRAD_CACHE = {str(k): str(v) for k, v in datos.items()}
        except Exception: _TRAD_CACHE = {}
    return _TRAD_CACHE


def _cache_trad_guardar():
    if _TRAD_CACHE is None: return
    try:
        recorte = dict(list(_TRAD_CACHE.items())[-8000:])
        with open(CACHE_TRAD_FILE, "w", encoding="utf-8") as f:
            json.dump(recorte, f, ensure_ascii=False)
    except Exception: pass


def _google_pedir(texto, origen, destino, timeout=25):
    params = urllib.parse.urlencode({"client": "gtx", "sl": origen or "auto", "tl": destino, "dt": "t"})
    datos = urllib.parse.urlencode({"q": texto}).encode("utf-8")
    req = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + params,
        data=datos,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
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
            if salida: return salida
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code == 429: time.sleep(1.2 * (intento + 1)); continue
        except Exception as exc: ultimo = exc
        if intento < 2: time.sleep(0.7 * (intento + 1))
    raise RuntimeError(f"Google Translate no respondió: {ultimo}")


def _google_lote(lineas, origen, destino, timeout=25):
    q = "\n".join(lineas)
    ultimo = None
    for intento in range(3):
        try:
            data = _google_pedir(q, origen, destino, timeout)
            texto = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
            salida = texto.split("\n")
            while salida and not salida[-1].strip(): salida.pop()
            if len(salida) == len(lineas): return [x.strip() for x in salida]
            return [_google_una(l, origen, destino, timeout) for l in lineas]
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code == 429: time.sleep(1.2 * (intento + 1)); continue
        except Exception as exc: ultimo = exc
        if intento < 2: time.sleep(0.7 * (intento + 1))
    raise RuntimeError(f"Google Translate no respondió: {ultimo}")


def traducir_lineas(lineas, origen, destino, progreso=None):
    limpio = [re.sub(r"\s+", " ", str(x)).strip() for x in lineas]
    cache = _cache_trad_cargar()
    salida = [None] * len(limpio)
    pendientes = []
    for i, texto in enumerate(limpio):
        if not texto: salida[i] = ""; continue
        clave = f"{origen}>{destino}|{texto}"
        if clave in cache: salida[i] = cache[clave]
        else: pendientes.append((i, texto))
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
        if len(resultado) != len(lote): raise RuntimeError("La traducción no devolvió la misma cantidad de líneas.")
        for (indice, texto), trad in zip(lote, resultado):
            trad = (trad or "").strip() or texto
            cache[f"{origen}>{destino}|{texto}"] = trad
            salida[indice] = trad
        hechos += len(lote)
        if progreso: progreso(84 + 4 * hechos / max(1, total), f"Traduciendo al {NOMBRES_IDIOMA.get(destino, destino)}...")
        if i < total: time.sleep(0.25)
    _cache_trad_guardar()
    return [s if s is not None else limpio[k] for k, s in enumerate(salida)]


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


def bloques_traduccion(unidades, traducciones, estilo="palabras"):
    bloques = []
    for unidad, texto in zip(unidades, traducciones):
        texto = (texto or "").strip()
        if not texto or not unidad: continue
        inicio = float(unidad[0][1])
        final = float(unidad[-1][2])
        if final <= inicio: final = inicio + 0.4
        dur = max(0.3, final - inicio)
        partes = (_partir_palabras(texto, dur) if estilo == "palabras" else _partir_frases(texto))
        if not partes: continue
        pesos = [max(1, len(p)) for p in partes]
        total = sum(pesos)
        t = inicio
        for k, parte in enumerate(partes):
            if k == len(partes) - 1: fin = final
            else: fin = min(final, t + dur * pesos[k] / total)
            if fin <= t: fin = t + 0.15
            bloques.append([parte, t, fin])
            t = fin
    for k in range(len(bloques) - 1):
        if bloques[k][2] > bloques[k + 1][1]: bloques[k][2] = bloques[k + 1][1]
    for b in bloques:
        if b[2] <= b[1]: b[2] = b[1] + 0.15
    return bloques


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
        os.replace(ruta, bak)
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


def _tiene_textplus(clip):
    try: cantidad = int(clip.GetFusionCompCount() or 0)
    except Exception: cantidad = 1
    for indice in range(1, min(max(cantidad, 1), 64) + 1):
        try:
            comp = clip.GetFusionCompByIndex(indice)
            if comp and comp.GetToolList(False, "TextPlus"): return True
        except Exception: continue
    return False


def _es_plantilla_media_pool(clip, en_carpeta_plantillas=False):
    tipo = _propiedad_clip(clip, "Type").lower()
    ruta = _propiedad_clip(clip, "File Path").lower()
    nombre = " ".join((str(clip.GetName() or ""), _propiedad_clip(clip, "Clip Name"))).lower()
    if any(p in nombre for p in ("solid color", "color generator", "color bars", "noise")): return False
    tipo_titulo = any(p in tipo for p in ("fusion", "title", "titulo", "título", "titre", "titel", "titolo", "generator", "text", "texto", "texte", "титр", "타이틀", "tiêu đề", "标题"))
    nombre_titulo = any(p in nombre for p in ("text+", "text plus", "fusion", "title", "titulo", "título", "subtitle", "subtítulo", "subtitulo", "plantilla", "template"))
    return (_tiene_textplus(clip) or en_carpeta_plantillas or tipo_titulo or nombre_titulo or ruta.endswith((".setting", ".comp", ".drfx")))


def _clips(carpeta, en_carpeta_plantillas, out, ruta=()):
    for c in carpeta.GetClipList() or []:
        if not _es_plantilla_media_pool(c, en_carpeta_plantillas): continue
        tipo = _propiedad_clip(c, "Type").lower()
        ruta_archivo = _propiedad_clip(c, "File Path").lower()
        if "subtitle" in tipo or ruta_archivo.endswith(".srt"): continue
        etiqueta = " / ".join((*ruta, c.GetName()))
        out[etiqueta] = c
    for f in carpeta.GetSubFolderList() or []:
        nombre = f.GetName().strip()
        marcada = (en_carpeta_plantillas or nombre.lower() in CARPETA_PLANTILLAS or nombre.lower() in ("plantilla", "templates", "template"))
        _clips(f, marcada, out, (*ruta, nombre))


def plantillas():
    out = {}
    _clips(mp.GetRootFolder(), False, out)
    return out


def _pista_activa(tipo, indice, activa):
    try: return bool(timeline.SetTrackEnable(tipo, int(indice), bool(activa)))
    except Exception: return False


def aplicar_plantilla(tpl, bloques, f_ini, progreso, p_ini=92, p_fin=100):
    timeline.AddTrack("video")
    pista = timeline.GetTrackCount("video")
    infos = []
    for t, s, e in bloques:
        if _cancelar_procesos.is_set(): raise RuntimeError("Proceso cancelado.")
        dur = max(1, round((e - s) * FPS))
        infos.append({"mediaPoolItem": tpl, "startFrame": 0, "endFrame": dur, "trackIndex": pista, "recordFrame": f_ini + round(s * FPS)})
    items = mp.AppendToTimeline(infos) or []
    if len(items) != len(bloques):
        if items:
            try: timeline.DeleteClips(items)
            except Exception: pass
        raise RuntimeError("Resolve no pudo insertar todos los títulos de la plantilla seleccionada.")
    comps_tools = []
    for it in items:
        comp = it.GetFusionCompByIndex(1)
        tools = comp.GetToolList(False, "TextPlus") if comp else {}
        tool = next(iter((tools or {}).values()), None)
        if tool is None:
            try: timeline.DeleteClips(items)
            except Exception: pass
            raise RuntimeError("El elemento elegido no contiene un nodo Text+ editable. Elegí un clip de tipo Fusion Composition/Text+ del Media Pool.")
        comps_tools.append(tool)
        
    for n, (tool, b) in enumerate(zip(comps_tools, bloques)):
        tool.StyledText = b[0]
        # Optimización: Actualizar la UI solo cada 15 elementos para evitar que Tkinter colapse
        if n % 15 == 0 or n == len(items) - 1:
            progreso(p_ini + (p_fin - p_ini) * n / max(1, len(items)), "Aplicando plantilla...")
            
    return len(items) == len(bloques)


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
        segs = segmentos_audio(pistas, f_ini, f_fin, tmp)
        if not segs: raise RuntimeError(f"No hay audio en {detalle_pistas} dentro de ese rango.")
        if len(pistas) > 1: progreso(20, f"Mezclando {len(pistas)} pistas en un solo audio...")
        
        nombres = nombres_del_guion(guion)
        glos = glosario_del_guion(texto_guion) if texto_guion else []
        for n in nombres:
            if n.lower() not in {x.lower() for x in glos}: glos.append(n)
        prompt = ", ".join(glos)[:400]
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
                try:
                    traducciones = traducir_lineas([t for _, t in pares], origen, destino, progreso)
                    bloques_trad = bloques_traduccion([u for u, _ in pares], traducciones, estilo_trad)
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
        escribir_transcripcion(pal, off, ruta_txt, TL_NAME)
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
        escribir_srt([[t, s + off, e + off] for t, s, e in bloques], srt)
        if ruta_trad:
            escribir_srt([[t, s + off, e + off] for t, s, e in bloques_trad], ruta_trad)
        if solo_srt:
            msg = (f"SRT GENERADO CORRECTAMENTE\n{len(bloques)} subtítulos originales\n{srt}")
            if ruta_trad: msg += (f"\n\nTRADUCCIÓN ({NOMBRES_IDIOMA.get(destino, destino).upper()})\n{len(bloques_trad)} subtítulos\n{ruta_trad}")
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
    def plantillas(self):
        nombres = sorted(plantillas().keys(), key=lambda s: s.lower())
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