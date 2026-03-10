#!/bin/bash
# setup_mac.sh
# Single-line installer wrapper for KegLevel Pico on macOS

# 1. Define the Install Directories
INSTALL_DIR="$HOME/keglevel_pico"
DATA_DIR="$HOME/keglevel_pico-data"
LAUNCHER_APP="$HOME/Applications/KegLevel Pico.app"
WHAT_TO_INSTALL="KegLevel Pico Application and Data Directory"
CLEANUP_MODE="NONE"

# SAFETY CHECK: Ensure we are not running from inside the install dir
CURRENT_DIR=$(pwd)
if [[ "$CURRENT_DIR" == "$INSTALL_DIR"* ]]; then
    echo "ERROR: You are running this script from inside the installation directory."
    echo "Please run this command from your home folder ($HOME) instead."
    exit 1
fi

echo "========================================"
echo "   KegLevel Pico Auto-Installer (macOS)"
echo "========================================"

# 2. Logic to handle existing installs
if [ -d "$INSTALL_DIR" ] || [ -d "$DATA_DIR" ]; then
    while true; do
        echo ""
        echo "Existing installation detected:"
        [ -d "$INSTALL_DIR" ] && echo " - App Folder: $INSTALL_DIR"
        [ -d "$DATA_DIR" ]    && echo " - Data Folder: $DATA_DIR"
        echo ""
        echo "How would you like to proceed? (Case Sensitive)"
        echo "  UPDATE    - Update the App (Git Pull) & Re-run install (Keeps data)"
        echo "  APP       - Reinstall App only (Deletes App folder, Keeps data)"
        echo "  ALL       - Reinstall App AND reset data (Fresh Install)"
        echo "  UNINSTALL - Uninstall the app and the data directory"
        echo "  EXIT      - Cancel"
        echo ""
        read -p "Enter selection: " choice

        if [ "$choice" == "UPDATE" ]; then
            WHAT_TO_INSTALL="KegLevel Pico Update"
            CLEANUP_MODE="NONE"
            break
        elif [ "$choice" == "APP" ]; then
            WHAT_TO_INSTALL="KegLevel Pico Application (Fresh App, Keep Data)"
            CLEANUP_MODE="APP"
            break
        elif [ "$choice" == "ALL" ]; then
            WHAT_TO_INSTALL="KegLevel Pico Application and Data Directory (Fresh Install)"
            CLEANUP_MODE="ALL"
            break
        elif [ "$choice" == "UNINSTALL" ]; then
            echo "------------------------------------------"
            echo "YOU ARE ABOUT TO DELETE:"
            echo "The KegLevel Pico application AND all user data/settings."
            echo "------------------------------------------"
            echo ""
            read -p "Type YES to UNINSTALL, or any other key to return: " confirm

            if [ "$confirm" == "YES" ]; then
                echo ""
                echo "Removing files..."

                if [ -d "$LAUNCHER_APP" ]; then
                    rm -rf "$LAUNCHER_APP"
                    echo " - Removed app launcher: $LAUNCHER_APP"
                fi
                if [ -d "$INSTALL_DIR" ]; then
                    rm -rf "$INSTALL_DIR"
                    echo " - Removed application directory: $INSTALL_DIR"
                fi
                if [ -d "$DATA_DIR" ]; then
                    rm -rf "$DATA_DIR"
                    echo " - Removed data directory: $DATA_DIR"
                fi

                echo ""
                echo "=========================================="
                echo "   Uninstallation Complete"
                echo "=========================================="
                exit 0
            else
                echo "Uninstallation aborted."
            fi
        elif [ "$choice" == "EXIT" ]; then
            echo "Cancelled."
            exit 0
        else
            echo "Invalid selection."
        fi
    done
fi

# 3. Size Warning / Confirmation
echo ""
echo "------------------------------------------------------------"
echo "Processing: $WHAT_TO_INSTALL"
echo "and will use about 200 MB of storage space."
echo ""
echo "Basic installed file structure:"
echo ""
echo "  $INSTALL_DIR/"
echo "  |-- utility files..."
echo "  |-- src/"
echo "  |   |-- application files..."
echo "  |   |-- assets/"
echo "  |       |-- supporting files..."
echo "  |-- venv/"
echo "  |   |-- python3 & dependencies"
echo "  $DATA_DIR/"
echo "  |-- user data..."
echo "  $LAUNCHER_APP"
echo ""
echo "------------------------------------------------------------"
echo ""

read -p "Press Y to proceed, or any other key to cancel: " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 1
fi

# 4. Perform Cleanup (Delayed until AFTER confirmation)
if [ "$CLEANUP_MODE" == "APP" ]; then
    echo "Removing existing application..."
    rm -rf "$INSTALL_DIR"
    rm -rf "$LAUNCHER_APP"
elif [ "$CLEANUP_MODE" == "ALL" ]; then
    echo "Removing application and data..."
    rm -rf "$INSTALL_DIR"
    rm -rf "$DATA_DIR"
    rm -rf "$LAUNCHER_APP"
fi

# 5. Check for Git
if ! command -v git &>/dev/null; then
    echo ""
    echo "Git is not installed."
    echo "On macOS, Git is included with Xcode Command Line Tools."
    echo "A system prompt may appear asking you to install it now."
    echo ""
    echo "If no prompt appears, run this command and re-run this installer:"
    echo "  xcode-select --install"
    echo ""
    # Attempt to trigger the CLT install prompt
    xcode-select --install 2>/dev/null
    echo "After Xcode Command Line Tools finishes installing, re-run this installer."
    exit 1
fi

# 6. Clone Repo OR Update
if [ -d "$INSTALL_DIR" ]; then
    echo "Directory exists. Updating via Git Pull..."
    cd "$INSTALL_DIR" || exit 1
    git reset --hard
    git pull --rebase
else
    echo "Cloning repository to $INSTALL_DIR..."
    git clone https://github.com/keglevelmonitor/keglevelpico.git "$INSTALL_DIR"
    cd "$INSTALL_DIR" || exit 1
fi

# 7. Run the Main Installer
echo "Launching main installer..."
chmod +x install_mac.sh
./install_mac.sh
