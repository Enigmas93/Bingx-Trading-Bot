@echo off
rem Inicia o bot completo: backend (motor + monitor), painel local e tunel ngrok (painel da Vercel).
cd /d "%~dp0"
start "BingX Bot - backend" cmd /k iniciar_backend.bat
timeout /t 8 /nobreak >nul
start "BingX Bot - painel" cmd /k iniciar_painel.bat
start "BingX Bot - tunel" cmd /k iniciar_tunel.bat
