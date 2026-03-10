#!/bin/bash
# install.sh
# Installation script for KegLevel Pico application.

# Stop on any error to prevent broken installs
set -e

echo "=========================================="
echo "   KegLevel Pico Installer"
echo "=========================================="

# --- 1. Define Variables ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_EXEC="python3"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PYTHON_EXEC="$VENV_DIR/bin/python"

# Desktop Entry Paths
DESKTOP_FILE_TEMPLATE="$PROJECT_DIR/keglevel_pico.desktop"
INSTALL_LOCATION="$HOME/.local/share/applications/keglevel_pico.desktop"
DATA_DIR="$HOME/keglevel_pico-data"
# Temp file for modification
TEMP_DESKTOP_FILE="/tmp/keglevel_pico_temp.desktop"

echo "Project path: $PROJECT_DIR"

# --- 2. Install System Dependencies (Requires Sudo) ---
echo ""
echo "--- [Step 1/5] Checking System Dependencies ---"
echo "You may be asked for your password to install system packages."

# Install Kivy Dependencies (SDL2), Build Tools, and GPIO
sudo apt-get update
sudo apt-get install -y python3-dev python3-venv liblgpio-dev numlockx libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev

# --- 3. Setup Python Environment (Clean Install) ---
echo ""
echo "--- [Step 2/5] Setting up Virtual Environment ---"

# CLEANUP: Delete existing venv to ensure a clean slate
if [ -d "$VENV_DIR" ]; then
    echo "Removing old virtual environment for a clean install..."
    rm -rf "$VENV_DIR"
fi

echo "Creating new Python virtual environment at $VENV_DIR..."
$PYTHON_EXEC -m venv "$VENV_DIR"

if [ $? -ne 0 ]; then
    echo "[FATAL ERROR] Failed to create virtual environment."
    exit 1
fi

# --- 4. Install Python Libraries ---
echo ""
echo "--- [Step 3/5] Installing Python Libraries ---"

# Upgrade pip inside the venv
"$VENV_PYTHON_EXEC" -m pip install --upgrade pip

# Install dependencies using the pip INSIDE the virtual environment
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "Installing from requirements.txt..."
    "$VENV_PYTHON_EXEC" -m pip install -r "$PROJECT_DIR/requirements.txt"
else
    echo "WARNING: requirements.txt not found. Installing default shim (rpi-lgpio)..."
    "$VENV_PYTHON_EXEC" -m pip install rpi-lgpio
fi

if [ $? -ne 0 ]; then
    echo "[FATAL ERROR] Dependency installation failed."
    exit 1
fi

# --- 5. Create User Data Directory ---
echo ""
echo "--- [Step 4/5] Configuring Data Directory ---"
if [ ! -d "$DATA_DIR" ]; then
    echo "Creating user data directory: $DATA_DIR"
    mkdir -p "$DATA_DIR"
    chmod 700 "$DATA_DIR"
else
    echo "Data directory already exists ($DATA_DIR). Skipping."
fi

# --- 6. Install App Icon for Taskbar (Raspberry Pi) ---
# Full paths in .desktop Icon= work for the launcher but often fail for the taskbar.
# Installing to /usr/share/icons/ and using short name ensures the icon shows everywhere.
ICON_SOURCE="$PROJECT_DIR/src/assets/beer-keg.png"
SYSTEM_ICON_NAME="keglevel"
if [ -f "$ICON_SOURCE" ]; then
    echo ""
    echo "Installing app icon for taskbar..."
    sudo cp "$ICON_SOURCE" "/usr/share/icons/${SYSTEM_ICON_NAME}.png"
    sudo chmod 644 "/usr/share/icons/${SYSTEM_ICON_NAME}.png"
    sudo mkdir -p /usr/share/icons/hicolor/48x48/apps
    sudo cp "$ICON_SOURCE" "/usr/share/icons/hicolor/48x48/apps/${SYSTEM_ICON_NAME}.png"
    sudo chmod 644 "/usr/share/icons/hicolor/48x48/apps/${SYSTEM_ICON_NAME}.png"
    if command -v gtk-update-icon-cache &>/dev/null; then
        sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
    fi
    echo "Icon installed."
else
    echo "[WARNING] beer-keg.png not found at $ICON_SOURCE - taskbar may show default icon."
fi

# --- 7. Install Desktop Shortcut ---
echo ""
echo "--- [Step 5/5] Installing Desktop Shortcut ---"

if [ -f "$DESKTOP_FILE_TEMPLATE" ]; then
    # 7a. Prepare paths
    EXEC_CMD="$VENV_PYTHON_EXEC $PROJECT_DIR/src/main_kivy.py"
    
    # 7b. Create the modified file in /tmp
    cp "$DESKTOP_FILE_TEMPLATE" "$TEMP_DESKTOP_FILE"
    
    # 7c. Inject correct paths (Icon=keglevel stays - system icon for taskbar compatibility)
    sed -i "s|Exec=PLACEHOLDER_EXEC_PATH|Exec=$EXEC_CMD|g" "$TEMP_DESKTOP_FILE"
    sed -i "s|Path=PLACEHOLDER_PATH|Path=$PROJECT_DIR/src|g" "$TEMP_DESKTOP_FILE"
    
    # 7d. Install to Application Menu (System Menu)
    mkdir -p "$HOME/.local/share/applications"
    mv "$TEMP_DESKTOP_FILE" "$INSTALL_LOCATION"
    chmod +x "$INSTALL_LOCATION"
    echo "Shortcut installed to Application Menu: $INSTALL_LOCATION"

else
    echo "[WARNING] keglevel_pico.desktop template not found. Skipping shortcut."
fi

echo ""
echo "================================================="
echo ""
echo "Installation complete!"
echo ""
echo "At the Applications menu:"
echo "   select Other, KegLevel Pico to run the app."
echo ""
echo "================================================="
echo ""

read -p "Enter Y to launch the app, or any other key to exit: " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Launching KegLevel Pico..."
    # Launch in background, detached from terminal
    nohup "$VENV_PYTHON_EXEC" "$PROJECT_DIR/src/main_kivy.py" >/dev/null 2>&1 &
    disown
    
    # Attempt to close the terminal window/session
    kill -HUP $PPID
    exit 0
else
    echo "Exiting installer."
    exit 0
fi
