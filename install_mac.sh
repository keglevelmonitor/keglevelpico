#!/bin/bash
# install_mac.sh
# Installation script for KegLevel Lite on macOS.

# Stop on any error to prevent broken installs
set -e

echo "=========================================="
echo "   KegLevel Lite Installer (macOS)"
echo "=========================================="

# --- 1. Define Variables ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PYTHON_EXEC="$VENV_DIR/bin/python"
DATA_DIR="$HOME/keglevel_lite-data"

# macOS app launcher paths
APPS_DIR="$HOME/Applications"
LAUNCHER_APP="$APPS_DIR/KegLevel Lite.app"
LAUNCHER_MACOS="$LAUNCHER_APP/Contents/MacOS"
LAUNCHER_EXEC="$LAUNCHER_MACOS/KegLevel Lite"

echo "Project path: $PROJECT_DIR"

# --- 2. Check Python ---
echo ""
echo "--- [Step 1/5] Checking Python ---"

PYTHON_EXEC=""

# Check for an existing compatible python3 (3.8 or later)
if command -v python3 &>/dev/null; then
    PY_OK=$(python3 -c "import sys; print('ok' if sys.version_info >= (3, 8) else 'bad')" 2>/dev/null)
    if [ "$PY_OK" == "ok" ]; then
        PYTHON_EXEC="python3"
        PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        echo "Found Python $PY_VERSION at: $(which python3)"
    fi
fi

# If no compatible Python found, try Homebrew
if [ -z "$PYTHON_EXEC" ]; then
    echo "Python 3.8+ not found. Checking for Homebrew..."
    if command -v brew &>/dev/null; then
        echo "Homebrew found. Installing Python..."
        brew install python
        if command -v python3 &>/dev/null; then
            PYTHON_EXEC="python3"
            echo "Python installed successfully."
        fi
    else
        echo ""
        echo "[ERROR] Python 3.8+ is required and was not found."
        echo ""
        echo "Please install Python using one of these options, then re-run this installer:"
        echo ""
        echo "  Option 1 - Homebrew (recommended):"
        echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "    brew install python"
        echo ""
        echo "  Option 2 - python.org installer:"
        echo "    https://www.python.org/downloads/macos/"
        echo ""
        exit 1
    fi
fi

if [ -z "$PYTHON_EXEC" ]; then
    echo "[FATAL ERROR] Python installation failed."
    exit 1
fi

# --- 3. Setup Python Virtual Environment ---
echo ""
echo "--- [Step 2/5] Setting up Virtual Environment ---"

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

"$VENV_PYTHON_EXEC" -m pip install --upgrade pip

if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "Installing from requirements.txt..."
    "$VENV_PYTHON_EXEC" -m pip install -r "$PROJECT_DIR/requirements.txt"
else
    echo "[FATAL ERROR] requirements.txt not found at $PROJECT_DIR."
    exit 1
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

# --- 6. Create macOS App Launcher ---
echo ""
echo "--- [Step 5/5] Creating App Launcher ---"

# Create ~/Applications if it does not exist (standard macOS per-user location)
mkdir -p "$LAUNCHER_MACOS"

# Write the executable shell script inside the bundle
cat > "$LAUNCHER_EXEC" << APPSCRIPT
#!/bin/bash
# KegLevel Lite macOS launcher
"$VENV_PYTHON_EXEC" "$PROJECT_DIR/src/main_kivy.py"
APPSCRIPT

chmod +x "$LAUNCHER_EXEC"

# Write the Info.plist
cat > "$LAUNCHER_APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>KegLevel Lite</string>
    <key>CFBundleDisplayName</key>
    <string>KegLevel Lite</string>
    <key>CFBundleIdentifier</key>
    <string>com.keglevelmonitor.keglevel-lite</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>KegLevel Lite</string>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

echo "App launcher created: $LAUNCHER_APP"

echo ""
echo "================================================="
echo ""
echo "Installation complete!"
echo ""
echo "To launch KegLevel Lite:"
echo "   Open Finder → Go → Home → Applications"
echo "   Double-click 'KegLevel Lite'"
echo ""
echo "Or run from Terminal:"
echo "   open \"$LAUNCHER_APP\""
echo ""
echo "================================================="
echo ""

read -p "Enter Y to launch the app now, or any other key to exit: " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Launching KegLevel Lite..."
    open "$LAUNCHER_APP"

    # Give the app a moment to start, then close this Terminal window
    sleep 2
    osascript -e 'tell application "Terminal" to close front window' 2>/dev/null || true
    exit 0
else
    echo "Exiting installer."
    exit 0
fi
