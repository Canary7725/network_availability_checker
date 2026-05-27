@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "ENVIRONMENT=%~1"
if "%ENVIRONMENT%"=="" set "ENVIRONMENT=uat"

if /I not "%ENVIRONMENT%"=="uat" if /I not "%ENVIRONMENT%"=="dev" (
  echo Invalid environment: "%ENVIRONMENT%"
  echo Usage: run.bat [uat^|dev]
  exit /b 1
)

if not exist "config.json" (
  echo Missing required file: config.json
  exit /b 1
)

if not exist "network_list.csv" (
  echo Missing required source file: network_list.csv
  exit /b 1
)

if not exist "requirements.txt" (
  echo Missing required file: requirements.txt
  exit /b 1
)

where py >nul 2>&1
if %errorlevel% equ 0 (
  set "PYTHON=py -3"
) else (
  where python >nul 2>&1
  if %errorlevel% neq 0 (
    echo Python 3 not found. Install Python and retry.
    exit /b 1
  )
  set "PYTHON=python"
)

if not exist "venv\" (
  echo Creating virtual environment in "venv"...
  %PYTHON% -m venv venv
  if %errorlevel% neq 0 exit /b %errorlevel%
)

call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
  echo Failed to activate virtual environment.
  exit /b %errorlevel%
)

echo Installing/updating dependencies...
python -m pip install --upgrade pip
if %errorlevel% neq 0 exit /b %errorlevel%
pip install -r requirements.txt
if %errorlevel% neq 0 exit /b %errorlevel%

echo Running reports for environment: %ENVIRONMENT%
if /I "%ENVIRONMENT%"=="uat" (
  python main.py --uat
) else (
  python main.py --dev
)
if %errorlevel% neq 0 exit /b %errorlevel%

echo Run completed successfully.
exit /b 0
