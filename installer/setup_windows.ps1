param(
    [switch]$SkipModelDownload
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $env:LOCALAPPDATA 'IndieDL\subs'
$VenvPython = Join-Path $AppDir '.venv\Scripts\python.exe'
$ResolveScripts = Join-Path $env:APPDATA 'Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Edit'

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Yellow }
function Find-Python312 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $detected = & $py.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { return ($detected | Select-Object -Last 1).Trim() }
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    return $null
}

if (-not $env:LOCALAPPDATA) { throw 'No se encontró LOCALAPPDATA para esta cuenta de Windows.' }
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

Write-Step 'Instalando los scripts de Indie Now'
$sourceFiles = Join-Path $ProjectRoot 'app'
Get-ChildItem -LiteralPath $sourceFiles -File | Where-Object { $_.Name -ne 'subtitulos_config.json.example' } |
    Copy-Item -Destination $AppDir -Force
$configPath = Join-Path $AppDir 'subtitulos_config.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item (Join-Path $sourceFiles 'subtitulos_config.json.example') $configPath
}

Write-Step 'Comprobando Python 3.12'
$python = Find-Python312
if (-not $python) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'Falta Python 3.12 y no está disponible winget para instalarlo automáticamente.' }
    & $winget.Source install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo instalar Python 3.12 con winget.' }
    $python = Find-Python312
}
if (-not $python) { throw 'Python 3.12 se instaló, pero no pude localizar python.exe. Cerrá y volvé a abrir PowerShell y ejecutá el instalador de nuevo.' }
Write-Host "Python: $python"

Write-Step 'Creando el entorno virtual e instalando Whisper y dependencias'
if (-not (Test-Path -LiteralPath $VenvPython)) { & $python -m venv (Join-Path $AppDir '.venv') }
if ($LASTEXITCODE -ne 0) { throw 'No pude crear el entorno virtual.' }
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'No pude actualizar pip.' }
& $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de las dependencias Python.' }

Write-Step 'Comprobando FFmpeg'
if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
    -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'FFmpeg no está instalado y winget no está disponible. Instalá FFmpeg y ejecutá el instalador otra vez.' }
    & $winget.Source install --id Gyan.FFmpeg.Shared --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw 'winget no pudo instalar FFmpeg.' }
    $links = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    if (Test-Path -LiteralPath $links) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($userPath -split ';') -notcontains $links) {
            [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $links).Trim(';')), 'User')
        }
        $env:Path = "$links;$env:Path"
    }
    if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue)) {
        Write-Warning 'FFmpeg quedó instalado; reiniciá Resolve para que herede el PATH actualizado.'
    }
}

Write-Step 'Detectando GPU NVIDIA y preparando CUDA cuando corresponda'
$nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if ($nvidia) { $nvidiaExe = $nvidia.Source }
else {
    $nvidiaCandidates = @(
        (Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'),
        (Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe')
    )
    $nvidiaExe = $nvidiaCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($nvidiaExe) {
    $gpuName = (& $nvidiaExe --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
} else { $gpuName = $null }
if ($gpuName) {
    Write-Host "GPU detectada: $gpuName" -ForegroundColor Green
    try {
        # faster-whisper documenta este paquete para Windows; se selecciona CUDA 12 + cuDNN 9.
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/Purfview/whisper-standalone-win/releases/tags/libs' -Headers @{ 'User-Agent' = 'IndieNow-Subtitle-Installer' }
        $asset = $release.assets | Where-Object { $_.name -match '12\.8\.4\.1' -and $_.name -match '9\.8\.0\.87' } | Select-Object -First 1
        if (-not $asset) { throw 'La publicación de bibliotecas CUDA no contiene el paquete CUDA 12 / cuDNN 9 esperado.' }
        $archive = Join-Path $env:TEMP $asset.name
        $cudaDir = Join-Path $AppDir 'cuda-runtime'
        New-Item -ItemType Directory -Force -Path $cudaDir | Out-Null
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
        & tar.exe -xf $archive -C $cudaDir
        if ($LASTEXITCODE -ne 0) { throw 'Windows no pudo extraer el archivo de bibliotecas CUDA.' }
        Remove-Item -LiteralPath $archive -Force
        $dlls = Get-ChildItem -LiteralPath $cudaDir -Filter '*.dll' -File -Recurse
        if (-not ($dlls | Where-Object Name -Like 'cublas64_12.dll')) { throw 'No apareció cublas64_12.dll después de extraer CUDA.' }
        if (-not ($dlls | Where-Object Name -Like 'cudnn*.dll')) { throw 'No aparecieron DLL de cuDNN después de extraer CUDA.' }
        Write-Host 'CUDA/cuBLAS/cuDNN quedaron junto a la aplicación; el programa probará la GPU y recurrirá a CPU si falla.' -ForegroundColor Green
    } catch {
        Write-Warning "No pude preparar el runtime CUDA automáticamente: $($_.Exception.Message)"
        Write-Warning 'La transcripción seguirá disponible con CPU; revisá el diagnóstico si querés activar CUDA.'
    }
} else {
    Write-Host 'No se detectó NVIDIA. La instalación queda en modo CPU; Whisper seguirá funcionando.' -ForegroundColor Cyan
}

if (-not $SkipModelDownload) {
    Write-Step 'Descargando el modelo Whisper large-v3-turbo para que esté listo en el primer uso'
    & $VenvPython -c 'from faster_whisper.utils import download_model; print(download_model("large-v3-turbo"))'
    if ($LASTEXITCODE -ne 0) { Write-Warning 'No pude precargar el modelo. Se descargará automáticamente en la primera transcripción.' }
}

Write-Step 'Registrando el lanzador en DaVinci Resolve'
New-Item -ItemType Directory -Force -Path $ResolveScripts | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'SubsSaul.py') (Join-Path $ResolveScripts 'SubsSaul.py') -Force

Write-Host "`nInstalación completa.`nReiniciá Resolve y elegí Workspace > Scripts > Edit > SubsSaul." -ForegroundColor Green
Write-Host "Aplicación: $AppDir"
