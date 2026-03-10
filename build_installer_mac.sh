#!/bin/bash
# build_installer_mac.sh
# Builds KegLevelSuite_Setup.dmg for distribution via GitHub Releases.
# Installs BOTH apps: KegLevel Pico + BatchFlow.
#
# Usage:
#   Run from within the keglevelpico repo folder on your Mac:
#     cd ~/keglevel_pico
#     chmod +x build_installer_mac.sh
#     ./build_installer_mac.sh
#
# Prerequisites (one-time setup on your Mac):
#   - Xcode Command Line Tools:  xcode-select --install
#
# Output:
#   ~/Desktop/KegLevelSuite_Setup.dmg
#   Upload this file to GitHub Releases.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"
OUTPUT_DIR="$HOME/Desktop"
BUILD_DIR="$SCRIPT_DIR/mac_build"
SCRIPTS_DIR="$BUILD_DIR/scripts"
DMG_STAGING="$BUILD_DIR/dmg_staging"

BUNDLE_ID="com.keglevelmonitor.keglevel-suite"
PKG_COMPONENT="$BUILD_DIR/KegLevelSuite_component.pkg"
PKG_FINAL="$BUILD_DIR/KegLevelSuite_Installer.pkg"
DMG_OUTPUT="$OUTPUT_DIR/KegLevelSuite_Setup.dmg"
DMG_VOLUME="KegLevel Suite Installer"

# Read app version from version.py
APP_VERSION=$(python3 -c "exec(open('$APP_DIR/src/version.py').read()); print(APP_VERSION)" 2>/dev/null || echo "1.0")

echo "========================================"
echo "   KegLevel Suite macOS DMG Builder"
echo "   KegLevel Pico + BatchFlow"
echo "========================================"
echo "Version:  $APP_VERSION"
echo "Repo dir: $APP_DIR"
echo "Output:   $DMG_OUTPUT"
echo ""

# ---------------------------------------------------------------------------
# CHECK PREREQUISITES
# ---------------------------------------------------------------------------
echo "[....] Checking prerequisites..."

if ! command -v pkgbuild &>/dev/null; then
    echo "[ERROR] pkgbuild not found."
    echo "        Install Xcode Command Line Tools:  xcode-select --install"
    exit 1
fi
if ! command -v productbuild &>/dev/null; then
    echo "[ERROR] productbuild not found."
    echo "        Install Xcode Command Line Tools:  xcode-select --install"
    exit 1
fi
if ! command -v hdiutil &>/dev/null; then
    echo "[ERROR] hdiutil not found. This script must run on macOS."
    exit 1
fi
if [ ! -f "$APP_DIR/post_install_mac.sh" ]; then
    echo "[ERROR] post_install_mac.sh not found at: $APP_DIR/post_install_mac.sh"
    exit 1
fi
echo "[OK]   Prerequisites satisfied."

# ---------------------------------------------------------------------------
# CLEAN UP PREVIOUS BUILD
# ---------------------------------------------------------------------------
rm -rf "$BUILD_DIR"
mkdir -p "$SCRIPTS_DIR"
mkdir -p "$DMG_STAGING"

# ---------------------------------------------------------------------------
# PREPARE PKG SCRIPTS
# ---------------------------------------------------------------------------
echo "[....] Preparing installer scripts..."
cp "$APP_DIR/post_install_mac.sh" "$SCRIPTS_DIR/postinstall"
chmod +x "$SCRIPTS_DIR/postinstall"
echo "[OK]   Scripts ready."

# ---------------------------------------------------------------------------
# GENERATE DISTRIBUTION XML
# ---------------------------------------------------------------------------
echo "[....] Generating distribution manifest..."
cat > "$BUILD_DIR/distribution.xml" << DISTXML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>KegLevel Suite</title>
    <welcome file="welcome.html" mime-type="text/html"/>
    <conclusion file="conclusion.html" mime-type="text/html"/>
    <pkg-ref id="${BUNDLE_ID}"/>
    <options customize="never" require-scripts="false" hostArchitectures="x86_64,arm64"/>
    <choices-outline>
        <line choice="default">
            <line choice="${BUNDLE_ID}"/>
        </line>
    </choices-outline>
    <choice id="default"/>
    <choice id="${BUNDLE_ID}" visible="false">
        <pkg-ref id="${BUNDLE_ID}"/>
    </choice>
    <pkg-ref id="${BUNDLE_ID}" version="${APP_VERSION}" onConclusion="none">KegLevelSuite_component.pkg</pkg-ref>
</installer-gui-script>
DISTXML
echo "[OK]   Distribution manifest created."

# ---------------------------------------------------------------------------
# GENERATE INSTALLER WELCOME PAGE
# ---------------------------------------------------------------------------
cat > "$BUILD_DIR/welcome.html" << WELCOMEHTML
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; padding: 20px; background: #1a1a1a; color: #e0e0e0;">
    <h2 style="color: #FFC107;">Welcome to KegLevel Suite</h2>
    <p>Version ${APP_VERSION}</p>
    <p>This installer will set up <strong>KegLevel Pico</strong> and <strong>BatchFlow</strong> on your Mac.</p>
    <p>Both apps are installed together &mdash; this is an all-or-nothing install. If either app fails to install, the whole installation is considered failed.</p>
    <p><strong>The installer will:</strong></p>
    <ul>
        <li>Download KegLevel Pico from GitHub (~10 MB)</li>
        <li>Download BatchFlow from GitHub (~10 MB)</li>
        <li>Set up Python environments with required dependencies for each app</li>
        <li>Create launchers in your home Applications folder</li>
    </ul>
    <p><strong>Requirements:</strong></p>
    <ul>
        <li>macOS 10.14 or later</li>
        <li>Active internet connection</li>
        <li>Git &mdash; Xcode Command Line Tools (free from Apple)</li>
        <li>Python 3.8 or later (from python.org or Homebrew)</li>
    </ul>
    <p style="color: #aaa; font-size: 12px;">
        If Git or Python are not installed, please install them before continuing.<br>
        Git: <code>xcode-select --install</code> in Terminal<br>
        Python: <a href="https://www.python.org/downloads/macos/" style="color:#FFC107;">python.org/downloads</a>
    </p>
</body>
</html>
WELCOMEHTML

# ---------------------------------------------------------------------------
# GENERATE INSTALLER CONCLUSION PAGE
# ---------------------------------------------------------------------------
cat > "$BUILD_DIR/conclusion.html" << CONCLUSIONHTML
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; padding: 20px; background: #1a1a1a; color: #e0e0e0;">
    <h2 style="color: #FFC107;">Installation Complete!</h2>
    <p><strong>KegLevel Pico</strong> and <strong>BatchFlow</strong> have been installed successfully.</p>
    <p><strong>To launch the apps:</strong></p>
    <ol>
        <li>Open <strong>Finder</strong></li>
        <li>Press <strong>Cmd + Shift + H</strong> to go to your Home folder</li>
        <li>Open the <strong>Applications</strong> folder</li>
        <li>Double-click <strong>KegLevel Pico</strong> or <strong>BatchFlow</strong></li>
    </ol>
    <p>You can drag either app from that folder to your Dock for easy access.</p>
    <p style="color: #aaa; font-size: 12px;">
        To update the apps in the future, use <strong>Settings &rarr; Updates</strong> inside KegLevel Pico.<br>
        Install log saved to: ~/keglevel_pico-data/install_log.txt
    </p>
</body>
</html>
CONCLUSIONHTML
echo "[OK]   Installer pages created."

# ---------------------------------------------------------------------------
# BUILD COMPONENT PKG (scripts only, no payload)
# ---------------------------------------------------------------------------
echo ""
echo "[....] Building component package..."
pkgbuild \
    --nopayload \
    --scripts "$SCRIPTS_DIR" \
    --identifier "$BUNDLE_ID" \
    --version "$APP_VERSION" \
    "$PKG_COMPONENT"
echo "[OK]   Component package built."

# ---------------------------------------------------------------------------
# WRAP IN DISTRIBUTION PKG (adds the wizard UI)
# ---------------------------------------------------------------------------
echo "[....] Building distribution package..."
productbuild \
    --distribution "$BUILD_DIR/distribution.xml" \
    --package-path "$BUILD_DIR" \
    --resources "$BUILD_DIR" \
    "$PKG_FINAL"
echo "[OK]   Distribution package built."

# ---------------------------------------------------------------------------
# STAGE DMG CONTENTS
# ---------------------------------------------------------------------------
echo ""
echo "[....] Staging DMG contents..."
cp "$PKG_FINAL" "$DMG_STAGING/KegLevel Suite Installer.pkg"

cat > "$DMG_STAGING/README.txt" << README
KegLevel Suite - macOS Installer
=================================
Installs: KegLevel Pico + BatchFlow

1. Double-click "KegLevel Suite Installer.pkg" to install.

2. If macOS blocks the installer (Gatekeeper warning):
      Right-click the .pkg -> "Open" -> "Open" in the dialog.

3. Requirements before installing:
      - Internet connection
      - Git (run: xcode-select --install  in Terminal)
      - Python 3.8+ (https://www.python.org/downloads/macos/)

4. After installation, find the apps at:
      Finder -> Go -> Home (Cmd+Shift+H) -> Applications
      -> KegLevel Pico
      -> BatchFlow

5. To update the apps in the future:
      Use Settings -> Updates inside KegLevel Pico.

Installation log: ~/keglevel_pico-data/install_log.txt
README

echo "[OK]   DMG contents staged."

# ---------------------------------------------------------------------------
# BUILD DMG
# ---------------------------------------------------------------------------
echo "[....] Creating DMG..."
rm -f "$DMG_OUTPUT"
hdiutil create \
    -volname "$DMG_VOLUME" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_OUTPUT"
echo "[OK]   DMG created."

# ---------------------------------------------------------------------------
# CLEAN UP build artifacts (keep only the DMG)
# ---------------------------------------------------------------------------
rm -rf "$BUILD_DIR"

echo ""
echo "========================================"
echo "   Build Complete!"
echo ""
echo "   ~/Desktop/KegLevelSuite_Setup.dmg"
echo ""
echo "   Upload this file to GitHub Releases."
echo "========================================"
