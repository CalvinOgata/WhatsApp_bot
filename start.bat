@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo O programa ainda nao foi instalado neste computador.
    echo Clique duas vezes em setup.bat primeiro.
    echo.
    pause
    exit /b 1
)

if not exist "config.json" (
    echo O arquivo config.json nao foi encontrado.
    echo Clique duas vezes em setup.bat primeiro.
    echo.
    pause
    exit /b 1
)

rem pythonw.exe roda sem deixar nenhuma janela preta aberta.
start "" ".venv\Scripts\pythonw.exe" "main.py" %*
exit /b 0
