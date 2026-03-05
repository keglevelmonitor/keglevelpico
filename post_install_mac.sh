#!/bin/bash
# post_install_mac.sh
# Bundled inside KegLevelLite_Setup.pkg by build_installer_mac.sh.
# Runs as root after the PKG wizard completes on the end user's Mac.
# Clones the KegLevel Lite repo, creates a venv, installs Kivy, creates .app launcher.

# --- Setup ---
REPO_URL="https://github.com/keglevelmonitor/keglevel_lite.git"

# Detect the real logged-in user (not root) and their home directory
REAL_USER=$(stat -f "%Su" /dev/console)
USER_HOME=$(eval echo "~$REAL_USER")

INSTALL_DIR="$USER_HOME/keglevel_lite"
DATA_DIR="$USER_HOME/keglevel_lite-data"
APPS_DIR="$USER_HOME/Applications"
LAUNCHER_APP="$APPS_DIR/KegLevel Lite.app"
LAUNCHER_MACOS="$LAUNCHER_APP/Contents/MacOS"
LAUNCHER_EXEC="$LAUNCHER_MACOS/KegLevel Lite"
LAUNCHER_RESOURCES="$LAUNCHER_APP/Contents/Resources"
VENV_DIR="$INSTALL_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

# Create data dir early so we have somewhere to write the log
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
chown "$REAL_USER" "$DATA_DIR"
LOG="$DATA_DIR/install_log.txt"

log() { echo "$1" | tee -a "$LOG"; }

echo "" > "$LOG"
log "[$(date)] === KegLevel Lite PKG Installer Started ==="
log "[INFO] Installing for user: $REAL_USER"
log "[INFO] Home directory: $USER_HOME"

# Convenience wrapper: run a command as the real user
run_as_user() {
    sudo -u "$REAL_USER" "$@"
}

# -------------------------------------------------------------------
# STEP 1: Check Git
# -------------------------------------------------------------------
log ""
log "[Step 1/5] Checking Git..."

if ! command -v git &>/dev/null; then
    log "[ERROR] Git not found."
    log "[ERROR] Install Xcode Command Line Tools first:"
    log "[ERROR]   xcode-select --install"
    log "[ERROR] Then re-run this installer."
    exit 1
fi
log "[INFO] Git: $(git --version)"

# -------------------------------------------------------------------
# STEP 2: Find Python 3.8+
# -------------------------------------------------------------------
log ""
log "[Step 2/5] Checking Python..."

PYTHON_EXEC=""

# Check common locations in order of preference
for candidate in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11; do
    if [ -f "$candidate" ]; then
        PY_OK=$("$candidate" -c "import sys; print('ok' if sys.version_info >= (3, 8) else 'bad')" 2>/dev/null)
        if [ "$PY_OK" == "ok" ]; then
            PYTHON_EXEC="$candidate"
            break
        fi
    fi
done

# Fallback: check whatever python3 is on PATH
if [ -z "$PYTHON_EXEC" ] && command -v python3 &>/dev/null; then
    PY_OK=$(python3 -c "import sys; print('ok' if sys.version_info >= (3, 8) else 'bad')" 2>/dev/null)
    if [ "$PY_OK" == "ok" ]; then
        PYTHON_EXEC=$(command -v python3)
    fi
fi

if [ -z "$PYTHON_EXEC" ]; then
    log "[ERROR] Python 3.8+ was not found."
    log "[ERROR] Please install Python from https://www.python.org/downloads/macos/"
    log "[ERROR] Then re-run this installer."
    exit 1
fi
log "[INFO] Python: $PYTHON_EXEC ($($PYTHON_EXEC --version 2>&1))"

# -------------------------------------------------------------------
# STEP 3: Clone or update the repository
# -------------------------------------------------------------------
log ""
log "[Step 3/5] Installing application..."

if [ -d "$INSTALL_DIR/.git" ]; then
    log "[INFO] Existing installation found. Updating via git pull..."
    run_as_user git -C "$INSTALL_DIR" reset --hard >> "$LOG" 2>&1
    run_as_user git -C "$INSTALL_DIR" pull --rebase origin main >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] git pull failed. Check internet connection and try again."
        exit 1
    fi
else
    if [ -d "$INSTALL_DIR" ]; then
        log "[INFO] Removing old installation directory..."
        rm -rf "$INSTALL_DIR"
    fi
    log "[INFO] Cloning repository to $INSTALL_DIR..."
    run_as_user git clone "$REPO_URL" "$INSTALL_DIR" >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] git clone failed. Check internet connection and try again."
        exit 1
    fi
fi
log "[INFO] Repository ready."

# -------------------------------------------------------------------
# STEP 4: Python virtual environment and dependencies
# -------------------------------------------------------------------
log ""
log "[Step 4/5] Setting up Python environment..."

if [ -d "$VENV_DIR" ]; then
    log "[INFO] Removing old virtual environment..."
    rm -rf "$VENV_DIR"
fi

log "[INFO] Creating virtual environment at $VENV_DIR..."
run_as_user "$PYTHON_EXEC" -m venv "$VENV_DIR" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] Failed to create virtual environment."
    exit 1
fi

log "[INFO] Upgrading pip..."
run_as_user "$VENV_PYTHON" -m pip install --upgrade pip >> "$LOG" 2>&1

log "[INFO] Installing dependencies (Kivy - this may take a few minutes)..."
run_as_user "$VENV_PYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] Dependency installation failed. See $LOG for details."
    exit 1
fi
log "[INFO] Dependencies installed."

# -------------------------------------------------------------------
# STEP 5: Create .app launcher
# -------------------------------------------------------------------
log ""
log "[Step 5/5] Creating app launcher..."

run_as_user mkdir -p "$LAUNCHER_MACOS"
run_as_user mkdir -p "$LAUNCHER_RESOURCES"

# Write the launcher executable
cat > "$LAUNCHER_EXEC" << APPSCRIPT
#!/bin/bash
"$VENV_PYTHON" "$INSTALL_DIR/src/main_kivy.py"
APPSCRIPT

chmod +x "$LAUNCHER_EXEC"
chown "$REAL_USER" "$LAUNCHER_EXEC"

# Copy app icon
ICON_SOURCE="$INSTALL_DIR/src/assets/beer-keg.icns"
if [ -f "$ICON_SOURCE" ]; then
    run_as_user cp "$ICON_SOURCE" "$LAUNCHER_RESOURCES/beer-keg.icns"
    ICON_PLIST='    <key>CFBundleIconFile</key>
    <string>beer-keg</string>'
    log "[INFO] App icon installed."
else
    ICON_PLIST=""
    log "[WARN] beer-keg.icns not found - app will use default icon."
fi

# Write Info.plist
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
${ICON_PLIST}
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

chown -R "$REAL_USER" "$LAUNCHER_APP"
log "[INFO] App launcher created: $LAUNCHER_APP"

log ""
log "[$(date)] === KegLevel Lite installation complete! ==="
log "[INFO] Install log saved to: $LOG"
log ""
log "To launch the app:"
log "   Finder -> Go -> Home (Cmd+Shift+H) -> Applications -> KegLevel Lite"

exit 0
