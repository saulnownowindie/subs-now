# Indie Now · Subtítulos

Herramienta de subtítulos integrada con DaVinci Resolve. Usa faster-whisper para transcribir, agrega subtítulos a la timeline y ofrece opciones de guion, traducción y revisión.

## Instalación automática en Windows

1. Instalá DaVinci Resolve y habilitá su scripting API en Preferences > System > General > External scripting using.
2. Instalá Git y cloná este repositorio (o descargá el ZIP).
3. Abrí PowerShell en la carpeta descargada y ejecutá:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\installer\setup_windows.ps1
   ```

El instalador configura Python 3.12, un entorno virtual aislado, Whisper y sus dependencias, FFmpeg y el lanzador de Resolve. Si encuentra NVIDIA, intenta preparar las bibliotecas CUDA/cuBLAS/cuDNN junto a la app. Si no hay GPU o CUDA no inicia, la transcripción cambia a CPU y la interfaz informa qué dispositivo está usando.

La primera instalación descarga el modelo `large-v3-turbo` (varios cientos de MB). Para omitir esa descarga inicial y dejar que se baje al transcribir por primera vez:

```powershell
.\installer\setup_windows.ps1 -SkipModelDownload
```

Luego reiniciá DaVinci Resolve y abrí **Workspace > Scripts > Edit > SubsSaul**.

## Requisitos y límites

- Windows 10/11 de 64 bits y Python 3.12.
- DaVinci Resolve instalado; el instalador no puede instalar Resolve ni habilitar su API por el usuario.
- Conexión a Internet durante la primera instalación para descargar paquetes, bibliotecas NVIDIA (solo cuando se detecte NVIDIA) y el modelo.
- Una GPU NVIDIA compatible acelera la transcripción. La CPU permite continuar sin NVIDIA; el modelo grande puede tardar bastante y requiere RAM suficiente.
- Los pesos de Whisper y las DLL de CUDA se descargan al equipo local; no se versionan en GitHub para mantener el repositorio pequeño y permitir reinstalarlos.

## Desarrollo

Dependencias Python en `requirements.txt`. Los módulos de la app viven en `app/`; el lanzador de Resolve y el instalador están en `installer/`.

## Referencias técnicas

- [faster-whisper: requisitos de GPU y uso en CPU](https://github.com/SYSTRAN/faster-whisper#requirements)
- [CTranslate2: instalación y soporte CUDA en Windows](https://opennmt.net/CTranslate2/installation.html)
- [Purfview: bibliotecas cuBLAS/cuDNN para Windows](https://github.com/Purfview/whisper-standalone-win/releases/tag/libs)
