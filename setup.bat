@echo off
setlocal
cd /d "%~dp0"

echo ==========================================================
echo   Assistente do WhatsApp - Instalacao
echo   (nao precisa de senha de Administrador)
echo ==========================================================
echo.

set "PYEXE="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERRO] O Python nao foi encontrado neste computador.
    echo.
    echo   1. Baixe o Python em https://www.python.org/downloads/
    echo   2. Na primeira tela, MARQUE a opcao "Add python.exe to PATH"
    echo   3. Clique em "Install Now" ^(instala so para o seu usuario^)
    echo   4. Depois rode este setup.bat novamente
    echo.
    pause
    exit /b 1
)

echo [1 de 4] Criando o ambiente local ^(.venv^)...
if not exist ".venv\Scripts\python.exe" (
    %PYEXE% -m venv .venv
    if errorlevel 1 goto :erro
)

set "VENV_PY=.venv\Scripts\python.exe"

echo [2 de 4] Atualizando o pip...
"%VENV_PY%" -m pip install --upgrade pip --no-warn-script-location
if errorlevel 1 goto :erro

echo [3 de 4] Instalando as bibliotecas necessarias...
"%VENV_PY%" -m pip install -r requirements.txt --no-warn-script-location
if errorlevel 1 goto :erro

echo [4 de 4] Preparando o navegador de reserva...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [AVISO] Nao deu para baixar o navegador de reserva.
    echo         Isso normalmente nao e problema: o programa vai usar
    echo         o Microsoft Edge ou o Google Chrome ja instalado.
)

echo.
echo ==========================================================
echo   Instalacao concluida!
echo.
echo   Proximos passos:
echo     1. Abra o arquivo config.json ^(clique com o botao
echo        direito ^> Abrir com ^> Bloco de Notas^) e preencha
echo        seu nome, os horarios e os nomes das conversas.
echo     2. Clique duas vezes em start.bat.
echo     3. Na primeira vez, uma janela do navegador vai abrir
echo        com um QR code para escanear com o celular.
echo ==========================================================
echo.
pause
exit /b 0

:erro
echo.
echo [ERRO] A instalacao nao terminou. Tire uma foto desta janela
echo        para pedir ajuda a quem instalou o programa.
echo.
pause
exit /b 1
