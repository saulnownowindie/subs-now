"""Motor de traduccion de Google con contexto, glosario y memoria.

Pensado para que las traducciones de una misma charla queden coherentes:

  - Ventanas contiguas: las lineas se agrupan en bloques de contexto antes de
    pedir el lote a Google (en vez de frase por frase).
  - Glosario: nombres propios, marcas y terminos del usuario se enmascaran con
    tokens internos (T0, T1...) y se restauran al final, asi un nombre no se
    traduce ni cambia entre bloques.
  - Memoria: se indexa por clave normalizada (minusculas y sin puntuacion de
    relleno) para reutilizar frases ya traducidas (misma frase con mayusculas o
    puntuacion distintas) sin gastar otro viaje a Google.

No reemplaza a un LLM: con estos tres recursos la calidad con Google sube mucho
sin claves ni costo extra.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_TERMINOS = 80
MAX_VENTANA_LINEAS = 20
MAX_VENTANA_CARACTERES = 3400


def clave(texto):
    """Clave normalizada para la memoria de traduccion."""
    t = re.sub(r"\s+", " ", str(texto or "")).strip()
    t = t.casefold().strip(" .,;:!?…¡¿\"'`“”‘’()[]{}<>")
    return t


def normalizar_terminos(nombres=(), texto="", extra=""):
    candidatos = list(nombres or [])
    candidatos += [x.strip() for x in re.split(r"[,;\n]", str(extra or "")) if x.strip()]
    vistos, out = set(), []
    for raw in candidatos:
        t = re.sub(r"\s+", " ", str(raw)).strip(" .,;:\"'()¡¿?[]{}<>–—")
        if len(t) < 2:
            continue
        k = t.casefold()
        if k in vistos:
            continue
        vistos.add(k)
        out.append(t)
    out.sort(key=len, reverse=True)
    return out[:MAX_TERMINOS]


def enmascarar(texto, terminos):
    """Reemplaza nombres/terminos por tokens internos (T0, T1, ...)."""
    terminos = list(terminos or [])
    patrones = []
    for i, term in enumerate(terminos):
        patrones.append((re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.IGNORECASE), f"T{i}"))
    out = str(texto or "")
    for pat, token in patrones:
        out = pat.sub(lambda m, token=token: token, out)
    return out, {f"T{i}": term for i, term in enumerate(terminos)}


def desenmascarar(texto, terminos):
    out = str(texto or "")
    for i, term in enumerate(terminos or []):
        out = re.sub(re.escape(f"T{i}") + r"(?![A-Za-z0-9])", lambda m, t=term: t, out)
    return out


def ventanas(lineas, max_lineas=MAX_VENTANA_LINEAS, max_chars=MAX_VENTANA_CARACTERES):
    grupos, actual, chars = [], [], 0
    for l in lineas:
        if actual and (len(actual) >= max_lineas or chars + len(l) + 1 > max_chars):
            grupos.append(actual)
            actual, chars = [], 0
        actual.append(l)
        chars += len(l) + 1
    if actual:
        grupos.append(actual)
    return grupos


def solicitar(texto, origen, destino, timeout=25):
    params = urllib.parse.urlencode({"client": "gtx", "sl": origen or "auto", "tl": destino, "dt": "t"})
    datos = urllib.parse.urlencode({"q": texto}).encode("utf-8")
    req = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + params,
        data=datos,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/x-www/form-urlencoded; charset=UTF-8"},
    )
    ultimo = None
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
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


class TraductorGoogle:
    def __init__(self, cache_path=None, timeout=25):
        self.cache_path = cache_path
        self.timeout = timeout
        self._cache = {}
        self._cargar()

    def _cargar(self):
        self._cache = {}
        if not self.cache_path:
            return self._cache
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                datos = json.load(f)
            if isinstance(datos, dict):
                self._cache = dict(datos)
        except Exception:
            self._cache = {}
        return self._cache

    def cache(self):
        return self._cache

    def _guardar(self, cache):
        self._cache = cache
        if not self.cache_path:
            return
        try:
            recorte = dict(list(cache.items())[-8000:])
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(recorte, f, ensure_ascii=False)
        except Exception:
            pass

    def _una(self, texto, origen, destino):
        data = solicitar(texto, origen, destino, timeout=self.timeout)
        lineas = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).split("\n")
        while lineas and not lineas[-1].strip():
            lineas.pop()
        return lineas[0].strip() if lineas and lineas[0].strip() else str(texto or "").strip()

    def _lote(self, lineas, origen, destino):
        data = solicitar("\n".join(lineas), origen, destino, timeout=self.timeout)
        lineas_out = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).split("\n")
        while lineas_out and not lineas_out[-1].strip():
            lineas_out.pop()
        if len(lineas_out) == len(lineas):
            return lineas_out
        return [self._una(l, origen, destino) for l in lineas]

    def traducir(self, lineas, origen, destino, terminos=(), progreso=None, umbral=8000):
        lineas = [re.sub(r"\s+", " ", str(x or "")).strip() for x in lineas]
        if not lineas:
            return []
        cache = self._cargar()
        salidas = [None] * len(lineas)
        pendientes, vistos = [], set()
        for i, texto in enumerate(lineas):
            if not texto:
                salidas[i] = ""
                continue
            k = clave(texto)
            if k in cache:
                salidas[i] = cache[k]
                continue
            if k in vistos:
                continue
            vistos.add(k)
            pendientes.append((texto, i))
        traducciones = {}
        orden = [p[0] for p in pendientes]
        hecho = 0
        for ventana in ventanas(orden):
            masked = [enmascarar(l, terminos)[0] for l in ventana]
            respuestas = self._lote(masked, origen, destino)
            for l, tr in zip(ventana, respuestas):
                traducciones[clave(l)] = desenmascarar(tr, terminos)
            hecho += 1
            if progreso:
                progreso(84 + 4 * hecho / max(1, len(orden)), f"Traduciendo al {destino or 'idioma'}...")
        if pendientes:
            for texto, i in pendientes:
                salidas[i] = traducciones[clave(texto)]
        for i, texto in enumerate(lineas):
            if not texto or salidas[i] is not None:
                continue
            salidas[i] = traducciones.get(clave(texto), texto)
        if pendientes:
            for texto, i in pendientes:
                cache[clave(texto)] = traducciones[clave(texto)]
            self._guardar(cache)
        return salidas
