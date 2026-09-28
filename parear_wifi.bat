@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Larinha Teleporter - Pareamento Wi-Fi
echo ============================================
echo.
echo Conecte o iPhone pelo cabo, desbloqueie a tela e confie no computador.
echo.

if not exist .venv (
  echo [ERRO] O ambiente ainda nao existe. Rode o iniciar.bat uma vez primeiro.
  pause
  exit /b 1
)

.venv\Scripts\python -m pymobiledevice3 lockdown wifi-connections on

echo.
echo Se apareceu OK acima, pode despluggar o cabo e usar o modo Wi-Fi no programa.
pause
endlocal
