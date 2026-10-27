@echo off
setlocal
set "APP=%LOCALAPPDATA%\IndieDL"

if not exist "%APP%\venv\Scripts\pythonw.exe" goto :falta
if not exist "%APP%\subs\subtitulos.py" goto :falta

start "" "%APP%\venv\Scripts\pythonw.exe" "%APP%\subs\subtitulos.py" %*
exit /b 0

:falta
echo.
echo   Indie Now Subtitulos no esta instalado en este equipo.
echo   Ejecuta INSTALAR.bat (una sola vez) y volve a probar.
echo.
pause
exit /b 1
