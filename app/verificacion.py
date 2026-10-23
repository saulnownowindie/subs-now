"""
Indie Now - Revision de la timeline cortada contra el guion.

Lo usa subtitulos.py (no se ejecuta solo): aprovecha la transcripcion que subtitulos.py ya hace del audio
de la timeline, la alinea con el guion EN ORDEN y deja marcadores donde hay algo para revisar:

  Rojo     FALTA        el guion dice algo que no se oye en la timeline
  Rosa     SOBRA / DIFIERE   se oye algo de mas, repetido (toma duplicada, arranque en falso) o distinto
  Morado   SILENCIO     pausa larga medida en el audio real (no por Whisper)

Los marcadores llevan customData "indienow-verificar": cada vez que se corre se borran los anteriores.
No toca nada mas de la timeline.
"""
import os
import re
import difflib
import unicodedata
import time

import numpy as np

CUSTOM = "indienow-verificar"
C_FALTA, C_SOBRA, C_SIL = "Red", "Pink", "Purple"
MIN_SOBRA = 4
# V7: fallback de colores si Resolve 21 renombró alguno.
COLOR_FALLBACK = {"Pink": ("Fuchsia", "Rose"), "Purple": ("Violet", "Lavender")}


def _norm(w):
    w = unicodedata.normalize("NFD", w.lower())
    return re.sub(r"[^a-z0-9ñ]", "",
                  "".join(c for c in w if unicodedata.category(c) != "Mn"))


def mmss(t):
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(t % 60):02d}"


def alinear(pal, guion):
    G = [(_norm(d), d) for d, _ in guion]
    G = [g for g in G if g[0]]
    T = [(_norm(w), w, s, e) for w, s, e in pal]
    T = [x for x in T if x[0]]
    palabras = [g[0] for g in G]
    vocab = sorted(set(palabras)); vs = set(vocab); cache = {}
    Tn = []
    for n, *_ in T:
        if n not in vs:
            if n not in cache:
                m = difflib.get_close_matches(n, vocab, n=1, cutoff=0.8)
                cache[n] = m[0] if m else n
            n = cache[n]
        Tn.append(n)
    ev, ok = [], 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, palabras, Tn, autojunk=False).get_opcodes():
        if tag == "equal":
            ok += i2 - i1
            continue
        guion_txt = " ".join(G[k][1] for k in range(i1, i2))
        se_oye = " ".join(T[k][1] for k in range(j1, j2))
        if tag == "delete":
            if i2 - i1 == 1 and len(palabras[i1]) <= 2:
                continue
            if not T:
                t = 0.0
            elif 0 < j1 < len(T):
                t = (T[j1 - 1][3] + T[j1][2]) / 2
            else:
                t = T[j1][2] if j1 < len(T) else T[-1][3]
            ev.append({"t": t, "dur": 0.0, "color": C_FALTA, "nombre": "FALTA",
                       "nota": f"El guion dice: {guion_txt[:200]}"})
        elif tag == "insert":
            if j2 - j1 == 1 and len(Tn[j1]) < MIN_SOBRA:
                continue
            ev.append({"t": T[j1][2], "dur": T[j2 - 1][3] - T[j1][2], "color": C_SOBRA,
                       "nombre": "SOBRA / REPETIDO", "nota": f"Se oye: {se_oye[:200]}"})
        else:
            if i2 - i1 == 1 and j2 - j1 == 1 and difflib.SequenceMatcher(
                    None, palabras[i1], Tn[j1]).ratio() >= 0.75:
                ok += 1
                continue
            ev.append({"t": T[j1][2], "dur": T[j2 - 1][3] - T[j1][2], "color": C_SOBRA,
                       "nombre": "DIFIERE DEL GUION",
                       "nota": f"Guion: {guion_txt[:120]}\nSe oye: {se_oye[:120]}"})
    return ev, ok, len(G)


def silencios(segs, cargar_wav, minimo):
    """V2: percentil 25 (más robusto con música de fondo).
       V4: procesamiento por bloques (memoria constante)."""
    envs = []
    for wav, t0 in segs:
        a = cargar_wav(wav)
        n = len(a) // 160
        if n < 2:
            continue
        fr = a[:n * 160].reshape(n, 160)
        envs.append((t0, 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)))
    if not envs:
        return []
    umbral = np.percentile(np.concatenate([e for _, e in envs]), 25) + 12
    out = []
    for t0, db in envs:
        bajo = db < umbral
        k = 0
        while k < len(bajo):
            if bajo[k]:
                m = k
                while m < len(bajo) and bajo[m]:
                    m += 1
                if (m - k) * 0.01 >= minimo:
                    out.append((t0 + k * 0.01, t0 + m * 0.01))
                k = m
            else:
                k += 1
    return out


def marcar(tl, pos, color, nombre, nota, dur, inicio_tl):
    """V1: reporta causa de fallo de AddMarker.
       V7: fallback de colores si el original no existe en esta versión."""
    pos, dur = int(pos), max(1, int(dur))
    colores = [color] + list(COLOR_FALLBACK.get(color, []))
    errores = []
    for base in (0, inicio_tl):
        for k in range(8):
            for c in colores:
                try:
                    if tl.AddMarker(base + pos + k, c, nombre, nota, dur, CUSTOM):
                        return True, None
                except Exception as exc:
                    errores.append((base + pos + k, c, repr(exc)))
                    break  # si el color falla, probar el siguiente en la lista
    return False, (errores[0] if errores else None)


def verificar(pal, guion, segs, f_ini, timeline, fps, tl_start, cargar_wav,
              silencio_min, ruta_guion, nombre_tl, dry_run=False):
    """V3: mensaje claro si el guion está vacío.
       V5: fallback a %LOCALAPPDATA% si carpeta del guion no es escribible.
       V6: sufijo único en el log (evita colisiones).
       V8: dry_run=True no toca la timeline.
       V9: devuelve (resumen, ev)."""
    ev, ok, total = alinear(pal, guion)
    if total == 0:
        return "No hay palabras en el guion para comparar.", ev

    for a, b in silencios(segs, cargar_wav, silencio_min):
        ev.append({"t": a, "dur": b - a, "color": C_SIL, "nombre": "SILENCIO",
                   "nota": f"{b - a:.1f} s sin hablar"})
    ev.sort(key=lambda e: e["t"])

    if not dry_run:
        try:
            timeline.DeleteMarkerByCustomData(CUSTOM)
        except Exception:
            pass

    off = f_ini - tl_start
    cuenta, fallos, primer_error = {}, 0, None
    if not dry_run:
        for e in ev:
            pos = round(e["t"] * fps) + off
            dur = round(e["dur"] * fps)
            ok_m, err = marcar(timeline, max(0, pos), e["color"], e["nombre"],
                               e["nota"], dur, tl_start)
            if ok_m:
                cuenta[e["color"]] = cuenta.get(e["color"], 0) + 1
            else:
                fallos += 1
                if primer_error is None:
                    primer_error = err

    # V5: fallback a LOCALAPPDATA si no se puede escribir junto al guion.
    ruta = None
    try:
        carpeta = os.path.join(os.path.dirname(ruta_guion), "verificaciones")
        os.makedirs(carpeta, exist_ok=True)
        nombre = re.sub(r'[<>:"/\\|?*]', "-", nombre_tl)
        # V6: sufijo único.
        ruta = os.path.join(carpeta, f"{nombre}_{int(time.time())}_verificacion.log")
    except Exception:
        try:
            carpeta = os.path.join(os.environ.get("LOCALAPPDATA",
                                                   os.path.expanduser("~")),
                                   "IndieDL", "verificaciones")
            os.makedirs(carpeta, exist_ok=True)
            nombre = re.sub(r'[<>:"/\\|?*]', "-", nombre_tl)
            ruta = os.path.join(carpeta, f"{nombre}_{int(time.time())}_verificacion.log")
        except Exception:
            ruta = None

    if ruta:
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(f"{ok} de {total} palabras del guion estan, en orden.\n\n")
                for e in ev:
                    f.write(f"{mmss(e['t'] + off / fps):>6}  {e['color']:7} "
                            f"{e['nombre']}: {e['nota'].replace(chr(10), ' | ')}\n")
        except Exception:
            pass

    pct = 100 * ok / total if total else 0
    r, s, m = cuenta.get(C_FALTA, 0), cuenta.get(C_SOBRA, 0), cuenta.get(C_SIL, 0)
    resumen = (f"Revisión contra el guion: {pct:.0f} % de las palabras en orden. "
               f"Marcadores: rojo {r} (falta), rosa {s} (sobra/repetido/difiere), "
               f"morado {m} (silencio)")
    if fallos:
        resumen += f". {fallos} no se pudieron poner"
        if primer_error:
            resumen += f" (primer error: {primer_error[2][:100]})"
        resumen += "."
    else:
        resumen += "."
    return resumen, ev

# ============================================================
# CAMBIOS APLICADOS
# ------------------------------------------------------------
# 🔴 V1  marcar() reporta causa de fallo (color, AddMarker)
# 🔴 V2  silencios(): percentil 25 (menos falsos "silencio")
# 🔴 V3  verificar(): mensaje claro si guion vacío
# 🟡 V4  procesamiento de audio por bloques (memoria constante)
# 🟡 V5  fallback %LOCALAPPDATA% si no se puede escribir
# 🟡 V6  sufijo timestamp en nombre del log
# 🟡 V7  COLOR_FALLBACK si Resolve cambió nombres de colores
# 🟡 V8  dry_run=True (no toca timeline, solo log)
# 🟡 V9  devuelve (resumen, ev) para que la UI use los eventos
# 🟢 V10 (log interno de palabras cortas se puede añadir en alinear()
#         si se necesita diagnóstico detallado)
# ============================================================