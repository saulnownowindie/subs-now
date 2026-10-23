"""
Indie Now · mezcla de pistas de audio
=====================================
Convierte varias pistas de una timeline en un único audio para transcribir,
respetando la posición temporal de cada clip (los huecos quedan en silencio).

Trabaja por ventanas para no cargar una hora de audio en memoria.
Solo necesita numpy y la librería estándar (los WAV ya vienen de FFmpeg).
"""

from __future__ import annotations

import math
import os
import wave

import numpy as np

RATE = 16000          # mismo formato que usa faster-whisper
VENTANA = 240.0       # segundos por bloque de mezcla
MUESTRAS_VENTANA = int(VENTANA * RATE)


def duracion_wav(path):
    """Duración en segundos de un WAV (rápido: lee la cabecera)."""
    with wave.open(path, "rb") as w:
        if not w.getframerate():
            return 0.0
        return w.getnframes() / float(w.getframerate())


def _leer_tramo(path, desde, cantidad):
    """Lee `cantidad` frames desde `desde` y devuelve float32 mono [-1, 1]."""
    if cantidad <= 0:
        return np.zeros(0, dtype=np.float32)
    with wave.open(path, "rb") as w:
        canales = w.getnchannels()
        ancho = w.getsampwidth()
        if ancho != 2:
            raise RuntimeError(f"Se esperaba PCM de 16 bits en {path!r}.")
        total = w.getnframes()
        desde = max(0, min(int(desde), total))
        cantidad = min(int(cantidad), total - desde)
        if cantidad <= 0:
            return np.zeros(0, dtype=np.float32)
        w.setpos(desde)
        crudo = w.readframes(cantidad)
    datos = np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0
    if canales > 1:
        datos = datos.reshape(-1, canales).mean(axis=1)
    return datos


def _escribir_wav(path, muestras):
    datos = np.clip(muestras, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((datos * 32767.0).astype(np.int16).tobytes())


def mezclar(por_pista, duracion, carpeta_tmp, ventana=VENTANA):
    """Mezcla varias pistas y devuelve [(wav, t0)] listos para transcribir.

    por_pista: [(indice_pista, [(wav, t0), ...]), ...]
    duracion:  duración total del rango, en segundos.
    """
    eventos = []
    for indice, segmentos in por_pista:
        for wav, t0 in segmentos:
            try:
                dur = duracion_wav(wav)
            except Exception:
                continue
            if dur > 0:
                eventos.append((wav, float(t0), dur, indice))
    if not eventos:
        return []

    salidas = []
    bloques = max(1, int(math.ceil(duracion / ventana)))
    for k in range(bloques):
        inicio = k * ventana
        fin = min(duracion, inicio + ventana)
        cantidad = int(round((fin - inicio) * RATE))
        if cantidad <= 0:
            continue
        buffer = np.zeros(cantidad, dtype=np.float32)
        activas = set()
        for wav, t0, dur, indice in eventos:
            ini = max(t0, inicio)
            fin_ev = min(t0 + dur, fin)
            if fin_ev <= ini:
                continue
            activas.add(indice)
            destino = int(round((ini - inicio) * RATE))
            cantidad_ev = int(round((fin_ev - ini) * RATE))
            origen = int(round((ini - t0) * RATE))
            try:
                tramo = _leer_tramo(wav, origen, cantidad_ev)
            except Exception:
                continue
            n = min(len(tramo), cantidad - destino)
            if n > 0 and destino < cantidad:
                buffer[destino:destino + n] += tramo[:n]

        if not activas:
            continue
        # Reparto de ganancia entre las pistas que suenan en este bloque.
        buffer /= float(len(activas))
        path = os.path.join(carpeta_tmp, f"mix_{k:03d}.wav")
        _escribir_wav(path, buffer)
        salidas.append((path, inicio))
    return salidas
