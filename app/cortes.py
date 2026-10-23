"""
Indie Now - Cortes automaticos desde guion (DaVinci Resolve Studio, Windows)

Lee el audio de la timeline, lo transcribe, busca cada frase del guion (y sus tomas repetidas),
elige una toma por frase, saca pausas largas y arma una timeline NUEVA, cortada y en el orden del guion.
La timeline original no se toca.

v5: guarda la transcripcion (reintentar con otros ajustes es instantaneo), penaliza tomas con dudas,
y deja marcadores de color en la timeline nueva para que revises solo lo que importa.
Despues de cortar, corre verificar.py para comprobar el resultado final contra el guion.

Requiere: pip install faster-whisper python-docx numpy   y ffmpeg en PATH
"""
import os, re, sys, unicodedata, subprocess, tempfile, threading, difflib, wave, hashlib, pickle
from collections import defaultdict
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import gpu_runtime

# ---------- Ajustes ----------
MODEL = "large-v3-turbo"
PRE = 0.10          # segundos que se dejan antes de la primera palabra de cada corte
POST = 0.20         # segundos que se dejan despues de la ultima palabra de cada corte
MIN_MATCH = 0.60
DEBUG = False
VERSION = "cortes v5 - marcadores + cache + verificacion"
PAUSA_DUDA = 1.0    # una pausa mas larga que esto DENTRO de una toma = duda/lectura: la toma pierde puntos
PENA_DUDA = 0.75    # puntos que pierde la toma por cada pausa de duda (maximo 3 pausas)
PAUSA_FRASE = 1.2   # si termina una frase del guion y hay una pausa mayor a esto, el tramo se corta ahi:
                    # asi las tomas repetidas de una frase del medio compiten entre si
GAP_DUDOSA = 1.5    # si la 2da mejor toma esta a menos de esto de la elegida, se marca "varias tomas"
MARCA_ABSOLUTA = False   # False: AddMarker recibe frames desde el inicio de la timeline. Si los marcadores
                         # caen corridos (ej. 1 hora), ponelo en True.
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".indienow_cache")

# Colores de marcador (Resolve no tiene naranja)
C_OK, C_DUDA, C_DIFIERE, C_FALTA, C_SIL = "Green", "Yellow", "Pink", "Red", "Blue"

# ---------- Conexion con Resolve ----------
os.environ.setdefault("RESOLVE_SCRIPT_API",
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting")
os.environ.setdefault("RESOLVE_SCRIPT_LIB",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")
sys.path.append(os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules"))
import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
project = resolve.GetProjectManager().GetCurrentProject()
mp = project.GetMediaPool()
timeline = project.GetCurrentTimeline()
if not timeline:
    sys.exit("No hay timeline abierta en Resolve.")

FPS = float(timeline.GetSetting("timelineFrameRate"))
TL_START = timeline.GetStartFrame()
TL_NAME = timeline.GetName()

# ---------- Guion ----------
def leer_guion(path):
    if path.lower().endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    return open(path, encoding="utf-8-sig").read()

def es_cabecera(l):
    return ("—" in l or " – " in l) and bool(re.search(r"[“\"‘]|álbum|album|EP", l))

def norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if unicodedata.category(c) != "Mn"))

def palabras_del_guion(texto):
    """Palabras del guion en orden, sin cabeceras: [(normalizada, como_esta_escrita)]."""
    out = []
    for linea in texto.splitlines():
        l = linea.strip()
        if not l or l.isupper() or es_cabecera(l) or l.startswith("AA "):
            continue
        for w in l.split():
            n = norm(w)
            if n:
                out.append((n, w))
    return out

# ---------- Cache de transcripcion ----------
def clave_cache(pista, f_ini, f_fin, prompt):
    """Identifica el audio (archivos, tamanos, offsets) + modelo + guion. Si nada cambio, se reutiliza la transcripcion."""
    partes = [MODEL, prompt, str(FPS), str(f_ini), str(f_fin)]
    for it in timeline.GetItemListInTrack("audio", pista) or []:
        a, b = max(it.GetStart(), f_ini), min(it.GetEnd(), f_fin)
        if b <= a:
            continue
        mpi = it.GetMediaPoolItem()
        src = mpi.GetClipProperty("File Path") if mpi else ""
        try:
            tam = os.path.getsize(src)
        except OSError:
            tam = 0
        partes += [str(src), str(tam), str(it.GetLeftOffset() + (a - it.GetStart())), str(b - a)]
    return hashlib.sha1("|".join(partes).encode("utf-8")).hexdigest()[:16]

def cache_leer(clave):
    try:
        with open(os.path.join(CACHE_DIR, clave + ".pkl"), "rb") as f:
            return pickle.load(f)
    except Exception:
        return None

def cache_guardar(clave, datos):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, clave + ".pkl"), "wb") as f:
            pickle.dump(datos, f)
    except Exception:
        pass

# ---------- Marcadores ----------
def poner_marca(tl, frame, color, nombre, nota, dur=1, custom="indienow"):
    """Marcador en la timeline. Si ya hay uno en ese frame, prueba los siguientes."""
    base = tl.GetStartFrame() if MARCA_ABSOLUTA else 0
    for k in range(8):
        try:
            if tl.AddMarker(base + int(frame) + k, color, nombre, nota, max(1, int(dur)), custom):
                return True
        except Exception:
            pass
    return False

def mmss(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(t % 60):02d}"

def diferencias(a, b, maxlen=140):
    """Resumen corto de lo que cambia entre lo que dice el guion (a) y lo que se dijo (b)."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [x[0] for x in a], [x[0] for x in b], autojunk=False).get_opcodes():
        if tag == "replace":
            out.append(f"{' '.join(x[1] for x in a[i1:i2])} -> {' '.join(x[1] for x in b[j1:j2])}")
        elif tag == "delete":
            out.append("no dice: " + " ".join(x[1] for x in a[i1:i2]))
        elif tag == "insert":
            out.append("de mas: " + " ".join(x[1] for x in b[j1:j2]))
    txt = "; ".join(out)
    return txt if len(txt) <= maxlen else txt[:maxlen - 3] + "..."

# ---------- Audio de la timeline ----------
def segmentos_audio(pista, f_ini, f_fin, tmp):
    segs = []
    for i, it in enumerate(timeline.GetItemListInTrack("audio", pista) or []):
        a, b = max(it.GetStart(), f_ini), min(it.GetEnd(), f_fin)
        if b <= a:
            continue
        src = it.GetMediaPoolItem().GetClipProperty("File Path")
        off = (it.GetLeftOffset() + (a - it.GetStart())) / FPS
        dur = (b - a) / FPS
        wav = os.path.join(tmp, f"seg{i}.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{off:.3f}", "-t", f"{dur:.3f}",
                        "-i", src, "-ac", "1", "-ar", "16000", wav], check=True)
        segs.append((wav, (a - f_ini) / FPS))
    return segs

def cargar_wav(path):
    import numpy as np
    with wave.open(path, "rb") as w:
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

ALUCINACIONES = ["graciasporverelvideo", "graciasporver", "suscribete", "subtitulosrealizadospor",
                 "subtitulosporlacomunidad", "amaraorg", "graciasporvernos"]

_ultimos = []

def es_alucinacion(seg):
    """Whisper inventa frases ('Gracias por ver el video') o repite texto en bucle cuando hay silencio o ruido."""
    t = norm(seg.text)
    _ultimos.append(t); del _ultimos[:-3]
    if any(b in t for b in ALUCINACIONES) or t == "gracias":
        return True
    if len(_ultimos) == 3 and t and _ultimos[0] == _ultimos[1] == _ultimos[2]:
        return True
    return seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8

def nombres_del_guion(G):
    """Secuencias de palabras con mayuscula que no abren una frase (bandas, discos, nombres)."""
    out, cur = [], []
    for k, (_, d) in enumerate(G):
        ini = k == 0 or G[k - 1][1].rstrip("”\"’)").endswith((".", "?", "!", "…"))
        if d.lstrip("“\"‘¿¡(")[:1].isupper() and not ini:
            cur.append(d.strip("“”\"‘’¿¡().,;:"))
        else:
            if cur: out.append(" ".join(cur))
            cur = []
        if d.endswith((",", ";", ":")) and cur:
            out.append(" ".join(cur)); cur = []
    if cur: out.append(" ".join(cur))
    return list(dict.fromkeys(out))

def transcribir(segs, prompt, progreso):
    from faster_whisper import WhisperModel
    global _ultimos
    gpu_runtime.preparar_cuda()
    gpu = gpu_runtime.nvidia_info()
    device = "cuda" if gpu else "cpu"
    if gpu:
        progreso(30, f"GPU detectada: {gpu}. Cargando Whisper en CUDA…")
        try:
            model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        except Exception as exc:
            device = "cpu"
            progreso(30, f"CUDA no inició ({type(exc).__name__}); continúo en CPU…")
            model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    else:
        progreso(30, "No detecté una GPU NVIDIA. Cargando Whisper en CPU…")
        model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    total = sum(float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", w]).strip())
        for w, _ in segs) or 1
    import numpy as np
    def ejecutar(modelo, nombre_dispositivo):
        global _ultimos
        _ultimos = []
        hecho, palabras, envs = 0.0, [], []
        for wav, t0 in segs:
            audio = cargar_wav(wav)
            n = len(audio) // 160                      # un valor cada 10 ms
            if n:
                fr = audio[:n * 160].reshape(n, 160)
                envs.append((t0, 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)))
            it, info = modelo.transcribe(audio, language="es", word_timestamps=True,
                                         initial_prompt=prompt, vad_filter=False, beam_size=5,
                                         condition_on_previous_text=False)
            for s in it:
                progreso(35 + 45 * (hecho + s.end) / total,
                         f"Transcribiendo en {nombre_dispositivo}…")
                if es_alucinacion(s):
                    continue
                for w in s.words or []:
                    n = norm(w.word)
                    if n:
                        palabras.append((n, t0 + w.start, t0 + w.end))
            hecho += info.duration
        return palabras, envs

    try:
        return ejecutar(model, "GPU NVIDIA (CUDA)" if device == "cuda" else "CPU")
    except Exception as exc:
        if device != "cuda":
            raise
        progreso(30, f"Falló CUDA ({type(exc).__name__}); repito todo en CPU…")
        cpu_model = WhisperModel(MODEL, device="cpu", compute_type="int8")
        return ejecutar(cpu_model, "CPU (respaldo tras fallo de CUDA)")

def refinar(T, envs):
    """Whisper estira palabras sobre los silencios. Acá se ajusta inicio/fin de cada palabra al sonido real."""
    import numpy as np, math
    if not envs:
        return T
    umbral = np.percentile(np.concatenate([e for _, e in envs]), 10) + 12   # 12 dB sobre el piso de ruido
    out = []
    for n, s, e in T:
        env = next(((t0, ev) for t0, ev in envs if t0 - 0.001 <= s < t0 + len(ev) * 0.01), None)
        if env is None:
            out.append((n, s, e)); continue
        t0, ev = env
        a, b = max(0, int((s - t0) / 0.01)), min(len(ev), int(math.ceil((e - t0) / 0.01)))
        if b - a < 2:
            out.append((n, s, e)); continue
        v = ev[a:b] > umbral
        runs, k = [], 0
        while k < len(v):
            if v[k]:
                m = k
                while m < len(v) and v[m]: m += 1
                runs.append([k, m]); k = m
            else:
                k += 1
        unidos = []
        for r in runs:                             # une tramos separados por menos de 150 ms
            if unidos and r[0] - unidos[-1][1] <= 15: unidos[-1][1] = r[1]
            else: unidos.append(r)
        unidos = [r for r in unidos if r[1] - r[0] >= 3]
        if not unidos:
            out.append((n, s, e)); continue
        r = max(unidos, key=lambda r: r[1] - r[0])  # el tramo con mas sonido
        out.append((n, t0 + (a + r[0]) * 0.01, t0 + (a + r[1]) * 0.01))
    return [(n, s, min(e, s + 1.5)) for n, s, e in out]       # ninguna palabra dura mas de 1.5 s

# ---------- Buscar en la grabacion lo que dice el guion ----------
# Se recorre el guion de principio a fin. En cada punto se busca, entre todas las tomas, el tramo
# que sigue el guion por mas palabras seguidas (tolerando alguna palabra cambiada o salteada).
_FIN_FRASE = []   # por palabra del guion: True si termina una frase (se llena en planear)
OPCIONES = [(1, 1), (0, 1), (1, 0), (2, 1), (1, 2), (2, 2), (2, 0), (3, 0)]   # (palabras de la grabacion, palabras del guion)

def extender(T, Tn, G, nl, i, g):
    """Desde la palabra i de la grabacion y la g del guion, avanza mientras coincidan.
    Devuelve (palabras_del_guion_cubiertas, fin_en_grabacion, coincidencias, errores, palabras_de_mas)."""
    ti, gi, ok, err, extra = i, g, 0, 0, []
    mejor = (0, i, 0, 0, [])
    nT, nG = len(Tn), len(G)
    while ti < nT and gi < nG:
        if gi > g and ti > i and gi - 1 < len(_FIN_FRASE) and _FIN_FRASE[gi - 1] and T[ti][1] - T[ti - 1][2] > PAUSA_FRASE:
            break                                   # fin de frase + pausa larga: ahi termina este tramo
        if Tn[ti] == G[gi]:
            ok += 1; ti += 1; gi += 1
            mejor = (gi - g, ti, ok, err, list(extra))
            continue
        for dt, dg in OPCIONES:
            if ti + dt < nT and gi + dg < nG and Tn[ti + dt] == G[gi + dg]:
                if dg == 0:
                    extra += range(ti, ti + dt)      # lo dicho de mas no se incluye en el corte
                ti += dt; gi += dg; err += max(dt, dg)
                break
        else:
            break
        if err > 2 and err > ok * 0.6:
            break
    cg, fin, ok, err, extra = mejor
    # una palabra mal oida pegada al final (ej. 'Phil' por 'FEEL') se conserva
    if cg and fin < nT and g + cg < nG and fin > i and Tn[fin] != G[g + cg] \
            and T[fin][1] - T[fin - 1][2] <= 0.3 \
            and nl[g + cg] and difflib.SequenceMatcher(None, Tn[fin], G[g + cg]).ratio() >= 0.2:
        cg, fin, err = cg + 1, fin + 1, err + 1
    return cg, fin, ok, err, extra

def niveles(T, envs):
    """Volumen (dB) de cada palabra, medido en el audio real. Sirve para separar 'leer el guion' de decirlo en serio."""
    import numpy as np
    if not envs:
        return None
    out = []
    for n, s, e in T:
        env = next(((t0, ev) for t0, ev in envs if t0 - 0.001 <= s < t0 + len(ev) * 0.01), None)
        if env is None:
            out.append(float("nan")); continue
        t0, ev = env
        a = max(0, int((s - t0) / 0.01))
        b = min(len(ev), max(a + 1, int((e - t0) / 0.01)))
        out.append(float(np.percentile(ev[a:b], 90)))
    arr = np.array(out)
    med = float(np.nanmedian(arr)) if np.isfinite(arr).any() else 0.0
    return [med if not np.isfinite(x) else x for x in out]

def sacar_extra(T, Tn, extra, recientes):
    """De las palabras dichas de mas se sacan: (1) las que repiten texto del guion cercano (se volvio a
    empezar una frase) y (2) las separadas por una pausa (un desliz como 'ay carajo').
    Si se dijeron fluidas y son nuevas (una improvisacion), se conservan."""
    extra, out, k = sorted(extra), set(), 0
    while k < len(extra):
        m = k
        while m + 1 < len(extra) and extra[m + 1] == extra[m] + 1:
            m += 1
        a, b = extra[k], extra[m]
        largas = [x for x in range(a, b + 1) if len(Tn[x]) >= 3]
        repite = bool(largas) and sum(Tn[x] in recientes for x in largas) >= 0.5 * len(largas)
        antes = a > 0 and T[a][1] - T[a - 1][2] > 0.3
        despues = b + 1 < len(T) and T[b + 1][1] - T[b][2] > 0.3
        if repite or antes or despues:
            out.update(range(a, b + 1))
        k = m + 1
    return out

def candidatos(T, Tn, pos, G, nl, g, lo, usados, ok_min=2):
    out = []
    for k in range(4):                    # el guion puede empezar con palabras que no se dijeron o se oyeron mal
        if g + k >= len(G) or (k and len(G[g + k]) < 3):
            continue
        for i in pos.get(G[g + k], []):
            if i < lo - 2:
                continue
            cg, fin, ok, err, extra = extender(T, Tn, G, nl, i, g + k)
            if ok < min(ok_min, len(G) - g) or fin <= i:
                continue
            ini, m = i, 0                 # palabras pegadas antes (ej. 'aqui en' por 'Here In') se conservan
            while m < k and ini - 1 >= 0 and T[ini][1] - T[ini - 1][2] <= 0.3 and \
                    not any(a <= ini - 1 < b for a, b in usados):
                ini -= 1; m += 1
            if any(not (fin <= a or ini >= b) for a, b in usados):
                continue
            out.append({"i": ini, "fin": fin, "cubre": k + cg, "ok": ok, "err": err, "extra_todas": set(extra),
                        "extra": sacar_extra(T, Tn, extra, set(G[max(0, g - 15):g + 15])),
                        "score": ok - 0.5 * err - 6 * (k - m) - 0.5 * m})
    return out

def cortes_de_toma(T, i, j, pausa, excluir=()):
    """Segundos (inicio, fin) de una toma. Las pausas mayores a 'pausa' y las palabras de mas se sacan."""
    piezas, a = [], None
    for k in range(i, j):
        if k in excluir:
            if a is not None:
                piezas.append((a, k)); a = None
            continue
        if a is None:
            a = k
        elif T[k][1] - T[k - 1][2] > pausa:
            piezas.append((a, k)); a = k
    if a is not None:
        piezas.append((a, j))
    out = []
    for a, b in piezas:
        pre, post = PRE, POST
        if a > 0:
            pre = min(pre, max(0.0, (T[a][1] - T[a - 1][2]) / 2))
        if b < len(T):
            post = min(post, max(0.0, (T[b][1] - T[b - 1][2]) / 2))
        out.append((max(0.0, T[a][1] - pre), T[b - 1][2] + post))
    return out

def planear(T, G, modo, pausa, niv=None, bajada=0.0):
    """Devuelve (cortes_en_segundos_en_orden_del_guion, reporte)."""
    Tn = [t[0] for t in T]
    palabras = [w for w, _ in G]
    vocab = sorted(set(palabras))
    vs, cache = set(vocab), {}
    for k, w in enumerate(Tn):                   # palabras casi iguales a las del guion se igualan (nombres mal oidos)
        if w not in vs:
            if w not in cache:
                m = difflib.get_close_matches(w, vocab, n=1, cutoff=0.8)
                cache[w] = m[0] if m else w
            Tn[k] = cache[w]
    pos = defaultdict(list)
    for i, w in enumerate(Tn):
        pos[w].append(i)
    _FIN_FRASE[:] = [d.rstrip("”\"’)»").endswith((".", "?", "!", "…")) for _, d in G]
    nl = []                                      # palabras del guion que parecen nombres (mayuscula fuera de inicio de frase)
    for k, (_, d) in enumerate(G):
        ini = k == 0 or G[k - 1][1].rstrip("”\"’)").endswith((".", "?", "!", "…"))
        nl.append(d.lstrip("“\"‘¿¡(")[:1].isupper() and not ini)
    ref, lecturas, bajas_usadas = None, [], 0
    if niv and bajada > 0:
        import numpy as np
        ref = float(np.percentile(niv, 75))      # tu voz "fuerte" de verdad
    traza, eventos, falta_actual = [], [], []
    cortes, faltan, usados = [], [], []
    g, lo, prev_fin, varias, tramos = 0, 0, None, 0, 0

    def cerrar_faltas(t_anclaje):
        """Las palabras del guion que no se encontraron se marcan (rojo) donde tendrian que ir."""
        if falta_actual:
            txt = " ".join(G[k][1] for k in falta_actual)
            eventos.append({"t": t_anclaje, "color": C_FALTA, "nombre": "FALTA EN EL AUDIO",
                            "nota": f"El guion dice y no lo encontre: {txt[:200]}"})
            falta_actual.clear()

    while g < len(palabras):
        cs = candidatos(T, Tn, pos, palabras, nl, g, lo, usados) or candidatos(T, Tn, pos, palabras, nl, g, 0, usados, ok_min=4)
        if not cs:
            faltan.append(g); falta_actual.append(g); g += 1; continue
        for c in cs:                             # pausas largas adentro de la toma = duda o lectura, pierde puntos
            c["pausas"] = sum(1 for k in range(c["i"] + 1, c["fin"])
                              if k not in c["extra"] and T[k][1] - T[k - 1][2] > PAUSA_DUDA)
            c["score"] -= PENA_DUDA * min(c["pausas"], 3)
        for c in cs:                             # tomas mucho mas bajas que tu voz normal = lectura, no toma real
            c["baja"] = False
            if ref is not None:
                seg = sorted(niv[c["i"]:c["fin"]])
                c["nivel"] = seg[len(seg) // 2]
                c["baja"] = c["nivel"] < ref - bajada
                if c["baja"]:
                    c["score"] -= 20
        for c in cs:                             # si sigue justo donde termino el tramo anterior, suena mas natural
            if prev_fin is not None and 0 <= c["i"] - prev_fin <= 1:
                c["score"] += 0.75
        top = max(c["score"] for c in cs)
        if modo == "ultima":
            c = max([c for c in cs if c["score"] >= top - 1.5], key=lambda c: c["i"])
        else:
            c = max([c for c in cs if c["score"] >= top - 0.01], key=lambda c: c["i"])
        if c["baja"]:
            bajas_usadas += 1
        else:
            for x in cs:
                if x["baja"] and x["ok"] >= max(3, 0.6 * c["ok"]):
                    lecturas.append((T[x["i"]][1], x["nivel"]))
        buenos = sorted([x for x in cs if x["ok"] >= max(2, 0.6 * c["ok"])], key=lambda x: x["i"])
        tomas, fin_t = 0, -1
        for x in buenos:                         # cuantas tomas distintas habia de este tramo
            if x["i"] >= fin_t:
                tomas += 1; fin_t = x["fin"]
        varias += tomas > 1
        # ---- clasificacion del tramo para los marcadores
        incl = [k for k in range(c["i"], c["fin"]) if k not in c["extra"]]
        ini_real = incl[0] if incl else c["i"]
        t_ini = T[ini_real][1]
        cerrar_faltas(t_ini)
        improv = len(c["extra_todas"] - c["extra"])          # palabras de mas que se dejaron (improvisacion)
        dif = (c["cubre"] - c["ok"]) + improv                # palabras del guion no dichas igual + improvisadas
        otras = [x for x in buenos if x["fin"] <= c["i"] or x["i"] >= c["fin"]]
        gap = (c["score"] - max(x["score"] for x in otras)) if otras else 99
        txt_guion = f"Guion: {' '.join(d for _, d in G[g:g + c['cubre']])[:110]}"
        if dif > 0 or c["baja"]:
            razon = diferencias(G[g:g + c["cubre"]], [(Tn[k], T[k][0]) for k in incl])
            if c["baja"]:
                razon = (razon + " | " if razon else "") + "volumen bajo (posible lectura)"
            eventos.append({"t": t_ini, "color": C_DIFIERE, "nombre": "DIFIERE DEL GUION",
                            "nota": f"{razon}\n{txt_guion}"})
        elif otras and gap < GAP_DUDOSA:
            distintas, fin_d = [], -1                        # una por toma (sin variantes que empiezan unas palabras despues)
            for x in sorted(otras, key=lambda x: x["i"]):
                if x["i"] >= fin_d:
                    distintas.append(x); fin_d = x["fin"]
            donde = ", ".join(mmss(T[x["i"]][1]) for x in distintas[:3])
            eventos.append({"t": t_ini, "color": C_DUDA, "nombre": "VARIAS TOMAS PARECIDAS",
                            "nota": f"Use la de {mmss(t_ini)}. Otras en la original: {donde}\n{txt_guion}"})
        else:
            eventos.append({"t": t_ini, "color": C_OK, "nombre": "OK", "nota": txt_guion})
        lo = buenos[0]["i"] if buenos else c["i"]
        usados.append((c["i"], c["fin"]))
        cortes += cortes_de_toma(T, c["i"], c["fin"], pausa, c["extra"])
        traza.append(f"{c['i'] and T[c['i']][1] or T[0][1]:7.1f}s  g={g:3} guion: {' '.join(palabras[g:g + c['cubre']])[:70]}\n"
                     f"            toma: {' '.join(Tn[c['i']:c['fin']])[:70]}\n"
                     f"            ok={c['ok']} score={c['score']:.1f} candidatas={[(round(T[x['i']][1]), x['ok']) for x in cs][:8]}"
                     + (f" extra_sacadas={len(c['extra'])}" if c['extra'] else ""))
        if DEBUG:
            print(f"g={g:3} {' '.join(palabras[g:g + c['cubre']])[:50]:50} <- T[{c['i']}:{c['fin']}] "
                  f"{T[c['i']][1]:.1f}s ok={c['ok']} sc={c['score']:.1f} cands={len(cs)}")
        prev_fin = c["fin"]
        tramos += 1
        g += max(1, c["cubre"])
    cerrar_faltas(None)                          # faltas al final del guion
    unidos = []                                  # une cortes que casi se tocan en el original
    for a, b in cortes:
        if unidos and -0.02 <= a - unidos[-1][1] <= 0.15:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], b))
        else:
            unidos.append((a, b))
    rep = [f"{len(palabras) - len(faltan)} de {len(palabras)} palabras del guion encontradas, {len(unidos)} cortes.",
           f"{varias} de {tramos} tramos tenian mas de una toma."]
    if ref is not None:
        rep.append(f"Tu voz fuerte ≈ {ref:.0f} dB. Se descartan tomas por debajo de {ref - bajada:.0f} dB (lecturas).")
        if lecturas:
            lugares = []                          # una marca por lectura (no una por cada frase de esa lectura)
            for t, _ in sorted(lecturas):
                if not lugares or t - lugares[-1] > 8:
                    lugares.append(t)
            marcas = ", ".join(f"{int(t // 60)}:{int(t % 60):02d}" for t in lugares[:15])
            rep.append(f"Zonas descartadas por volumen bajo (lecturas): {marcas}.")
        if bajas_usadas:
            rep.append(f"OJO: {bajas_usadas} tramos solo existían con volumen bajo y se usaron igual.")
    if faltan:
        grupos, cur = [], [faltan[0]]
        for k in faltan[1:]:
            if k == cur[-1] + 1: cur.append(k)
            else: grupos.append(cur); cur = [k]
        grupos.append(cur)
        rep.append("No encontré en el audio (no se incluye):")
        rep += ["  - " + " ".join(G[k][1] for k in gr) for gr in grupos]
    return unidos, rep, traza, eventos

# ---------- Armar la timeline nueva ----------
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
                if c.GetName() == TL_NAME and "timeline" in (c.GetClipProperty("Type") or "").lower():
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

def _fps_clip(mpi):
    try:
        v = float(mpi.GetClipProperty("FPS"))
        return v if v > 0 else FPS
    except Exception:
        return FPS

def infos_pista(tipo, pista, cortes_f, rec0, destino=1):
    """clipInfo para AppendToTimeline: un trozo por cada corte y por cada clip de la pista que lo cubre."""
    items = timeline.GetItemListInTrack(tipo, pista) or []
    out, acum = [], 0
    for a, b in cortes_f:
        for it in items:
            aa, bb = max(a, it.GetStart()), min(b, it.GetEnd())
            if bb <= aa:
                continue
            mpi = it.GetMediaPoolItem()
            if not mpi:
                continue
            f = _fps_clip(mpi)
            sf = round((it.GetLeftOffset() + (aa - it.GetStart())) / FPS * f)
            n = max(1, round((bb - aa) / FPS * f))
            out.append({"mediaPoolItem": mpi, "startFrame": sf, "endFrame": sf + n - 1,
                        "mediaType": 1 if tipo == "video" else 2, "trackIndex": destino,
                        "recordFrame": rec0 + acum + (aa - a)})
        acum += b - a
    return out

def pos_en_nueva(cortes_f, frame_orig):
    """Frame (desde el inicio) en la timeline nueva de un frame de la original. None si quedo afuera de los cortes."""
    acum = 0
    for a, b in cortes_f:
        if a <= frame_orig < b:
            return acum + (frame_orig - a)
        acum += b - a
    return None

def construir(cortes_f, pista_audio, pista_video, otras):
    nombre, n = f"{TL_NAME} - cortado", 1
    previa = mp.GetCurrentFolder()
    try:
        destino = carpeta_de_timeline()
        if destino:
            mp.SetCurrentFolder(destino)
        nueva = None
        while not nueva and n < 20:
            nueva = mp.CreateEmptyTimeline(nombre if n == 1 else f"{nombre} {n}")
            n += 1
    finally:
        if previa:
            mp.SetCurrentFolder(previa)
    if not nueva:
        raise RuntimeError("No pude crear la timeline nueva.")
    project.SetCurrentTimeline(nueva)
    rec0 = nueva.GetStartFrame()
    v = infos_pista("video", pista_video, cortes_f, rec0, 1)         # video del iPhone
    a = infos_pista("audio", pista_audio, cortes_f, rec0, 1)         # audio del microfono
    extras, dest = [], 2
    if otras:                                                       # audio del iPhone u otros: quedan desactivados
        for t in range(1, timeline.GetTrackCount("audio") + 1):
            if t == pista_audio:
                continue
            info = infos_pista("audio", t, cortes_f, rec0, dest)
            if info:
                while nueva.GetTrackCount("audio") < dest:
                    nueva.AddTrack("audio")
                extras.append((dest, info)); dest += 1
    ok_v = (not v) or bool(mp.AppendToTimeline(v))
    ok_a = (not a) or bool(mp.AppendToTimeline(a))
    for d, info in extras:
        ok_a = bool(mp.AppendToTimeline(info)) and ok_a
        try: nueva.SetTrackEnable("audio", d, False)
        except Exception: pass
    return nueva, ok_v, ok_a, len(v), len(a), len(extras)

# ---------- Proceso principal ----------
def correr(cfg, progreso, reporte):
    progreso(2, "Leyendo guion...")
    G = palabras_del_guion(leer_guion(cfg["guion"]))
    if not G:
        raise RuntimeError("No pude sacar palabras del guion.")
    mi = timeline.GetMarkInOut() or {}
    ai = mi.get("audio") or mi.get("video") or {}
    if cfg["rango"] == "inout" and ai:
        f_ini, f_fin = ai["in"], ai["out"]
    else:
        f_ini, f_fin = TL_START, timeline.GetEndFrame()
    prompt = " ".join(d for _, d in G)[:600]
    clave = clave_cache(cfg["pista"], f_ini, f_fin, prompt)
    datos = cache_leer(clave) if cfg.get("cache", True) else None
    if datos:
        T, envs = datos
        progreso(80, "Usando la transcripción guardada...")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            progreso(5, "Extrayendo audio...")
            segs = segmentos_audio(cfg["pista"], f_ini, f_fin, tmp)
            if not segs:
                raise RuntimeError("No hay audio en esa pista/rango.")
            progreso(30, "Cargando modelo...")
            T, envs = transcribir(segs, prompt, progreso)
        if T:
            cache_guardar(clave, (T, envs))
    T = refinar(T, envs)
    niv = niveles(T, envs)
    progreso(82, "Buscando las frases del guion...")
    cortes, rep, traza, eventos = planear(T, G, cfg["modo"], cfg["pausa"], niv, cfg["bajada"])
    if not cortes:
        raise RuntimeError("No encontré ninguna frase del guion en el audio.")
    try:                                         # archivo de diagnostico, al lado del guion
        ruta = os.path.join(os.path.dirname(cfg["guion"]), TL_NAME + "_cortes_debug.txt")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(VERSION + "\n\n" + "\n".join(rep) + "\n\n=== TRAMOS ELEGIDOS ===\n" + "\n".join(traza))
            f.write("\n\n=== MARCADORES (posicion en la original) ===\n")
            for ev in eventos:
                if ev["color"] != C_OK:
                    f.write(f"{mmss(ev['t']) if ev['t'] is not None else 'final':>6}  {ev['color']:7} {ev['nombre']}: {ev['nota'].splitlines()[0]}\n")
            f.write("\n\n=== TRANSCRIPCION (seg, volumen dB, palabra) ===\n")
            for k, (n, s0, e0) in enumerate(T):
                f.write(f"{s0:7.2f} {e0:7.2f} {niv[k] if niv else 0:6.1f}  {n}\n")
        rep.append("Diagnóstico guardado: " + ruta)
    except Exception as ex:
        rep.append("No pude guardar el diagnóstico: " + str(ex))
    cortes_f = []
    for a, b in cortes:
        fa, fb = f_ini + round(a * FPS), f_ini + round(b * FPS)
        fa, fb = max(f_ini, fa), min(f_fin, fb)
        if fb > fa:
            cortes_f.append((fa, fb))
    progreso(90, "Armando la timeline nueva...")
    nueva, ok_v, ok_a, nv, na, ne = construir(cortes_f, cfg["pista"], cfg["video"], cfg["otras"])
    nombre = nueva.GetName()
    rep.insert(0, VERSION)
    progreso(96, "Poniendo marcadores...")
    n_marc, n_fallo, cuenta = 0, 0, defaultdict(int)
    total_f = sum(b - a for a, b in cortes_f)
    for ev in eventos:
        if ev["color"] == C_OK and not cfg.get("verdes"):
            continue
        if ev["t"] is None:
            pos = max(0, total_f - 1)
        else:
            pos = pos_en_nueva(cortes_f, f_ini + round(ev["t"] * FPS))
        if pos is None or not poner_marca(nueva, pos, ev["color"], ev["nombre"], ev["nota"]):
            n_fallo += 1; continue
        n_marc += 1; cuenta[ev["color"]] += 1
    rep.append(f"Marcadores: rojo {cuenta[C_FALTA]} (falta en el audio), rosa {cuenta[C_DIFIERE]} (difiere del guion), "
               f"amarillo {cuenta[C_DUDA]} (varias tomas)" + (f", verde {cuenta[C_OK]} (ok)" if cfg.get("verdes") else "") +
               (f". {n_fallo} no se pudieron poner." if n_fallo else "."))
    rep.append(f"Timeline creada: {nombre} ({nv} trozos de video, {na} de audio" +
               (f", {ne} pistas extra desactivadas)." if ne else ")."))
    if nv == 0:
        rep.append("OJO: no encontré video en esa pista de video.")
    if not (ok_v and ok_a):
        rep.append("OJO: Resolve rechazó parte de los clips. Revisá la timeline.")
    reporte(rep)
    progreso(100, "Listo")

# ---------- Ventana ----------
def main():
    n_audio = timeline.GetTrackCount("audio")
    n_video = timeline.GetTrackCount("video")
    root = tk.Tk(); root.title("Indie Now - " + VERSION); root.geometry("460x760")
    v_guion = tk.StringVar(); v_pista = tk.StringVar(value="A1"); v_rango = tk.StringVar(value="inout")
    v_video = tk.StringVar(value="V1"); v_otras = tk.BooleanVar(value=False); v_baja = tk.DoubleVar(value=8)
    v_modo = tk.StringVar(value="mejor"); v_pausa = tk.StringVar(value="0.5")
    v_cache = tk.BooleanVar(value=True); v_verdes = tk.BooleanVar(value=False)

    def elegir():
        p = filedialog.askopenfilename(filetypes=[("Guion", "*.txt *.docx")])
        if p: v_guion.set(p)

    ttk.Button(root, text="Elegir guion...", command=elegir).pack(fill="x", padx=12, pady=(12, 0))
    ttk.Label(root, textvariable=v_guion, wraplength=430).pack(anchor="w", padx=12)
    ttk.Label(root, text="Video (iPhone)").pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Combobox(root, textvariable=v_video, state="readonly",
                 values=[f"V{i}" for i in range(1, n_video + 1)]).pack(fill="x", padx=12)
    ttk.Label(root, text="Audio (micrófono): de acá se transcribe y se copia el sonido").pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Combobox(root, textvariable=v_pista, state="readonly",
                 values=[f"A{i}" for i in range(1, n_audio + 1)]).pack(fill="x", padx=12)
    ttk.Checkbutton(root, text="Copiar también las otras pistas de audio (quedan desactivadas)",
                    variable=v_otras).pack(anchor="w", padx=12, pady=(4, 0))
    ttk.Radiobutton(root, text="Puntos In / Out", variable=v_rango, value="inout").pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Radiobutton(root, text="Línea completa", variable=v_rango, value="full").pack(anchor="w", padx=12)
    ttk.Label(root, text="Si una frase tiene varias tomas, usar").pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Radiobutton(root, text="La que más se parece al guion (recomendado)", variable=v_modo, value="mejor").pack(anchor="w", padx=12)
    ttk.Radiobutton(root, text="La última (la mejor de las últimas)", variable=v_modo, value="ultima").pack(anchor="w", padx=12)
    fila = ttk.Frame(root); fila.pack(fill="x", padx=12, pady=(8, 0))
    ttk.Label(fila, text="Sacar pausas de más de").pack(side="left")
    ttk.Entry(fila, textvariable=v_pausa, width=5).pack(side="left", padx=4)
    ttk.Label(fila, text="segundos").pack(side="left")
    ttk.Label(root, text="Ignorar tomas más bajas que tu voz normal (dB). 0 = no usar").pack(anchor="w", padx=12, pady=(8, 0))
    tk.Scale(root, from_=0, to=20, orient="horizontal", variable=v_baja).pack(fill="x", padx=12)
    ttk.Checkbutton(root, text="Usar transcripción guardada si el audio no cambió (más rápido)",
                    variable=v_cache).pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Checkbutton(root, text="Marcar también las partes OK (verde)", variable=v_verdes).pack(anchor="w", padx=12)
    barra = ttk.Progressbar(root, maximum=100); barra.pack(fill="x", padx=12, pady=(12, 0))
    estado = ttk.Label(root, text="", wraplength=430); estado.pack(anchor="w", padx=12)
    caja = tk.Text(root, height=9, wrap="word", state="disabled"); caja.pack(fill="both", expand=True, padx=12, pady=(4, 0))

    def progreso(p, msg):
        root.after(0, lambda: (barra.configure(value=p), estado.configure(text=msg)))

    def reporte(lineas):
        def poner():
            caja.configure(state="normal"); caja.delete("1.0", "end")
            caja.insert("end", "\n".join(lineas)); caja.configure(state="disabled")
        root.after(0, poner)

    def go():
        if not v_guion.get():
            return messagebox.showwarning("Falta guion", "Elegí el guion primero.")
        try:
            pausa = float(v_pausa.get().replace(",", "."))
        except ValueError:
            return messagebox.showwarning("Pausa", "Poné un número, por ejemplo 0.5")
        cfg = {"guion": v_guion.get(), "pista": int(v_pista.get()[1:]), "video": int(v_video.get()[1:]),
               "otras": v_otras.get(), "bajada": float(v_baja.get()), "rango": v_rango.get(), "modo": v_modo.get(), "pausa": pausa,
               "cache": v_cache.get(), "verdes": v_verdes.get()}
        def t():
            try: correr(cfg, progreso, reporte)
            except Exception as ex:
                import traceback; traceback.print_exc()
                progreso(0, "Error: " + str(ex))
        threading.Thread(target=t, daemon=True).start()

    ttk.Button(root, text="Cortar", command=go).pack(fill="x", padx=12, pady=12)
    root.mainloop()

if __name__ == "__main__":
    main()
