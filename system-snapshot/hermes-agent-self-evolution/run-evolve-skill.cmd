@echo off
setlocal
set "SELF_EVOLUTION_ROOT=%~dp0"
if "%SELF_EVOLUTION_ROOT:~-1%"=="\" set "SELF_EVOLUTION_ROOT=%SELF_EVOLUTION_ROOT:~0,-1%"

set "HERMES_AGENT_REPO=__WINDOWS_WORKSPACE__\hermes-agent"
set "XDG_CACHE_HOME=%SELF_EVOLUTION_ROOT%\.cache"
set "HF_HOME=%SELF_EVOLUTION_ROOT%\.cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
set "TRANSFORMERS_CACHE=%HF_HOME%\transformers"
set "DSPY_CACHEDIR=%SELF_EVOLUTION_ROOT%\.cache\dspy"
set "TMP=%SELF_EVOLUTION_ROOT%\.cache\tmp"
set "TEMP=%TMP%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist "%SELF_EVOLUTION_ROOT%\local-env.cmd" call "%SELF_EVOLUTION_ROOT%\local-env.cmd"

if not exist "%XDG_CACHE_HOME%" mkdir "%XDG_CACHE_HOME%"
if not exist "%HF_HOME%" mkdir "%HF_HOME%"
if not exist "%HUGGINGFACE_HUB_CACHE%" mkdir "%HUGGINGFACE_HUB_CACHE%"
if not exist "%TRANSFORMERS_CACHE%" mkdir "%TRANSFORMERS_CACHE%"
if not exist "%DSPY_CACHEDIR%" mkdir "%DSPY_CACHEDIR%"
if not exist "%TMP%" mkdir "%TMP%"
if not exist "%SELF_EVOLUTION_ROOT%\output" mkdir "%SELF_EVOLUTION_ROOT%\output"

pushd "%SELF_EVOLUTION_ROOT%"
"%SELF_EVOLUTION_ROOT%\.venv\Scripts\python.exe" -m evolution.skills.evolve_skill %*
set "EXITCODE=%ERRORLEVEL%"
popd
exit /b %EXITCODE%
