@echo off
setlocal
cd /d "%~dp0"
title Indie Now Subtitulos - Instalador

where powershell >nul 2>&1
if errorlevel 1 (
  echo.
  echo No se encontro PowerShell en este equipo.
  echo Windows 10/11 lo trae de fabrica; si falta, instala Windows Management Framework.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\instalar.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   La instalacion termino con advertencias o errores ^(codigo %RC%^).
  echo   Lee el detalle de arriba.
) else (
  echo   Instalacion completada.
)
echo.
pause
exit /b %RC%
