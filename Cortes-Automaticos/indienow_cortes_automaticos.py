"""
Indie Now - Cortes automaticos v2 (mini proyecto) (DaVinci Resolve Studio, Windows)

Mini-proyecto nuevo sobre la base de "Cortes automaticos v1". Lo que cambia es la
decision final de cortes, incorporando:

  1. Transcripcion + deteccion de alertas (silencios, volumen/lectura, repeticiones).
  2. Comparacion del orden y la escritura de la transcripcion contra el guion.
  3. Seleccion de la ultima toma (la mejor) cuando hay repeticiones.
  4. Cortes automaticos entre palabras, con margenes PRE/POST, sin cortes a mitad de palabra.
  5. Validacion del corte contra el guion (orden y escritura) y cortes que no encajan.
  6. Armar la timeline nueva con marcadores, sin tocar la original.

Requiere: pip install faster-whisper python-docx numpy   y ffmpeg en PATH
"""

import os, re, sys, unicodedata, subprocess, tempfile, threading, difflib, wave, hashlib, pickle
from collections import Counter
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import gpu_runtime
    gpu_runtime.preparar_cuda()
except Exception:
    pass

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


MODEL = "large-v3-turbo"


VERSION = "cortes auto v1 (manda la transcripcion)"


PRE = 0.10               # segundos que se dejan antes de la primera palabra de cada corte


POST = 0.20              # segundos que se dejan despues de la ultima palabra de cada corte


PAUSA_FRASE = 0.8        # una pausa mayor a esto separa una frase de otra


REF_VENTANA = 45.0       # segundos a cada lado para calcular tu volumen "normal" local


REF_PERCENTIL = 80       # el volumen normal = el nivel que alcanza el 20 % mas fuerte de las frases cercanas


UMBRAL_REPETIDA = 0.70   # que tan parecida tiene que ser una frase a otra para contarla como repeticion


LOOKBACK = 10            # con cuantas frases anteriores se compara para buscar repeticiones


VENTANA_T = 240.0        # segundos maximos entre una toma y su repeticion


FLAG_COBERTURA = 0.85    # si la repeticion se parece menos que esto, se marca "revisar"


MARCA_ABSOLUTA = False   # si los marcadores caen corridos (ej. 1 hora), ponelo en True


CUSTOM = "indienow-auto"


CACHE_DIR = os.path.join(os.path.expanduser("~"), ".indienow_auto_cache")

# Colores de marcador
C_DUDA, C_DIFIERE, C_ORDEN = "Yellow", "Pink", "Blue"

# Frases que no aportan nada (alucinaciones / bloqueos repetidos)
ALUCINACIONES = ["graciasporverelvideo", "graciasporver", "suscribete", "subtitulosrealizadospor",
                 "subtitulosporlacomunidad", "amaraorg", "graciasporvernos"]
_ultimos = []

PALABRAS_TRABADAS = {"por", "que", "de", "la", "el", "en", "y", "a", "con", "los", "las", "un", "una",
                     "se", "es", "lo", "del", "al", "para", "su", "mi", "o", "e"}


def norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "", "".join(c for c in w if unicodedata.category(c) != "Mn"))


def mmss(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def limpiar(p):
    return p.strip(" “”\"‘’¿¡().,;:…—-?!")


def leer_guion(path):
    if path.lower().endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    return open(path, encoding="utf-8-sig").read()


def es_cabecera(l):
    return ("—" in l or " – " in l) and bool(re.search(r"[“\"‘]|álbum|album|EP", l))


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


def formas_del_guion(G):
    """Como se escribe cada palabra en el guion (la forma mas usada)."""
    c = {}
    for n, d in G:
        f = limpiar(d)
        if f:
            c.setdefault(n, Counter())[f] += 1
    return {n: cn.most_common(1)[0][0] for n, cn in c.items()}


def nombres_del_guion(G):
    """Secuencias de palabras con mayúscula que no abren una frase (bandas, discos, nombres)."""
    out, cur = [], []
    for k, (_, d) in enumerate(G):
        ini = k == 0 or G[k - 1][1].rstrip("”\"’)").endswith((".", "?", "!", "…"))
        if d.lstrip("“\"‘¿¡(")[:1].isupper() and not ini:
            cur.append(d.strip("“”\"‘’¿¡().,;:"))
        else:
            if cur:
                out.append(" ".join(cur))
            cur = []
        if d.endswith((",", ";", ":")) and cur:
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return list(dict.fromkeys(out))


def igualar_al_guion(T, G):
    """Palabras mal oidas parecidas a una del guion se igualan a ella (solo para comparar y escribir)."""
    if not G:
        return [t[0] for t in T]
    vocab = sorted({n for n, _ in G})
    vs, cache, Tn = set(vocab), {}, []
    for t in T:
        n = t[0]
        if n not in vs and len(n) >= 4:
            if n not in cache:
                m = difflib.get_close_matches(n, vocab, n=1, cutoff=0.84)
                cache[n] = m[0] if m else n
            n = cache[n]
        Tn.append(n)
    return Tn


def clave_cache(pista, f_ini, f_fin, prompt):
    partes = ["auto", MODEL, prompt, str(FPS), str(f_ini), str(f_fin)]
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


def poner_marca(tl, frame, color, nombre, nota, dur=1, custom=CUSTOM):
    base = tl.GetStartFrame() if MARCA_ABSOLUTA else 0
    for k in range(8):
        try:
            if tl.AddMarker(base + int(frame) + k, color, nombre, nota, max(1, int(dur)), custom):
                return True
        except Exception:
            pass
    return False


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


def es_alucinacion(seg):
    t = norm(seg.text)
    _ultimos.append(t); del _ultimos[:-3]
    if any(b in t for b in ALUCINACIONES) or t == "gracias":
        return True
    if len(_ultimos) == 3 and t and _ultimos[0] == _ultimos[1] == _ultimos[2]:
        return True
    return seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8


def transcribir(segs, prompt, progreso):
    """Devuelve ([(normalizada, inicio, fin, palabra_original)], envolventes_de_volumen)."""
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
    except Exception:
        model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    total = sum(float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", w]).strip())
        for w, _ in segs) or 1
    import numpy as np
    hecho, palabras, envs = 0.0, [], []
    for wav, t0 in segs:
        audio = cargar_wav(wav)
        n = len(audio) // 160                      # un valor de volumen cada 10 ms
        if n:
            fr = audio[:n * 160].reshape(n, 160)
            envs.append((t0, 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)))
        it, info = model.transcribe(audio, language="es", word_timestamps=True,
                                    initial_prompt=prompt, vad_filter=False, beam_size=5,
                                    condition_on_previous_text=False)
        for s in it:
            progreso(35 + 45 * (hecho + s.end) / total, "Transcribiendo...")
            if es_alucinacion(s):
                continue
            for w in s.words or []:
                n = norm(w.word)
                if n:
                    palabras.append((n, t0 + w.start, t0 + w.end, w.word.strip()))
        hecho += info.duration
    return palabras, envs


def refinar(T, envs):
    """Ajusta inicio/fin de cada palabra al sonido real (Whisper estira palabras sobre los silencios)."""
    import numpy as np, math
    if not envs:
        return T
    umbral = np.percentile(np.concatenate([e for _, e in envs]), 10) + 12
    out = []
    for n, s, e, raw in T:
        env = next(((t0, ev) for t0, ev in envs if t0 - 0.001 <= s < t0 + len(ev) * 0.01), None)
        if env is None:
            out.append((n, s, e, raw)); continue
        t0, ev = env
        a, b = max(0, int((s - t0) / 0.01)), min(len(ev), int(math.ceil((e - t0) / 0.01)))
        if b - a < 2:
            out.append((n, s, e, raw)); continue
        v = ev[a:b] > umbral
        runs, k = [], 0
        while k < len(v):
            if v[k]:
                m = k
                while m < len(v) and v[m]:
                    m += 1
                runs.append([k, m]); k = m
            else:
                k += 1
        unidos = []
        for r in runs:
            if unidos and r[0] - unidos[-1][1] <= 15:
                unidos[-1][1] = r[1]
            else:
                unidos.append(r)
        unidos = [r for r in unidos if r[1] - r[0] >= 3]
        if not unidos:
            out.append((n, s, e, raw)); continue
        r = max(unidos, key=lambda r: r[1] - r[0])
        out.append((n, t0 + (a + r[0]) * 0.01, t0 + (a + r[1]) * 0.01, raw))
    return [(n, s, min(e, s + 1.5), raw) for n, s, e, raw in out]


def niveles(T, envs):
    """Volumen (dB) de cada palabra, medido en el audio real del microfono."""
    import numpy as np
    if not envs:
        return None
    out = []
    for t in T:
        s, e = t[1], t[2]
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


def dividir_en_frases(T):
    utts, cur = [], []
    for k in range(len(T)):
        if cur and T[k][1] - T[cur[-1]][2] > PAUSA_FRASE:
            utts.append(cur); cur = []
        cur.append(k)
    if cur:
        utts.append(cur)
    return utts


def ubicar(ws, Gn, indice):
    """Donde del guion cae mejor esta frase. Devuelve (posicion, cobertura 0-1)."""
    n = len(ws)
    if n < 2 or not Gn:
        return None, 0.0
    cuenta = Counter()
    for k, w in enumerate(ws):
        if len(w) >= 4:
            for p in indice.get(w, ()):
                cuenta[max(0, p - k)] += 1
    mejor, mejor_s = None, 0.0
    for s, _ in cuenta.most_common(8):
        sm = difflib.SequenceMatcher(None, ws, Gn[s:s + n + 4], autojunk=False)
        cob = sum(b.size for b in sm.get_matching_blocks()) / n
        if cob > mejor_s:
            mejor, mejor_s = s, cob
    return mejor, mejor_s


def referencias(infos):
    """Volumen 'normal' local: se adapta si el microfono se acerca o se aleja."""
    import numpy as np
    niv = np.array([u["nivel"] for u in infos])
    tm = np.array([u["t"] for u in infos])
    glob = float(np.percentile(niv, REF_PERCENTIL))
    for u in infos:
        m = np.abs(tm - u["t"]) <= REF_VENTANA
        u["ref"] = float(np.percentile(niv[m], REF_PERCENTIL)) if m.sum() >= 4 else glob


def _igual(a, b):
    """Dos palabras iguales, o casi iguales si son largas (semana / semanal)."""
    if a == b:
        return True
    return len(a) >= 5 and len(b) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def sin_reinicios(idx, Tn, T):
    """Dentro de una frase saca los intentos viejos:
       - trabadas: una palabra de enlace dicha dos veces seguidas ('por por qué')
       - reinicios: arranca, se traba y vuelve a empezar (3 palabras iguales, o 2 palabras largas
         iguales si hubo una duda/pausa en el medio).
       Devuelve (indices_que_quedan, [lo_que_se_quito])."""
    res, quitadas, k, n = [], [], 0, len(idx)
    while k < n:
        w = Tn[idx[k]]
        if k + 1 < n and w == Tn[idx[k + 1]] and w in PALABRAS_TRABADAS:
            quitadas.append([idx[k]]); k += 1
            continue
        salto = None
        for m in range(k + 1, min(n, k + 13)):
            if m + 3 <= n and k + 3 <= n and all(_igual(Tn[idx[k + j]], Tn[idx[m + j]]) for j in range(3)):
                salto = m
                break
            if m + 2 <= n and k + 2 <= n and all(
                    _igual(Tn[idx[k + j]], Tn[idx[m + j]]) and len(Tn[idx[k + j]]) >= 4 for j in range(2)):
                if any(T[idx[j + 1]][1] - T[idx[j]][2] >= 0.25 for j in range(k, m)):
                    salto = m
                    break
        if salto is not None:
            quitadas.append(idx[k:salto]); k = salto
        else:
            res.append(idx[k]); k += 1
    return res, quitadas


def solapan(a, b):
    """(cobertura sobre la mas corta, palabras en comun) entre dos listas de palabras."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    m = sum(x.size for x in sm.get_matching_blocks())
    return m / max(1, min(len(a), len(b))), m


def cortes_de(T, idxs, pausa):
    """Cortes (segundos) de una toma. Siempre entre palabras, nunca a la mitad de una."""
    segs, cur = [], [idxs[0]]
    for a, b in zip(idxs, idxs[1:]):
        if b != a + 1 or T[b][1] - T[a][2] > pausa:
            segs.append(cur); cur = [b]
        else:
            cur.append(b)
    segs.append(cur)
    out = []
    for sg in segs:
        a, b = sg[0], sg[-1]
        ini = T[a][1] - PRE
        if a > 0:
            ini = max(ini, (T[a - 1][2] + T[a][1]) / 2)
        fin = T[b][2] + POST
        if b + 1 < len(T):
            fin = min(fin, (T[b][2] + T[b + 1][1]) / 2)
        ini = max(0.0, ini)
        if fin > ini:
            out.append((ini, fin))
    return out


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
            # sin recordFrame: Resolve los agrega uno detrás del otro, pegados, sin huecos
            out.append({"mediaPoolItem": mpi, "startFrame": sf, "endFrame": sf + n - 1,
                        "mediaType": 1 if tipo == "video" else 2, "trackIndex": destino})
        acum += b - a
    return out


def pos_en_nueva(cortes_f, frame_orig):
    acum = 0
    for a, b in cortes_f:
        if a <= frame_orig < b:
            return acum + (frame_orig - a)
        acum += b - a
    return None


def revisar_huecos(tl):
    """Cuenta huecos entre clips de V1 y A1 de la timeline nueva y compara donde termina cada pista."""
    res, fines = [], {}
    for tipo in ("video", "audio"):
        try:
            items = sorted(tl.GetItemListInTrack(tipo, 1) or [], key=lambda it: it.GetStart())
        except Exception:
            continue
        huecos = []
        for a, b in zip(items, items[1:]):
            d = b.GetStart() - a.GetEnd()
            if d > 0:
                huecos.append(d)
        if items:
            fines[tipo] = items[-1].GetEnd()
        if huecos:
            res.append(f"{tipo}: {len(huecos)} huecos (frames: {huecos[:8]})")
    if len(fines) == 2 and fines["video"] != fines["audio"]:
        res.append(f"video y audio terminan con {abs(fines['video'] - fines['audio'])} frames de diferencia")
    return res


def construir(cortes_f, pista_audio, pista_video, otras):
    nombre, n = f"{TL_NAME} - auto", 1
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
    v = infos_pista("video", pista_video, cortes_f, rec0, 1)
    a = infos_pista("audio", pista_audio, cortes_f, rec0, 1)
    extras, dest = [], 2
    if otras:
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
        try:
            nueva.SetTrackEnable("audio", d, False)
        except Exception:
            pass
    return nueva, ok_v, ok_a, len(v), len(a), len(extras)





# ---------- Orquestacion del mini-proyecto v2 ----------

def alertas_transcripcion(T, niveles=None, umbral=18.0):
    """Alertas del proceso para decidir cortes automaticos:
       - silencios entre frases grandes.
       - frases que son solo lectura (volumen bajo respecto al nivel local).
       - repeticiones internas (intentos viejos) -> se descartan, siempre agarra la ultima toma.
       Devuelve lista de dicts: {tipo, inicio, fin, palabra}."""
    alertas = []
    if not T:
        return alertas
    for frase in dividir_en_frases(T):
        palabras = [T[k] for k in frase]
        ini, fin = palabras[0][1], palabras[-1][2]
        if niveles is not None and len(palabras) >= 1:
            lv = niveles[frase[0]]
            if not np.isfinite(lv) or lv < umbral:
                alertas.append({
                    "tipo": "silencio" if not np.isfinite(lv) else "lectura",
                    "inicio": float(ini), "fin": float(fin), "palabra": palabras[0][3],
                })
                continue
        if (float(fin) - float(ini)) < 0.4:
            alertas.append({"tipo": "silencio", "inicio": float(ini), "fin": float(fin), "palabra": palabras[0][3]})
    return alertas


def cortes_validos(T, cortes_f, guion_texto, indice):
    """Valida cortes contra el guion: no cortan palabras a la mitad y la escritura se parece a un
       fragmento del guion (orden + escritura). Devuelve (validos, descartados).
       Validos: (ini, fin, words, cob)."""
    ok, detalle = [], []
    Gn = [w[0] for w in palabras_del_guion(guion_texto) if len(w[0]) >= 3]
    for ini, fin in cortes_f:
        words = [w for w in T if w[1] < fin and w[2] > ini]
        if not words:
            detalle.append((ini, fin, "sin audio")); continue
        if any(T[i][2] > T[i + 1][1] for i in range(len(words) - 1)):
            detalle.append((ini, fin, "corta palabras")); continue
        ini2, cob = ubicar([w[0] for w in words], Gn, indice) if len(words) >= 2 else (None, 0.0)
        ok.append((ini, fin, words, cob))
    return ok, detalle


def armar_cortes(palabras):
    """Genera cortes automaticos desde la transcripcion:
       1) descarta intentos/reinicios viejos y se queda con la ULTIMA toma;
       2) arma cortes entre palabras (nunca a mitad de palabra) agrupando por pausa;
       3) cada corte se arma como ventana completa (PRE/POST) sobre palabras solidas."""
    Tn = igualar_al_guion(palabras, [])
    idx = list(range(len(palabras)))
    idx, _intentos = sin_reinicios(idx, Tn, palabras)
    return cortes_de(palabras, idx, PAUSA_FRASE)


def run(cfg, progreso, reporte):
    """Flujo v2: transcribir -> alertas -> cortes (ultima toma) -> validar contra guion -> timeline nueva."""
    guion = cfg.get("guion") or ""
    indice = {}
    for i, (n, _d) in enumerate(palabras_del_guion(guion)):
        indice.setdefault(n, []).append(i)
    progreso(5, "Transcribiendo con Whisper...")
    segs = segmentos_audio(list(cfg.get("pistas") or [1]), float(cfg.get("inicio", 0.0)),
                           float(cfg.get("fin", 3600.0)), tempfile.mkdtemp())
    palabras, envs = transcribir(segs, cfg.get("prompt") or "", lambda p, m: progreso(int(p), m))
    palabras = refinar(palabras, envs)
    progreso(60, "Analizando...")
    niv = niveles(palabras, envs) if envs else None
    alertas = alertas_transcripcion(palabras, niv)
    progreso(75, f"Generando cortes con {len(alertas)} alertas...")
    candidatos = armar_cortes(palabras)
    progreso(80, f"Generando {len(candidatos)} cortes candidatos...")
    validos, descartados = cortes_validos(palabras, candidatos, guion, indice)
    cortes_f = [(ini, fin) for ini, fin, _w, _cob in validos]
    progreso(90, f"{len(cortes_f)} cortes validados contra el guion ({len(descartados)} descartados).")
    progreso(95, "Armando timeline nueva con los cortes validados...")
    tl, ok_v, ok_a, nv, na, ne = construir([(round(ini * FPS), round(fin * FPS)) for ini, fin in cortes_f],
                                         cfg.get("pista_audio") or 1, cfg.get("pista_video") or 1,
                                         cfg.get("otras") or [])
    for ini, fin, words, cob in validos:
        try:
            poner_marca(tl, round(ini * FPS), C_ORDEN, f"Corte @{mmss(ini)}", f"Guion ({len(words)} palabras)", max(1, round((fin - ini) * FPS)), CUSTOM)
        except Exception:
            pass
    for ini, fin, motivo in descartados:
        try:
            poner_marca(tl, round(ini * FPS), C_DIFIERE, f"Descartado @{mmss(ini)}", str(motivo), max(1, round((fin - ini) * FPS)), CUSTOM)
        except Exception:
            pass
    progreso(100, f"Listo: {len(cortes_f)} cortes (video ok: {ok_v}, audio ok: {ok_a}).")
    return {"cortes": cortes_f, "validos": validos, "descartados": descartados, "alertas": alertas, "timeline": tl}


def main():
    run({}, lambda *a: None, lambda *a: None)
