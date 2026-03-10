#!/bin/bash
# update.sh
# Handles checking, pulling code, and dependency updates for KegLevel Pico.

# --- 1. Setup ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PYTHON_EXEC="$VENV_DIR/bin/python"
MODE=$1

# Detect Branch (main or master)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "--- KegLevel Pico Update Manager ---"
echo "Root: $PROJECT_DIR"
echo "Branch: $BRANCH"

# --- 2. Check for Git Sanity ---
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "[ERROR] Not a Git repository."
    exit 1
fi

# Ignore execute-bit changes (chmod +x) so git pull does not fail on install.sh/update.sh
git config --local core.fileMode false

# --- 3. FETCH & COMPARE (Common to Check and Install) ---
echo "Fetching latest meta-data..."
git fetch origin $BRANCH

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/$BRANCH)

if [ "$LOCAL" == "$REMOTE" ]; then
    echo "Result: Up to date."
    exit 0
else
    echo "Result: Update Available!"
    echo "Local:  ${LOCAL:0:7}"
    echo "Remote: ${REMOTE:0:7}"
    
    # If we are only checking, stop here
    if [ "$MODE" == "--check" ]; then
        exit 0
    fi
fi

# =========================================================
# INSTALLATION PHASE (Only runs if NOT in --check mode)
# =========================================================

echo "--- Starting Install Process ---"

# --- 4. Git Pull ---
echo "Pulling changes..."
if ! git pull --rebase origin $BRANCH; then
    echo "Resetting install.sh and update.sh (chmod changes) and retrying..."
    git checkout -- install.sh update.sh 2>/dev/null
    if ! git pull --rebase origin $BRANCH; then
        echo "[ERROR] git pull failed."
        exit 1
    fi
fi
chmod +x "$PROJECT_DIR/install.sh" "$PROJECT_DIR/update.sh" 2>/dev/null || true

# --- 5. System Dependencies (Kivy Specific) ---
echo "Checking system dependencies (sudo)..."
sudo apt-get install -y python3-dev python3-venv liblgpio-dev numlockx libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev

if [ $? -ne 0 ]; then
    echo "[WARNING] System dependency check had issues."
fi

# --- 6. Python Dependencies ---
echo "Updating Python environment..."

if [ ! -f "$VENV_PYTHON_EXEC" ]; then
    echo "[ERROR] Virtual environment missing."
    exit 1
fi

"$VENV_PYTHON_EXEC" -m pip install -r "$PROJECT_DIR/requirements.txt"

if [ $? -ne 0 ]; then
    echo "[FATAL ERROR] Pip install failed."
    exit 1
fi

# --- 7. Refresh App Icon (ensures taskbar icon works after update) ---
ICON_SOURCE="$PROJECT_DIR/src/assets/beer-keg.png"
SYSTEM_ICON_NAME="keglevel"
DESKTOP_FILE="$HOME/.local/share/applications/keglevel_pico.desktop"
if [ -f "$ICON_SOURCE" ]; then
    echo "Updating app icon..."
    sudo cp "$ICON_SOURCE" "/usr/share/icons/${SYSTEM_ICON_NAME}.png"
    sudo chmod 644 "/usr/share/icons/${SYSTEM_ICON_NAME}.png"
    sudo mkdir -p /usr/share/icons/hicolor/48x48/apps
    sudo cp "$ICON_SOURCE" "/usr/share/icons/hicolor/48x48/apps/${SYSTEM_ICON_NAME}.png"
    sudo chmod 644 "/usr/share/icons/hicolor/48x48/apps/${SYSTEM_ICON_NAME}.png"
    if command -v gtk-update-icon-cache &>/dev/null; then
        sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
    fi
    # Ensure desktop file uses Icon=keglevel (fixes taskbar on existing installs)
    if [ -f "$DESKTOP_FILE" ]; then
        sed -i "s|^Icon=.*|Icon=$SYSTEM_ICON_NAME|" "$DESKTOP_FILE"
        if ! grep -q "^StartupWMClass=" "$DESKTOP_FILE"; then
            echo "StartupWMClass=KegLevel Pico" >> "$DESKTOP_FILE"
        fi
    fi
fi

echo "--- Update Complete! Please Restart. ---"
exit 0
