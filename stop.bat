@echo off
setlocal enabledelayedexpansion

echo ==========================================================
echo   Assistente do WhatsApp - Desligar
echo ==========================================================
echo.

set "DATA=%LOCALAPPDATA%\WhatsAppBotData"
set "LOCK=%DATA%\assistente.lock"
set "STOP=%DATA%\stop.request"

if not exist "%LOCK%" (
    echo O assistente nao esta rodando.
    echo.
    echo Para ligar, clique duas vezes em start.bat.
    echo.
    pause
    exit /b 0
)

rem O arquivo de trava guarda o numero do processo que esta rodando.
set "PID="
set /p PID=<"%LOCK%"
if not defined PID (
    echo Nao consegui ler o arquivo de controle. Removendo.
    del /q "%LOCK%" 2>nul
    echo Pode ligar normalmente com o start.bat.
    echo.
    pause
    exit /b 0
)

rem Confere que o processo existe E que e' um Python. Numeros de processo sao
rem reaproveitados pelo Windows: sem checar o nome, poderiamos encerrar outro
rem programa que herdou o mesmo numero.
call :esta_rodando !PID!
if errorlevel 1 (
    echo O assistente nao esta rodando ^(sobrou um arquivo de controle antigo^).
    del /q "%LOCK%" 2>nul
    del /q "%STOP%" 2>nul
    echo Limpei o arquivo. Pode ligar normalmente com o start.bat.
    echo.
    pause
    exit /b 0
)

echo Pedindo para o assistente encerrar com calma...
echo.
echo   Isso fecha o navegador do jeito certo e mantem sua sessao do WhatsApp.
echo   Se ele estiver enviando uma mensagem agora, espera ela terminar -
echo   pode levar ate um minuto. Nao feche esta janela.
echo.

rem Cria o pedido de parada. O programa ve esse arquivo em menos de um segundo e
rem sai sozinho. Um taskkill direto deixaria o navegador aberto por tras,
rem travando o perfil da sessao e impedindo o proximo start.bat de funcionar.
type nul > "%STOP%"

set "PAROU="
<nul set /p "=Aguardando"
for /l %%s in (1,1,60) do (
    if not defined PAROU (
        call :esta_rodando !PID!
        if errorlevel 1 (
            set "PAROU=1"
        ) else (
            <nul set /p "=."
            timeout /t 1 /nobreak >nul
        )
    )
)
echo.

if defined PAROU (
    echo.
    echo   Pronto! O assistente foi desligado.
    del /q "%STOP%" 2>nul
) else (
    echo.
    echo   Nao respondeu no tempo esperado. Encerrando a forca...
    rem /T leva junto os processos filhos - sem isso o navegador ficaria aberto.
    taskkill /F /T /PID !PID! >nul 2>&1
    if errorlevel 1 (
        echo   [AVISO] Nao consegui encerrar o processo !PID!.
        echo   Abra o Gerenciador de Tarefas ^(Ctrl+Shift+Esc^) e finalize pythonw.exe.
    ) else (
        echo   Assistente encerrado.
    )
    rem Depois de um encerramento a forca, o programa nao teve chance de limpar.
    del /q "%STOP%" 2>nul
    del /q "%LOCK%" 2>nul
)

echo.
echo Para ligar de novo, clique duas vezes em start.bat.
echo.
pause
exit /b 0

rem ---------------------------------------------------------------------------
rem Retorna errorlevel 0 se o processo %1 existe e e' um python/pythonw.
:esta_rodando
tasklist /FI "PID eq %~1" /NH 2>nul | find /i "python" >nul
exit /b %errorlevel%
