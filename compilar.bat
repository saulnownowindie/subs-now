@echo off
setlocal
cd /d "%~dp0"
title Compilar IndieNow-Subtitulos

set "VENV=%LOCALAPPDATA%\IndieDL\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo.
  echo   Falta el entorno virtual. Ejecuta INSTALAR.bat primero.
  echo.
  pause
  exit /b 1
)

echo Instalando PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if errorlevel 1 goto :error

echo Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist_anterior rmdir /s /q dist_anterior
if exist dist move dist dist_anterior

echo Compilando...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm IndieNow-Subtitulos.spec
if errorlevel 1 goto :error

echo.
echo   Listo: dist\IndieNow-Subtitulos\IndieNow-Subtitulos.exe
echo   (el build anterior quedo en dist_anterior, si existia)
echo.
pause
exit /b 0

:error
echo.
echo   Fallo la compilacion. Revisa el mensaje de arriba.
echo.
pause
exit /b 1
