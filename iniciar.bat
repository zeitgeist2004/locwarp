@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   LocWarp-Simples - Localizacao Facil
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado. Instale de https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist .venv (
  echo Criando ambiente virtual...
  python -m venv .venv
)

echo Instalando/atualizando dependencias...
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\pip install -r requisitos.txt -q

if /i "%~1"=="--sem-navegador" goto :servidor

echo.
echo Abrindo http://127.0.0.1:8777 no navegador, aguarde 3 segundos...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8777"

:servidor
echo.
echo Servidor rodando. Feche esta janela para parar.
.venv\Scripts\python servidor.py

endlocal
