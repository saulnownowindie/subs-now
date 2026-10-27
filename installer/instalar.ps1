#Requires -Version 5.1
<#
    Indie Now - Subtitulos - instalador automatico
    -----------------------------------------------
    Deja la herramienta lista para usar en un PC con Windows 10/11 y DaVinci Resolve:

      1. Python 3.12 (lo instala si falta)
      2. Entorno virtual compartido + dependencias (faster-whisper, ctranslate2, av...)
      3. FFmpeg (obligatorio para esta herramienta)
      4. Copia la app a %LOCALAPPDATA%\IndieDL\subs
      5. Acceso directo y lanzador dentro del menu Scripts de DaVinci Resolve
      6. Verificacion final (Resolve, FFmpeg, Whisper)

    Uso:
        powershell -ExecutionPolicy Bypass -File installer\instalar.ps1
        powershell -ExecutionPolicy Bypass -File installer\instalar.ps1 -SinAbrir -SinResolve
#>
[CmdletBinding()]
param(
    [switch]$SinAccesoDirecto,
    [switch]$SinAbrir,
    [switch]$SinResolve
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoDir  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AppDir  = Join-Path $env:LOCALAPPDATA 'IndieDL'
$SubsDir = Join-Path $AppDir 'subs'
$VenvDir = Join-Path $AppDir 'venv'
$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPyW = Join-Path $VenvDir 'Scripts\pythonw.exe'
$ReqFile = Join-Path $RepoDir 'requirements.txt'

$ResolveApi = Join-Path $env:PROGRAMDATA 'Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting'
$ResolveModules = Join-Path $ResolveApi 'Modules'
$ResolveExe = Join-Path $env:ProgramFiles 'Blackmagic Design\DaVinci Resolve\Resolve.exe'
$ResolveScripts = Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility'

function Titulo {
    Write-Host ''
    Write-Host '  INDIE NOW - SUBTITULOS' -ForegroundColor Black -BackgroundColor Yellow
    Write-Host '  Instalador automatico' -ForegroundColor DarkGray
}
function Paso    { param([string]$t) Write-Host ''; Write-Host "  == $t" -ForegroundColor Yellow }
function Detalle { param([string]$t) Write-Host "     $t" -ForegroundColor DarkGray }
function Bien    { param([string]$t) Write-Host "     OK   $t" -ForegroundColor Green }
function Aviso   { param([string]$t) Write-Host "     !    $t" -ForegroundColor Yellow }
function Mal     { param([string]$t) Write-Host "     X    $t" -ForegroundColor Red }

function Refrescar-Ruta {
    $maquina = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $usuario = [Environment]::GetEnvironmentVariable('Path', 'User')
    $partes  = @()
    foreach ($p in @($maquina, $usuario)) { if ($p) { $partes += $p } }
    $env:Path = ($partes -join ';')
}

function Buscar-Python {
    $candidatos = @(
        @{ Exe = 'py';     Args = @('-3.12') },
        @{ Exe = 'py';     Args = @('-3.13') },
        @{ Exe = 'py';     Args = @('-3')    },
        @{ Exe = 'python'; Args = @()        }
    )
    foreach ($c in $candidatos) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $full = @($c.Args) + @('-c', "import sys;print('%d.%d' % sys.version_info[:2])")
        try { $v = (& $cmd.Source @full 2>$null) } catch { continue }
        if ("$v" -match '^3\.(1[0-3])$') {
            return @{ Exe = $cmd.Source; Args = @($c.Args); Version = "$v" }
        }
    }
    $rutas = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe')
    )
    foreach ($r in $rutas) {
        if (Test-Path $r) {
            try { $v = (& $r -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null) } catch { continue }
            if ("$v" -match '^3\.(1[0-3])$') { return @{ Exe = $r; Args = @(); Version = "$v" } }
        }
    }
    return $null
}

function Instalar-Python {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Detalle 'Probando con winget (Python 3.12)...'
        & $winget.Source install --id Python.Python.3.12 -e --scope user --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        Refrescar-Ruta
        $py = Buscar-Python
        if ($py) { return $py }
    }
    $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
    $destino = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
    Detalle 'Descargando Python 3.12 desde python.org...'
    Invoke-WebRequest -Uri $url -OutFile $destino -UseBasicParsing
    Detalle 'Instalando Python (silencioso, solo para este usuario)...'
    Start-Process -FilePath $destino -Wait -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1', 'Include_launcher=1'
    ) | Out-Null
    Refrescar-Ruta
    return (Buscar-Python)
}

function Instalar-Requisitos {
    & $VenvPy -m pip install --upgrade --disable-pip-version-check --quiet pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar pip.' }
    Detalle 'Instalando dependencias (faster-whisper, ctranslate2, av, tokenizers)...'
    Detalle 'La primera vez son ~600 MB y puede tardar varios minutos.'
    & $VenvPy -m pip install --disable-pip-version-check --quiet --upgrade -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la instalacion de dependencias (revisa la salida de pip).' }
}

function Asegurar-FFmpeg {
    Refrescar-Ruta
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { return $true }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Detalle 'Instalando FFmpeg con winget...'
        & $winget.Source install --id Gyan.FFmpeg -e --scope user --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        Refrescar-Ruta
        if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { return $true }
    }
    return $false
}

function Nuevo-Acceso {
    param([string]$Ruta, [string]$Destino, [string]$Argumentos, [string]$Carpeta, [string]$Descripcion)
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($Ruta)
        $lnk.TargetPath = $Destino
        $lnk.Arguments = $Argumentos
        $lnk.WorkingDirectory = $Carpeta
        $lnk.IconLocation = "$Destino,0"
        $lnk.Description = $Descripcion
        $lnk.Save()
        return $true
    } catch {
        Aviso "No se pudo crear el acceso directo: $($_.Exception.Message)"
        return $false
    }
}

# =============================================================
Titulo

Paso '1/6  Python'
$py = Buscar-Python
if ($py) {
    Bien "Python $($py.Version) encontrado"
} else {
    Aviso 'No hay Python 3.10-3.13. Instalando...'
    $py = Instalar-Python
    if (-not $py) {
        Mal 'No se pudo instalar Python automaticamente.'
        Mal 'Instalalo a mano desde https://www.python.org/downloads/ y volve a ejecutar INSTALAR.bat'
        exit 1
    }
    Bien "Python $($py.Version) instalado"
}

Paso '2/6  Carpeta de la aplicacion'
New-Item -ItemType Directory -Force -Path $SubsDir | Out-Null
Bien $SubsDir

Paso '3/6  Entorno virtual'
if (Test-Path $VenvPy) {
    Bien 'Ya existe, se reutiliza (compartido con Indie Now DL si esta instalado)'
} else {
    $pyArgs = @($py.Args) + @('-m', 'venv', $VenvDir)
    Detalle 'Creando entorno virtual...'
    & $py.Exe @pyArgs
    if (-not (Test-Path $VenvPy)) {
        Mal 'No se pudo crear el entorno virtual.'
        exit 1
    }
    Bien 'Entorno virtual creado'
}

Paso '4/6  Dependencias'
Instalar-Requisitos
Bien 'faster-whisper, ctranslate2, av, tokenizers, numpy, Pillow, python-docx'

Paso '5/6  Archivos de la aplicacion'
$archivos = @(
    'subtitulos.py', 'subtitulos_ui.py', 'indie_ui.py', 'mezcla_audio.py',
    'gpu_runtime.py', 'verificacion.py', 'verificar.py', 'cortes.py'
)
foreach ($f in $archivos) {
    $origen = Join-Path $RepoDir ('app\' + $f)
    if (Test-Path $origen) { Copy-Item $origen $SubsDir -Force }
}
Bien "Aplicacion copiada a $SubsDir"

$ff = Asegurar-FFmpeg
$ffOk = $ff
if ($ff) {
    Bien 'FFmpeg disponible en el sistema'
} else {
    Mal 'FFmpeg no esta instalado. Esta herramienta lo necesita para extraer el audio.'
    Detalle 'Instalalo con:  winget install --id Gyan.FFmpeg -e'
    Detalle 'Despues volve a ejecutar INSTALAR.bat.'
}

Paso '6/6  DaVinci Resolve'
$resolveOk = (Test-Path $ResolveExe) -and (Test-Path (Join-Path $ResolveModules 'DaVinciResolveScript.py'))
if ($resolveOk) {
    Bien 'DaVinci Resolve y su API de scripting estan presentes'
    try {
        $check = & $VenvPy -c "import sys; sys.path.insert(0, r'$ResolveModules'); import DaVinciResolveScript; print('import-ok')" 2>$null
        if ("$check" -match 'import-ok') { Bien 'El modulo DaVinciResolveScript se importa correctamente' }
        else { Aviso 'No se pudo importar DaVinciResolveScript (?Resolve instalado en otra ruta?)' }
    } catch {
        Aviso "No se pudo verificar DaVinciResolveScript: $($_.Exception.Message)"
    }
} else {
    Aviso 'No encontre DaVinci Resolve con la API de scripting habilitada.'
    Detalle 'La app necesita Resolve abierto con un proyecto y una timeline.'
    Detalle 'En Resolve: Preferencias -> Sistema -> General -> "External scripting using" = Local.'
}

if (-not $SinResolve) {
    if (Test-Path $ResolveScripts) {
        try {
            Copy-Item (Join-Path $RepoDir 'installer\Indie Now - Subtitulos.py') $ResolveScripts -Force
            Bien 'Lanzador agregado al menu Scripts -> Utility de Resolve'
            Detalle 'Reinicia Resolve para que aparezca: Workspace -> Scripts -> Utility'
        } catch {
            Aviso "No se pudo copiar el lanzador a la carpeta de scripts de Resolve: $($_.Exception.Message)"
        }
    } else {
        Aviso 'Todavia no existe la carpeta de scripts de Resolve (?nunca abriste Resolve en este usuario?).'
        Detalle "Despues de abrirlo una vez, copia 'Indie Now - Subtitulos.py' a:"
        Detalle $ResolveScripts
    }
}

if (-not $SinAccesoDirecto) {
    $pythonw = $VenvPyW
    if (-not (Test-Path $pythonw)) { $pythonw = $VenvPy }
    $appPy = Join-Path $SubsDir 'subtitulos.py'
    $escritorio = [Environment]::GetFolderPath('Desktop')
    $inicio = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    Nuevo-Acceso -Ruta (Join-Path $escritorio 'Indie Now Subtitulos.lnk') -Destino $pythonw `
        -Argumentos "`"$appPy`"" -Carpeta $SubsDir -Descripcion 'Indie Now - Subtitulos' | Out-Null
    Nuevo-Acceso -Ruta (Join-Path $inicio 'Indie Now Subtitulos.lnk') -Destino $pythonw `
        -Argumentos "`"$appPy`"" -Carpeta $SubsDir -Descripcion 'Indie Now - Subtitulos' | Out-Null
    Bien 'Accesos directos creados (Escritorio y Menu Inicio)'
}

Write-Host ''
Write-Host '  =========================================================' -ForegroundColor DarkGray
Write-Host '   LISTO' -ForegroundColor Green
Write-Host ''
Write-Host '   Como usarla:' -ForegroundColor White
Write-Host '     - Desde Resolve:  Workspace -> Scripts -> Utility -> Indie Now - Subtitulos' -ForegroundColor Gray
Write-Host '     - O con el acceso directo "Indie Now Subtitulos"' -ForegroundColor Gray
Write-Host ''
Write-Host '   Necesita Resolve abierto, con un proyecto y una timeline.' -ForegroundColor Gray
Write-Host '   La primera transcripcion descarga el modelo de Whisper (~500 MB).' -ForegroundColor Gray
if (-not $ffOk) {
    Write-Host ''
    Write-Host '   ATENCION: falta FFmpeg, la app no va a poder procesar audio.' -ForegroundColor Red
}
Write-Host '  =========================================================' -ForegroundColor DarkGray

if (-not $SinAbrir -and $ffOk) {
    Write-Host ''
    Write-Host '   Abriendo Indie Now - Subtitulos...' -ForegroundColor DarkGray
    Start-Process -FilePath $VenvPyW -ArgumentList @((Join-Path $SubsDir 'subtitulos.py')) -WorkingDirectory $SubsDir
}

if (-not $ffOk) { exit 1 }
exit 0
