@echo off
SETLOCAL

:: update.bat - KegLevel Lite update (run from project directory)
:: Supports --check to only check for updates (no install)

SET "PROJECT_DIR=%~dp0"
IF %PROJECT_DIR:~-1%==\ SET "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
SET "VENV_PYTHON=%PROJECT_DIR%\venv\Scripts\python.exe"
SET "MODE=%~1"

cd /d "%PROJECT_DIR%"

echo --- KegLevel Lite Update ---
echo Project: %PROJECT_DIR%

:: Check for Git
IF NOT EXIST "%PROJECT_DIR%\.git" (
    echo [ERROR] Not a Git repository.
    exit /b 1
)

:: Detect branch
FOR /F "tokens=*" %%i IN ('git rev-parse --abbrev-ref HEAD 2^>nul') DO SET "BRANCH=%%i"
echo Branch: %BRANCH%
echo.

:: Fetch and compare
echo Fetching latest meta-data...
git fetch origin %BRANCH% 2>nul

FOR /F "tokens=*" %%i IN ('git rev-parse HEAD 2^>nul') DO SET "LOCAL=%%i"
FOR /F "tokens=*" %%i IN ('git rev-parse origin/%BRANCH% 2^>nul') DO SET "REMOTE=%%i"

IF "%LOCAL%"=="%REMOTE%" (
    echo Result: Up to date.
    exit /b 0
)

echo Result: Update Available!
echo Local:  %LOCAL:~0,7%
echo Remote: %REMOTE:~0,7%

:: If --check mode, stop here
IF "%MODE%"=="--check" (
    exit /b 0
)

echo.
echo --- Starting Install Process ---

:: 1. Git Pull
echo [1/2] Pulling latest code...
git pull --rebase origin %BRANCH%

IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git pull failed.
    exit /b 1
)

:: 2. Update Dependencies
echo.
echo [2/2] Updating dependencies...
IF NOT EXIST "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found. Please run install.bat first.
    exit /b 1
)

"%VENV_PYTHON%" -m pip install -r "%PROJECT_DIR%\requirements.txt"

IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Dependency update failed.
    exit /b 1
)

echo.
echo --- Update Complete! Please restart the app. ---
exit /b 0
