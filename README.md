# Indie Now · Subtítulos

Generador de subtítulos para **DaVinci Resolve** a partir de una timeline o de un guion.
Transcribe con Whisper (faster-whisper, GPU si hay), compara contra el guion, arma el SRT
y lo aplica como pista de subtítulos usando una plantilla del Media Pool.

Interfaz propia con la estética de Indie Now: fondo casi negro, tarjetas redondeadas y
acento amarillo `#FFF333`. Nada de consolas negras ni de pasos a mano.

---

## Instalación en un PC nuevo

1. Instalá Git (una sola vez): `winget install --id Git.Git -e`
2. Cloná y ejecutá el instalador:

```powershell
git clone https://github.com/saulnownowindie/subs-now.git
cd subs-now
.\INSTALAR.bat
```

El instalador deja todo listo:

| Paso | Qué hace |
|---|---|
| 1 | Verifica Python 3.10–3.13. Si no hay, lo instala solo. |
| 2 | Crea el entorno virtual en `%LOCALAPPDATA%\IndieDL\venv` (lo comparte con Indie Now DL si ya está). |
| 3 | Instala faster-whisper, ctranslate2, av, tokenizers, numpy, Pillow y python-docx (~600 MB). |
| 4 | Instala **FFmpeg** con winget (esta herramienta no funciona sin él). |
| 5 | Copia la app a `%LOCALAPPDATA%\IndieDL\subs`. |
| 6 | Agrega el lanzador al menú **Scripts → Utility** de Resolve y crea accesos directos. |

Es idempotente: podés volver a ejecutarlo cuando actualices con `git pull`.

### Requisitos

- Windows 10 u 11 (64 bits)
- **DaVinci Resolve** instalado, con scripting habilitado:
  `Preferencias → Sistema → General → External scripting using = Local`
- Resolve abierto con un proyecto y una timeline (la app se conecta al abrir)
- Opcional: GPU NVIDIA con CUDA para transcribir mucho más rápido (si no, usa CPU)

---

## Uso

1. Abrí Resolve con tu proyecto y timeline.
2. Entrá por **Workspace → Scripts → Utility → Indie Now - Subtitulos**
   (o el acceso directo «Indie Now Subtitulos»).
3. Elegí qué hacer: subtítulos, `.txt`, comparar con guion, o sólo SRT.
4. Marcá las pistas de audio, el rango (In/Out o timeline completa) y la plantilla.
5. **GENERAR SUBTÍTULOS**. Hay botón **CANCELAR** mientras corre.

La primera transcripción descarga el modelo de Whisper (~500 MB) y tarda más; las
siguientes arrancan al toque porque queda en caché.

### Rango

- **SOLO IN / OUT**: usa las marcas de entrada y salida de la timeline.
- **TODA LA LÍNEA**: procesa la timeline completa.

### Dónde se guarda

- El **SRT** va a la ruta de la tarjeta «DÓNDE GUARDAR» (o junto al guion / en Documentos
  si no elegiste ninguna).
- La **transcripción `.txt`** se guarda al lado del SRT y toma su nombre:
  `black parade.srt` → `black parade_transcripcion.txt`. El chip de la tarjeta muestra
  el nombre exacto antes de ejecutar.
- Si el archivo ya existía, **no se borra**: la versión anterior queda como respaldo con
  fecha (`black parade_transcripcion.20261027-154902.txt.bak`). Podés generar la misma
  timeline las veces que quieras que no perdés nada.

---

## Actualizar

```powershell
git pull
.\INSTALAR.bat
```

Si Resolve estaba abierto, reinicialo para que tome el lanzador nuevo del menú Scripts.

---

## Compilar el .exe (opcional)

Para distribuir sin Python instalado:

```bat
compilar.bat
```

Queda en `dist\IndieNow-Subtitulos\`. Usa el entorno virtual de
`%LOCALAPPDATA%\IndieDL\venv` y guarda el build anterior en `dist_anterior\`.

> El `.exe` empaquetado guarda su configuración y su log dentro de `_internal\`, así que
> arranca con ajustes propios. El flujo normal (Resolve → Scripts) usa el código fuente
> instalado en `%LOCALAPPDATA%\IndieDL\subs`, que es el que actualiza `INSTALAR.bat`.

---

## Dónde quedan las cosas

| Qué | Dónde |
|---|---|
| Programa | `%LOCALAPPDATA%\IndieDL\subs\` |
| Entorno virtual | `%LOCALAPPDATA%\IndieDL\venv\` |
| Configuración | `%LOCALAPPDATA%\IndieDL\subs\subtitulos_config.json` |
| Log | `%LOCALAPPDATA%\IndieDL\subs\subtitulos.log` |
| Lanzador de Resolve | `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\Indie Now - Subtitulos.py` |
| Modelos de Whisper | caché de Hugging Face (`%USERPROFILE%\.cache\huggingface`) |

Nada de eso vive en el repo: configuración, logs y binarios están en `.gitignore`.

---

## Estructura

```
subs-now/
├─ app/
│  ├─ subtitulos.py            # lógica: Resolve, Whisper, SRT, plantillas
│  ├─ subtitulos_ui.py         # interfaz (arranque animado, estado, registro)
│  ├─ indie_ui.py              # kit visual compartido (tarjetas, botones, scroll)
│  ├─ mezcla_audio.py          # mezcla y normalización del audio extraído
│  ├─ gpu_runtime.py           # detección de CUDA y librerías de GPU
│  ├─ verificacion.py          # comparación contra el guion
│  ├─ verificar.py             # utilidad de verificación
│  └─ cortes.py                # utilidades de corte
├─ installer/
│  ├─ instalar.ps1             # lógica del instalador
│  └─ Indie Now - Subtitulos.py   # lanzador para el menú Scripts de Resolve
├─ requirements.txt
├─ requirements-dev.txt
├─ INSTALAR.bat
├─ Iniciar Subtitulos.bat
├─ compilar.bat
└─ IndieNow-Subtitulos.spec
```

---

## Problemas comunes

**«No se pudo conectar con DaVinci Resolve»**
Resolve tiene que estar abierto con un proyecto. Verificá el scripting externo en
`Preferencias → Sistema → General → External scripting using = Local` y reiniciá Resolve.

**«No encontré: ffmpeg»**
`winget install --id Gyan.FFmpeg -e` y volvé a abrir la app. El instalador también lo
resuelve solo.

**La app abre pero no ve la timeline**
Cerrá la app y volvé a abrirla con la timeline activa. El nombre de la timeline se lee al
arrancar y se refresca cada pocos segundos.

**Transcribe lento**
Está usando CPU. Con una GPU NVIDIA, `gpu_runtime.py` detecta CUDA automáticamente; si
falla, la app lo informa en el registro y sigue en CPU.

**Un `.exe` viejo no tiene los cambios**
El flujo normal no usa el `.exe`: Resolve lanza el código instalado. Recompilá solo si
necesitás el ejecutable suelto.

---

## Notas

- Probado en Windows 11 con Python 3.12, Resolve 18/19 y `faster-whisper` 1.2.
- La ventana se arma completa antes de mostrarse y aparece con un fundido corto: no se ve
  el armado ni textos de «Cargando…».
- El scroll de los paneles es propio (rueda suave sobre cualquier tarjeta) y el registro
  tiene scroll independiente.
