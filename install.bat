@echo off
SETLOCAL EnableDelayedExpansion

:: install.bat - KegLevel Pico installation (run from project directory)

:: --- Variables ---
SET "PROJECT_DIR=%~dp0"
IF %PROJECT_DIR:~-1%==\ SET "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

SET "VENV_DIR=%PROJECT_DIR%\venv"
SET "DATA_DIR=%USERPROFILE%\keglevel_pico-data"
SET "SHORTCUT_PATH=%USERPROFILE%\Desktop\KegLevel Pico.lnk"
SET "ICON_PATH=%PROJECT_DIR%\src\assets\beer-keg.ico"
SET "SCRIPT_PATH=%PROJECT_DIR%\src\main_kivy.py"

echo.
echo ==========================================
echo    KegLevel Pico Installer
echo ==========================================
echo Project: %PROJECT_DIR%
echo.

:: --- Step 1: Data Directory ---
echo --- [Step 1/3] Configuring Data Directory ---
IF NOT EXIST "%DATA_DIR%" (
    mkdir "%DATA_DIR%"
    echo Created data directory: %DATA_DIR%
) ELSE (
    echo Data directory exists.
)

:: --- Step 2: Python Environment ---
echo.
echo --- [Step 2/3] Setting up Python Environment ---
IF EXIST "%VENV_DIR%" (
    echo Virtual environment exists. Skipping creation.
) ELSE (
    echo Creating virtual environment...
    python -m venv "%VENV_DIR%"
)

IF %ERRORLEVEL% NEQ 0 (
    echo [FATAL ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%PROJECT_DIR%\requirements.txt"

IF %ERRORLEVEL% NEQ 0 (
    echo [FATAL ERROR] Dependency installation failed.
    pause
    exit /b 1
)

:: --- Step 3: Desktop Shortcut ---
echo.
echo --- [Step 3/3] Creating Desktop Shortcut ---
SET "SHORTCUT_ICON=%ICON_PATH%"

SET "TARGET=%VENV_DIR%\Scripts\pythonw.exe"
SET "ARGS=\"%SCRIPT_PATH%\""

powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT_PATH%');$s.TargetPath='%TARGET%';$s.Arguments='%ARGS%';$s.WorkingDirectory='%PROJECT_DIR%\src';$s.IconLocation='!SHORTCUT_ICON!';$s.Save()"

echo Shortcut created on Desktop: KegLevel Pico.lnk

echo.
echo ==========================================
echo    Installation complete!
echo ==========================================
echo.
echo Run KegLevel Pico from the Desktop shortcut or:
echo   %VENV_DIR%\Scripts\pythonw.exe "%SCRIPT_PATH%"
echo.
pause
