@echo off
setlocal
echo Installing Hermes autolearn cron job...
wsl -e bash __WSL_WORKSPACE__/hermes-agent-self-evolution/scripts/install-autolearn-cron.sh
echo.
echo Exit code: %errorlevel%
pause
