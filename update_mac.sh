#!/bin/bash
# update_mac.sh
# Handles checking, pulling code, and dependency updates for KegLevel Lite on macOS.

# --- 1. Setup ---
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
VENV_PYTHON_EXEC="$VENV_DIR/bin/python"
MODE=$1

# Detect Branch (main or master)
BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "--- KegLevel Lite Update Manager (macOS) ---"
echo "Root: $PROJECT_DIR"
echo "Branch: $BRANCH"

# --- 2. Check for Git Sanity ---
if [ ! -d "$PROJECT_DIR/.git" ]; then
    echo "[ERROR] Not a Git repository."
    exit 1
fi

# Ignore execute-bit changes (chmod +x) so git pull does not fail on install scripts
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
    echo "Resetting install_mac.sh and update_mac.sh (chmod changes) and retrying..."
    git checkout -- install_mac.sh update_mac.sh 2>/dev/null
    if ! git pull --rebase origin $BRANCH; then
        echo "[ERROR] git pull failed."
        exit 1
    fi
fi
chmod +x "$PROJECT_DIR/install_mac.sh" "$PROJECT_DIR/update_mac.sh" 2>/dev/null || true

# --- 5. Python Dependencies ---
echo "Updating Python environment..."

if [ ! -f "$VENV_PYTHON_EXEC" ]; then
    echo "[ERROR] Virtual environment missing. Please re-run the installer:"
    echo "  bash <(curl -sL bit.ly/keglevel-lite-mac)"
    exit 1
fi

"$VENV_PYTHON_EXEC" -m pip install -r "$PROJECT_DIR/requirements.txt"

if [ $? -ne 0 ]; then
    echo "[FATAL ERROR] Pip install failed."
    exit 1
fi

# --- 6. Refresh App Launcher (ensures paths are correct after update) ---
LAUNCHER_APP="$HOME/Applications/KegLevel Lite.app"
LAUNCHER_EXEC="$LAUNCHER_APP/Contents/MacOS/KegLevel Lite"

if [ -f "$LAUNCHER_EXEC" ]; then
    echo "Updating app launcher paths..."
    cat > "$LAUNCHER_EXEC" << APPSCRIPT
#!/bin/bash
"$VENV_PYTHON_EXEC" "$PROJECT_DIR/src/main_kivy.py"
APPSCRIPT
    chmod +x "$LAUNCHER_EXEC"
    echo "Launcher updated."
fi

echo "--- Update Complete! Please Restart. ---"
exit 0
