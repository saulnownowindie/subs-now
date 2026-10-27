"""
Indie Now - Verificar la timeline cortada contra el guion (DaVinci Resolve Studio, Windows)

Se corre DESPUES de cortes.py, con la timeline cortada abierta (y se puede volver a correr cada vez que
la arreglás a mano). Transcribe el audio tal como suena en la timeline, lo alinea con el guion en orden y
deja marcadores de color donde hay algo para revisar:

  Rojo    FALTA              el guion dice algo que no se oye
  Rosa    DIFIERE / SOBRA    se oye algo distinto, de mas o repetido (arranque en falso, toma duplicada)
  Azul    SILENCIO           pausa larga entre palabras (o al inicio / final)

Necesita cortes.py en la misma carpeta (usa sus funciones).
Requiere: pip install faster-whisper python-docx numpy   y ffmpeg en PATH
"""
import os, sys, tempfile, threading, difflib, wave
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cortes as C        # conecta con Resolve y trae transcribir(), refinar(), poner_marca(), etc.

VERSION = "verificar v1"
CUSTOM = "indienow-verificar"
SILENCIO = 0.7            # segundos: pausa mas larga que esto se marca (se cambia en la ventana)
MIN_SOBRA = 4             # una sola palabra de mas se ignora si tiene este largo o menos (eh, mm, y...)


def audio_continuo(segs, tmp):
    """Une los clips de la pista en un solo audio, igual a como suena la timeline (los huecos quedan en silencio)."""
    import numpy as np
    trozos = [(t0, C.cargar_wav(w)) for w, t0 in segs]
    total = max(int(round(t0 * 16000)) + len(a) for t0, a in trozos)
    out = np.zeros(total, dtype=np.float32)
    for t0, a in trozos:
        k = int(round(t0 * 16000))
        out[k:k + len(a)] = a
    ruta = os.path.join(tmp, "continuo.wav")
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes())
    return [(ruta, 0.0)]


def analizar(T, G, silencio, dur_total):
    """Alinea lo que se oye (T) con el guion (G) y devuelve (eventos, palabras_ok)."""
    Tn = [t[0] for t in T]
    palabras = [w for w, _ in G]
    vocab = sorted(set(palabras)); vs = set(vocab); cache = {}
    for k, w in enumerate(Tn):                       # nombres mal oidos se igualan a los del guion
        if w not in vs:
            if w not in cache:
                m = difflib.get_close_matches(w, vocab, n=1, cutoff=0.8)
                cache[w] = m[0] if m else w
            Tn[k] = cache[w]
    ev, ok = [], 0
    ops = difflib.SequenceMatcher(None, palabras, Tn, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            ok += i2 - i1
            continue
        guion_txt = " ".join(G[k][1] for k in range(i1, i2))
        se_oye = " ".join(T[k][0] for k in range(j1, j2))
        if tag == "delete":                          # esta en el guion, no se oye
            if i2 - i1 == 1 and len(palabras[i1]) <= 2:
                continue
            if 0 < j1 < len(T):
                t = (T[j1 - 1][2] + T[j1][1]) / 2
            else:
                t = T[j1][1] if j1 < len(T) else T[-1][2]
            ev.append({"t": t, "dur": 0.0, "color": C.C_FALTA, "nombre": "FALTA",
                       "nota": f"El guion dice: {guion_txt[:200]}"})
        elif tag == "insert":                        # se oye y no esta en el guion (o esta repetido)
            if j2 - j1 == 1 and len(Tn[j1]) < MIN_SOBRA:
                continue
            ev.append({"t": T[j1][1], "dur": T[j2 - 1][2] - T[j1][1], "color": C.C_DIFIERE,
                       "nombre": "SOBRA / REPETIDO", "nota": f"Se oye: {se_oye[:200]}"})
        else:                                        # replace: dice otra cosa
            if i2 - i1 == 1 and j2 - j1 == 1 and difflib.SequenceMatcher(None, palabras[i1], Tn[j1]).ratio() >= 0.75:
                ok += 1
                continue
            ev.append({"t": T[j1][1], "dur": T[j2 - 1][2] - T[j1][1], "color": C.C_DIFIERE,
                       "nombre": "DIFIERE DEL GUION", "nota": f"Guion: {guion_txt[:120]}\nSe oye: {se_oye[:120]}"})
    prev = 0.0                                       # silencios (incluye inicio y final)
    for n, s0, e0 in T:
        if s0 - prev > silencio:
            ev.append({"t": prev, "dur": s0 - prev, "color": C.C_SIL, "nombre": "SILENCIO",
                       "nota": f"{s0 - prev:.1f} s sin hablar"})
        prev = e0
    if dur_total - prev > silencio:
        ev.append({"t": prev, "dur": dur_total - prev, "color": C.C_SIL, "nombre": "SILENCIO FINAL",
                   "nota": f"{dur_total - prev:.1f} s sin hablar"})
    ev.sort(key=lambda e: e["t"])
    return ev, ok


def correr(cfg, progreso, reporte):
    tl = C.timeline
    progreso(2, "Leyendo guion...")
    G = C.palabras_del_guion(C.leer_guion(cfg["guion"]))
    if not G:
        raise RuntimeError("No pude sacar palabras del guion.")
    f_ini, f_fin = C.TL_START, tl.GetEndFrame()
    prompt = " ".join(d for _, d in G)[:600]
    clave = C.clave_cache(cfg["pista"], f_ini, f_fin, prompt)
    datos = C.cache_leer(clave) if cfg["cache"] else None
    if datos:
        T, envs = datos
        progreso(80, "Usando la transcripción guardada (el audio no cambió)...")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            progreso(5, "Extrayendo audio de la timeline...")
            segs = C.segmentos_audio(cfg["pista"], f_ini, f_fin, tmp)
            if not segs:
                raise RuntimeError("No hay audio en esa pista.")
            segs = audio_continuo(segs, tmp)
            progreso(30, "Cargando modelo...")
            T, envs = C.transcribir(segs, prompt, progreso)
        if T:
            C.cache_guardar(clave, (T, envs))
    if not T:
        raise RuntimeError("No se transcribió nada.")
    T = C.refinar(T, envs)
    progreso(85, "Comparando con el guion...")
    dur_total = (f_fin - f_ini) / C.FPS
    ev, ok = analizar(T, G, cfg["silencio"], dur_total)

    try:
        tl.DeleteMarkerByCustomData(CUSTOM)          # saca los marcadores de la verificacion anterior
    except Exception:
        pass
    progreso(92, "Poniendo marcadores...")
    cuenta, fallos = {}, 0
    for e in ev:
        fr = round(e["t"] * C.FPS)
        dur = max(1, round(e["dur"] * C.FPS))
        if C.poner_marca(tl, fr, e["color"], e["nombre"], e["nota"], dur, CUSTOM):
            cuenta[e["color"]] = cuenta.get(e["color"], 0) + 1
        else:
            fallos += 1

    pct = 100 * ok / len(G)
    rep = [VERSION,
           f"{ok} de {len(G)} palabras del guion están, en orden ({pct:.0f} %).",
           f"Marcadores: rojo {cuenta.get(C.C_FALTA, 0)} (falta), rosa {cuenta.get(C.C_DIFIERE, 0)} (difiere/sobra/repetido), "
           f"azul {cuenta.get(C.C_SIL, 0)} (silencio)." + (f" {fallos} no se pudieron poner." if fallos else "")]
    if not ev:
        rep.append("Todo coincide con el guion y no hay silencios largos.")
    else:
        rep.append("")
        for e in ev[:40]:
            rep.append(f"{C.mmss(e['t']):>6}  {e['nombre']}: {e['nota'].splitlines()[0][:90]}")
        if len(ev) > 40:
            rep.append(f"... y {len(ev) - 40} más (están en los marcadores).")
    try:                                              # copia completa al lado del guion
        ruta = os.path.join(os.path.dirname(cfg["guion"]), C.TL_NAME + "_verificacion.txt")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(rep[:3]) + "\n\n")
            for e in ev:
                f.write(f"{C.mmss(e['t']):>6}  {e['color']:6} {e['nombre']}: {e['nota'].replace(chr(10), ' | ')}\n")
        rep.append("\nGuardado: " + ruta)
    except Exception:
        pass
    reporte(rep)
    progreso(100, "Listo")


def main():
    tl = C.timeline
    n_audio = tl.GetTrackCount("audio")
    root = tk.Tk(); root.title("Indie Now - " + VERSION + "  [" + C.TL_NAME + "]"); root.geometry("460x600")
    v_guion = tk.StringVar(); v_pista = tk.StringVar(value="A1")
    v_sil = tk.StringVar(value=str(SILENCIO)); v_cache = tk.BooleanVar(value=True)

    def elegir():
        p = filedialog.askopenfilename(filetypes=[("Guion", "*.txt *.docx")])
        if p: v_guion.set(p)

    ttk.Label(root, text="Se verifica la timeline abierta: " + C.TL_NAME, wraplength=430).pack(anchor="w", padx=12, pady=(12, 0))
    ttk.Button(root, text="Elegir guion...", command=elegir).pack(fill="x", padx=12, pady=(8, 0))
    ttk.Label(root, textvariable=v_guion, wraplength=430).pack(anchor="w", padx=12)
    ttk.Label(root, text="Audio (micrófono)").pack(anchor="w", padx=12, pady=(8, 0))
    ttk.Combobox(root, textvariable=v_pista, state="readonly",
                 values=[f"A{i}" for i in range(1, n_audio + 1)]).pack(fill="x", padx=12)
    fila = ttk.Frame(root); fila.pack(fill="x", padx=12, pady=(8, 0))
    ttk.Label(fila, text="Marcar silencios de más de").pack(side="left")
    ttk.Entry(fila, textvariable=v_sil, width=5).pack(side="left", padx=4)
    ttk.Label(fila, text="segundos").pack(side="left")
    ttk.Checkbutton(root, text="Usar transcripción guardada si el audio no cambió",
                    variable=v_cache).pack(anchor="w", padx=12, pady=(8, 0))
    barra = ttk.Progressbar(root, maximum=100); barra.pack(fill="x", padx=12, pady=(12, 0))
    estado = ttk.Label(root, text="", wraplength=430); estado.pack(anchor="w", padx=12)
    caja = tk.Text(root, height=14, wrap="word", state="disabled"); caja.pack(fill="both", expand=True, padx=12, pady=(4, 0))

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
            sil = float(v_sil.get().replace(",", "."))
        except ValueError:
            return messagebox.showwarning("Silencio", "Poné un número, por ejemplo 0.7")
        cfg = {"guion": v_guion.get(), "pista": int(v_pista.get()[1:]), "silencio": sil, "cache": v_cache.get()}
        def t():
            try: correr(cfg, progreso, reporte)
            except Exception as ex:
                import traceback; traceback.print_exc()
                progreso(0, "Error: " + str(ex))
        threading.Thread(target=t, daemon=True).start()

    ttk.Button(root, text="Verificar", command=go).pack(fill="x", padx=12, pady=12)
    root.mainloop()


if __name__ == "__main__":
    main()
