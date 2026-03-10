@echo off
SETLOCAL EnableDelayedExpansion

:: setup.bat - Bootstrap installer for KegLevel Pico (Windows)
:: Can be run from anywhere (e.g. after: curl -sL ... -o setup.bat ^&^& setup.bat)
:: Clones repo to %USERPROFILE%\keglevel_pico and runs install.bat

SET "INSTALL_DIR=%USERPROFILE%\keglevel_pico"
SET "DATA_DIR=%USERPROFILE%\keglevel_pico-data"
SET "SHORTCUT_PATH=%USERPROFILE%\Desktop\KegLevel Pico.lnk"
SET "REPO_URL=https://github.com/keglevelmonitor/keglevelpico.git"

TITLE KegLevel Pico Auto-Installer

echo.
echo ========================================
echo    KegLevel Pico Windows Installer
echo ========================================
echo.

:: 1. Check if Git and Python are installed
where git >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git is not installed. Please install Git for Windows:
    echo         https://git-scm.com/download/win
    pause
    exit /b 1
)
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed. Please install Python from python.org
    echo         and check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: 2. Check for existing install - show menu if found
SET "WHAT_TO_INSTALL=KegLevel Pico Application and Data Directory"
SET "CLEANUP_MODE=NONE"
IF EXIST "%INSTALL_DIR%" goto :menu
IF EXIST "%DATA_DIR%" goto :menu
goto :confirm_proceed

:menu
echo Existing installation detected:
IF EXIST "%INSTALL_DIR%" echo  - App: %INSTALL_DIR%
IF EXIST "%DATA_DIR%" echo  - Data: %DATA_DIR%
echo.
echo How would you like to proceed? (Case Sensitive)
echo   UPDATE    - Update the App (Git Pull) and re-run install (Keeps data)
echo   APP       - Reinstall App only (Deletes App folder, Keeps data)
echo   ALL       - Reinstall App AND reset data (Fresh Install)
echo   UNINSTALL - Uninstall the app and the data directory
echo   EXIT      - Cancel
echo.
set /p "choice=Enter selection: "

IF /i "%choice%"=="UPDATE" (
    SET "WHAT_TO_INSTALL=KegLevel Pico Update"
    SET "CLEANUP_MODE=NONE"
    goto :confirm_proceed
)
IF /i "%choice%"=="APP" (
    SET "WHAT_TO_INSTALL=KegLevel Pico Application (Fresh App, Keep Data)"
    SET "CLEANUP_MODE=APP"
    goto :confirm_proceed
)
IF /i "%choice%"=="ALL" (
    SET "WHAT_TO_INSTALL=KegLevel Pico Application and Data Directory (Fresh Install)"
    SET "CLEANUP_MODE=ALL"
    goto :confirm_proceed
)
IF /i "%choice%"=="UNINSTALL" goto :do_uninstall
IF /i "%choice%"=="EXIT" (
    echo Cancelled.
    pause
    exit /b 0
)
echo Invalid selection.
goto :menu

:confirm_proceed
echo.
echo ------------------------------------------------------------
echo Processing: %WHAT_TO_INSTALL%
echo ------------------------------------------------------------
echo.
set /p "confirm=Press Y to proceed, or any other key to cancel: "
IF /i not "%confirm%"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)
IF "%CLEANUP_MODE%"=="APP" (
    echo Removing existing application...
    IF EXIST "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
)
IF "%CLEANUP_MODE%"=="ALL" (
    echo Removing application and data...
    IF EXIST "%INSTALL_DIR%" rmdir /s /q "%INSTALL_DIR%"
    IF EXIST "%DATA_DIR%" rmdir /s /q "%DATA_DIR%"
)
goto :clone

:do_uninstall
echo.
echo ------------------------------------------
echo YOU ARE ABOUT TO DELETE:
echo The KegLevel Pico application AND all user data/settings.
echo ------------------------------------------
echo.
set /p "confirm=Type YES to UNINSTALL, or any other key to return: "
IF /i not "%confirm%"=="YES" (
    echo Uninstallation aborted.
    goto :menu
)
echo.
echo Removing files...
IF EXIST "%SHORTCUT_PATH%" (
    del "%SHORTCUT_PATH%"
    echo  - Removed desktop shortcut
)
IF EXIST "%INSTALL_DIR%" (
    rmdir /s /q "%INSTALL_DIR%"
    echo  - Removed application directory
)
IF EXIST "%DATA_DIR%" (
    rmdir /s /q "%DATA_DIR%"
    echo  - Removed data directory
)
echo.
echo ==========================================
echo    Uninstallation Complete
echo ==========================================
pause
exit /b 0

:clone
:: 3. Clone or update repository
IF EXIST "%INSTALL_DIR%" (
    echo.
    echo Updating code...
    cd /d "%INSTALL_DIR%"
    git pull --rebase
) ELSE (
    echo.
    echo Cloning repository to %INSTALL_DIR%...
    git clone %REPO_URL% "%INSTALL_DIR%"
    cd /d "%INSTALL_DIR%"
)

IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git operation failed.
    pause
    exit /b 1
)

:: 4. Run the main installer
echo.
echo Launching main installer...
call install.bat

echo.
echo ========================================
echo    Setup Complete!
echo ========================================
pause
