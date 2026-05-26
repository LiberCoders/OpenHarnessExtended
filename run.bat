@echo off
setlocal

REM Always run from this script's directory.
cd /d "%~dp0"

REM Use project-local config dir.
set "OPENHARNESS_CONFIG_DIR=%CD%\records"
set "OPENHARNESS_DATA_DIR=%OPENHARNESS_CONFIG_DIR%\data"
set "OPENHARNESS_LOGS_DIR=%OPENHARNESS_CONFIG_DIR%\logs"

if not exist "%OPENHARNESS_CONFIG_DIR%" (
  mkdir "%OPENHARNESS_CONFIG_DIR%"
)
if not exist "%OPENHARNESS_DATA_DIR%" (
  mkdir "%OPENHARNESS_DATA_DIR%"
)
if not exist "%OPENHARNESS_LOGS_DIR%" (
  mkdir "%OPENHARNESS_LOGS_DIR%"
)

REM OpenHarness reads settings.json from OPENHARNESS_CONFIG_DIR.
if not exist "%CD%\settings.json" (
  echo [ERROR] Missing "%CD%\settings.json"
  echo Please create it first or copy from template.
  exit /b 1
)
copy /Y "%CD%\settings.json" "%OPENHARNESS_CONFIG_DIR%\settings.json" >nul

REM Launch OpenHarness. "oh" may conflict in PowerShell, but this is cmd/bat.
where oh >nul 2>nul
if %errorlevel%==0 (
  oh %*
  exit /b %errorlevel%
)

REM Fallback to module execution.
python -m openharness %*
exit /b %errorlevel%
