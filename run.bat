@echo off
setlocal

REM Always run from this script's directory.
cd /d "%~dp0"

REM Conda env name (edit this line only when needed).
set "CONDA_ENV_NAME=py310"

REM Use project-local config dir.
set "OPENHARNESS_CONFIG_DIR=%CD%\build\wksp"
REM Source settings file name under OPENHARNESS_CONFIG_DIR (copied to settings.json before launch).
set "OPENHARNESS_SETTINGS_SOURCE_FILE=settings_debug.json"
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
if not exist "%OPENHARNESS_CONFIG_DIR%\%OPENHARNESS_SETTINGS_SOURCE_FILE%" (
  echo [ERROR] Missing "%OPENHARNESS_CONFIG_DIR%\%OPENHARNESS_SETTINGS_SOURCE_FILE%"
  echo Please create it first or copy from template.
  exit /b 1
)
copy /Y "%OPENHARNESS_CONFIG_DIR%\%OPENHARNESS_SETTINGS_SOURCE_FILE%" "%OPENHARNESS_CONFIG_DIR%\settings.json" >nul

REM Require conda env. Exit immediately on failure.
where conda >nul 2>nul
if %errorlevel%==0 (
  call conda activate %CONDA_ENV_NAME% >nul 2>nul
  if not %errorlevel%==0 (
    echo [ERROR] Failed to activate conda env "%CONDA_ENV_NAME%".
    exit /b 1
  )
) else (
  echo [ERROR] conda is not available in PATH.
  exit /b 1
)

REM Launch OpenHarness. "oh" may conflict in PowerShell, but this is cmd/bat.
where oh >nul 2>nul
if %errorlevel%==0 (
  oh %*
  exit /b %errorlevel%
)

REM Fallback to module execution.
python -m openharness %*
exit /b %errorlevel%
