#!/bin/bash
# post_install_mac.sh
# Bundled inside KegLevelSuite_Setup.pkg by build_installer_mac.sh.
# Runs as root after the PKG wizard completes on the end user's Mac.
# Installs BOTH apps (all-or-nothing):
#   App 1: KegLevel Pico  -> ~/keglevel_pico   + ~/Applications/KegLevel Pico.app
#   App 2: BatchFlow      -> ~/batchflow        + ~/Applications/BatchFlow.app
# If either app fails the whole install script exits non-zero (PKG reports failure).

# --- Setup ---
KL_REPO="https://github.com/keglevelmonitor/keglevelpico.git"
BF_REPO="https://github.com/keglevelmonitor/batchflow.git"

# Detect the real logged-in user (not root) and their home directory
REAL_USER=$(stat -f "%Su" /dev/console)
USER_HOME=$(eval echo "~$REAL_USER")

KL_DIR="$USER_HOME/keglevel_pico"
KL_DATA="$USER_HOME/keglevel_pico-data"
BF_DIR="$USER_HOME/batchflow"

APPS_DIR="$USER_HOME/Applications"

KL_APP="$APPS_DIR/KegLevel Pico.app"
KL_APP_MACOS="$KL_APP/Contents/MacOS"
KL_APP_EXEC="$KL_APP_MACOS/KegLevel Pico"
KL_APP_RESOURCES="$KL_APP/Contents/Resources"
KL_VENV="$KL_DIR/venv"
KL_VENV_PYTHON="$KL_VENV/bin/python"

BF_APP="$APPS_DIR/BatchFlow.app"
BF_APP_MACOS="$BF_APP/Contents/MacOS"
BF_APP_EXEC="$BF_APP_MACOS/BatchFlow"
BF_APP_RESOURCES="$BF_APP/Contents/Resources"
BF_VENV="$BF_DIR/venv"
BF_VENV_PYTHON="$BF_VENV/bin/python"

# Create data dir early so we have somewhere to write the log
mkdir -p "$KL_DATA"
chmod 700 "$KL_DATA"
chown "$REAL_USER" "$KL_DATA"
LOG="$KL_DATA/install_log.txt"

log() { echo "$1" | tee -a "$LOG"; }

echo "" > "$LOG"
log "[$(date)] === KegLevel Suite PKG Installer Started ==="
log "[INFO] Installing KegLevel Pico + BatchFlow (all-or-nothing)"
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
log "[Step 1/8] Checking Git..."

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
log "[Step 2/8] Checking Python..."

PYTHON_EXEC=""

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

# ===================================================================
# APP 1: KEGLEVEL PICO
# ===================================================================
log ""
log "[$(date)] --- [App 1/2] Installing KegLevel Pico ---"

# -------------------------------------------------------------------
# STEP 3: Clone or update KegLevel Pico repository
# -------------------------------------------------------------------
log ""
log "[Step 3/8] Cloning/updating KegLevel Pico..."

if [ -d "$KL_DIR/.git" ]; then
    log "[INFO] KegLevel Pico: existing installation found. Updating via git pull..."
    run_as_user git -C "$KL_DIR" reset --hard >> "$LOG" 2>&1
    run_as_user git -C "$KL_DIR" pull --rebase origin main >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] KegLevel Pico: git pull failed. Check internet connection and try again."
        exit 1
    fi
else
    if [ -d "$KL_DIR" ]; then
        log "[INFO] KegLevel Pico: removing old directory..."
        rm -rf "$KL_DIR"
    fi
    log "[INFO] KegLevel Pico: cloning repository to $KL_DIR..."
    run_as_user git clone "$KL_REPO" "$KL_DIR" >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] KegLevel Pico: git clone failed. Check internet connection and try again."
        exit 1
    fi
fi
log "[INFO] KegLevel Pico: repository ready."

# -------------------------------------------------------------------
# STEP 4: KegLevel Pico virtual environment and dependencies
# -------------------------------------------------------------------
log ""
log "[Step 4/8] Setting up KegLevel Pico Python environment..."

if [ -d "$KL_VENV" ]; then
    log "[INFO] KegLevel Pico: removing old virtual environment..."
    rm -rf "$KL_VENV"
fi

log "[INFO] KegLevel Pico: creating virtual environment at $KL_VENV..."
run_as_user "$PYTHON_EXEC" -m venv "$KL_VENV" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] KegLevel Pico: failed to create virtual environment."
    exit 1
fi

log "[INFO] KegLevel Pico: upgrading pip..."
run_as_user "$KL_VENV_PYTHON" -m pip install --upgrade pip >> "$LOG" 2>&1

log "[INFO] KegLevel Pico: installing dependencies (Kivy - this may take a few minutes)..."
run_as_user "$KL_VENV_PYTHON" -m pip install -r "$KL_DIR/requirements.txt" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] KegLevel Pico: dependency installation failed. See $LOG for details."
    exit 1
fi
log "[INFO] KegLevel Pico: dependencies installed."

# -------------------------------------------------------------------
# STEP 5: Create KegLevel Pico .app launcher
# -------------------------------------------------------------------
log ""
log "[Step 5/8] Creating KegLevel Pico app launcher..."

run_as_user mkdir -p "$KL_APP_MACOS"
run_as_user mkdir -p "$KL_APP_RESOURCES"

cat > "$KL_APP_EXEC" << APPSCRIPT
#!/bin/bash
"$KL_VENV_PYTHON" "$KL_DIR/src/main_kivy.py"
APPSCRIPT

chmod +x "$KL_APP_EXEC"
chown "$REAL_USER" "$KL_APP_EXEC"

KL_ICON_SOURCE="$KL_DIR/src/assets/beer-keg.icns"
if [ -f "$KL_ICON_SOURCE" ]; then
    run_as_user cp "$KL_ICON_SOURCE" "$KL_APP_RESOURCES/beer-keg.icns"
    KL_ICON_PLIST='    <key>CFBundleIconFile</key>
    <string>beer-keg</string>'
    log "[INFO] KegLevel Pico: app icon installed."
else
    KL_ICON_PLIST=""
    log "[WARN] KegLevel Pico: beer-keg.icns not found - app will use default icon."
fi

cat > "$KL_APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>KegLevel Pico</string>
    <key>CFBundleDisplayName</key>
    <string>KegLevel Pico</string>
    <key>CFBundleIdentifier</key>
    <string>com.keglevelmonitor.keglevel-pico</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>KegLevel Pico</string>
${KL_ICON_PLIST}
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

chown -R "$REAL_USER" "$KL_APP"
log "[INFO] KegLevel Pico: app launcher created at $KL_APP"
log "[INFO] KegLevel Pico installation complete."

# ===================================================================
# APP 2: BATCHFLOW
# ===================================================================
log ""
log "[$(date)] --- [App 2/2] Installing BatchFlow ---"

# -------------------------------------------------------------------
# STEP 6: Clone or update BatchFlow repository
# -------------------------------------------------------------------
log ""
log "[Step 6/8] Cloning/updating BatchFlow..."

if [ -d "$BF_DIR/.git" ]; then
    log "[INFO] BatchFlow: existing installation found. Updating via git pull..."
    run_as_user git -C "$BF_DIR" reset --hard >> "$LOG" 2>&1
    run_as_user git -C "$BF_DIR" pull --rebase origin main >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] BatchFlow: git pull failed. Check internet connection and try again."
        exit 1
    fi
else
    if [ -d "$BF_DIR" ]; then
        log "[INFO] BatchFlow: removing old directory..."
        rm -rf "$BF_DIR"
    fi
    log "[INFO] BatchFlow: cloning repository to $BF_DIR..."
    run_as_user git clone "$BF_REPO" "$BF_DIR" >> "$LOG" 2>&1
    if [ $? -ne 0 ]; then
        log "[ERROR] BatchFlow: git clone failed. Check internet connection and try again."
        exit 1
    fi
fi
log "[INFO] BatchFlow: repository ready."

# -------------------------------------------------------------------
# STEP 7: BatchFlow virtual environment and dependencies
# -------------------------------------------------------------------
log ""
log "[Step 7/8] Setting up BatchFlow Python environment..."

if [ -d "$BF_VENV" ]; then
    log "[INFO] BatchFlow: removing old virtual environment..."
    rm -rf "$BF_VENV"
fi

log "[INFO] BatchFlow: creating virtual environment at $BF_VENV..."
run_as_user "$PYTHON_EXEC" -m venv "$BF_VENV" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] BatchFlow: failed to create virtual environment."
    exit 1
fi

log "[INFO] BatchFlow: upgrading pip..."
run_as_user "$BF_VENV_PYTHON" -m pip install --upgrade pip >> "$LOG" 2>&1

log "[INFO] BatchFlow: installing dependencies..."
run_as_user "$BF_VENV_PYTHON" -m pip install -r "$BF_DIR/requirements.txt" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "[ERROR] BatchFlow: dependency installation failed. See $LOG for details."
    exit 1
fi
log "[INFO] BatchFlow: dependencies installed."

# -------------------------------------------------------------------
# STEP 8: Create BatchFlow .app launcher
# -------------------------------------------------------------------
log ""
log "[Step 8/8] Creating BatchFlow app launcher..."

run_as_user mkdir -p "$BF_APP_MACOS"
run_as_user mkdir -p "$BF_APP_RESOURCES"

cat > "$BF_APP_EXEC" << APPSCRIPT
#!/bin/bash
"$BF_VENV_PYTHON" "$BF_DIR/src/batchflow_main.py"
APPSCRIPT

chmod +x "$BF_APP_EXEC"
chown "$REAL_USER" "$BF_APP_EXEC"

BF_ICON_SOURCE="$BF_DIR/src/assets/batchflow.icns"
if [ -f "$BF_ICON_SOURCE" ]; then
    run_as_user cp "$BF_ICON_SOURCE" "$BF_APP_RESOURCES/batchflow.icns"
    BF_ICON_PLIST='    <key>CFBundleIconFile</key>
    <string>batchflow</string>'
    log "[INFO] BatchFlow: app icon installed."
else
    BF_ICON_PLIST=""
    log "[WARN] BatchFlow: batchflow.icns not found - app will use default icon."
fi

cat > "$BF_APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>BatchFlow</string>
    <key>CFBundleDisplayName</key>
    <string>BatchFlow</string>
    <key>CFBundleIdentifier</key>
    <string>com.keglevelmonitor.batchflow</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>BatchFlow</string>
${BF_ICON_PLIST}
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

chown -R "$REAL_USER" "$BF_APP"
log "[INFO] BatchFlow: app launcher created at $BF_APP"
log "[INFO] BatchFlow installation complete."

# ===================================================================
# DONE
# ===================================================================
log ""
log "[$(date)] === KegLevel Suite installation complete! ==="
log "[INFO] Install log saved to: $LOG"
log ""
log "To launch the apps:"
log "   Finder -> Go -> Home (Cmd+Shift+H) -> Applications"
log "   Then open 'KegLevel Pico' or 'BatchFlow'"

exit 0
