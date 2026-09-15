#!/bin/bash
# Build MatreshkaRemapRenderer.app on macOS.
#
#   chmod +x build_mac.sh
#   ./build_mac.sh
#
# Result: dist/MatreshkaRemapRenderer.app
#
# Everything happens inside this folder -- a local virtual environment, the
# PyInstaller work files and the finished app. Nothing is installed system-wide.
#
# Optional signing, for an app that has to travel to another Mac. Without these
# the build is ad-hoc signed, which is enough to run on the machine that built
# it but not enough for Gatekeeper elsewhere:
#
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" ./build_mac.sh
#
# Add notarization to remove the warning everywhere:
#
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" \
#   APPLE_ID="you@example.com" TEAM_ID="TEAMID" APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
#   ./build_mac.sh

set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
APP="dist/MatreshkaRemapRenderer.app"

PYTHON="${PYTHON:-python3}"
echo "==> using $($PYTHON --version) at $(command -v $PYTHON)"

if ! $PYTHON -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 or newer is required. Install it and rerun, or set PYTHON=/path/to/python3"
    exit 1
fi

echo "==> creating the virtual environment"
$PYTHON -m venv .venv
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -r requirements.txt pyinstaller

echo "==> building"
# On macOS the --add-data separator is ':' -- on Windows it is ';'.
# Paths must be absolute because --specpath moves where relative ones resolve.
./.venv/bin/pyinstaller \
    --noconfirm \
    --windowed \
    --name "MatreshkaRemapRenderer" \
    --osx-bundle-identifier "com.matreshka.remaprenderer" \
    --add-data "$HERE/Check.png:." \
    --add-data "$HERE/remap_tool.json:." \
    --add-data "$HERE/tables/table_full.npz:." \
    --add-data "$HERE/tables/table_half.npz:." \
    --add-data "$HERE/tables/table_quarter.npz:." \
    --add-data "$HERE/tables/screen_geometry.npz:." \
    --add-data "$HERE/tables/viewer_table.npz:." \
    --add-data "$HERE/icons:icons" \
    --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtQuick \
    --exclude-module PySide6.QtQml \
    --exclude-module PySide6.QtMultimedia \
    --exclude-module PySide6.QtCharts \
    --exclude-module PySide6.QtNetwork \
    --exclude-module PySide6.QtPdf \
    --exclude-module PySide6.QtOpenGL \
    --exclude-module cv2 \
    --exclude-module matplotlib \
    --exclude-module scipy \
    --exclude-module PIL \
    --exclude-module tkinter \
    --distpath dist \
    --workpath build \
    --specpath build \
    main.py

if [ -n "${SIGN_IDENTITY:-}" ]; then
    echo "==> signing with $SIGN_IDENTITY"
    # --deep is deprecated but still the practical way to reach the bundled
    # Qt frameworks; sign inner binaries first, the bundle itself last.
    find "$APP" \( -name "*.dylib" -o -name "*.so" -o -perm +111 -type f \) -print0 |
        xargs -0 -I {} codesign --force --timestamp --options runtime \
            --entitlements entitlements.plist --sign "$SIGN_IDENTITY" {} 2>/dev/null || true
    codesign --force --deep --timestamp --options runtime \
        --entitlements entitlements.plist --sign "$SIGN_IDENTITY" "$APP"
    codesign --verify --deep --strict --verbose=2 "$APP"

    if [ -n "${APPLE_ID:-}" ] && [ -n "${TEAM_ID:-}" ] && [ -n "${APP_PASSWORD:-}" ]; then
        echo "==> notarizing (this uploads the app to Apple and waits)"
        ditto -c -k --keepParent "$APP" dist/notarize.zip
        xcrun notarytool submit dist/notarize.zip \
            --apple-id "$APPLE_ID" --team-id "$TEAM_ID" --password "$APP_PASSWORD" --wait
        xcrun stapler staple "$APP"
        rm -f dist/notarize.zip
        echo "==> notarized and stapled"
    else
        echo "==> signed but NOT notarized: other Macs will still warn."
        echo "    Set APPLE_ID, TEAM_ID and APP_PASSWORD to notarize."
    fi
else
    echo "==> no SIGN_IDENTITY given: ad-hoc signature only."
    echo "    Fine on this Mac, will be blocked if the app is sent elsewhere."
fi

# A bundle that travelled through a zip carries the quarantine flag; macOS then
# runs it from a random read-only copy, where it cannot create its folders.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

echo
echo "==> done: $HERE/$APP"
spctl -a -vvv "$APP" 2>&1 | sed 's/^/    gatekeeper: /' || true
echo
echo "    Move the .app into an empty folder in Finder and launch it -- it"
echo "    creates ToRemap/ and OUT/ beside itself on first launch."
