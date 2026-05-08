#!/bin/bash
# Build AppImage (universal, works on all Linux distros)
set -euo pipefail

VERSION="${1:-0.0.0}"
BINARY="${2:-dist/gdrive-linux}"
OUTPUT_DIR="${3:-.}"

echo "Building AppImage version ${VERSION}..."

# We use appimagetool (formerly linuxdeploy) to create the AppImage
APPDIR="gdrive-linux.AppDir"
mkdir -p "${APPDIR}/usr/bin"
mkdir -p "${APPDIR}/usr/share/applications"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

cp "${BINARY}" "${APPDIR}/usr/bin/gdrive-linux"

# Create AppRun wrapper
cat > "${APPDIR}/AppRun" << 'APPIMAGE_EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/gdrive-linux" "$@"
APPIMAGE_EOF
chmod +x "${APPDIR}/AppRun"

# Desktop file for AppImage
cat > "${APPDIR}/usr/share/applications/gdrive-linux.desktop" << 'EOF'
[Desktop Entry]
Name=gdrive-linux
Comment=Google Drive two-way sync for Linux
Exec=gdrive-linux
Icon=gdrive-linux
Terminal=false
Type=Application
Categories=Utility;FileTools;
StartupNotify=true
EOF

# Copy desktop file to root of AppDir (required by appimagetool)
cp "${APPDIR}/usr/share/applications/gdrive-linux.desktop" "${APPDIR}/gdrive-linux.desktop"

# Use the real icon if available, otherwise fall back to placeholder
ICON_SRC="icons/gdrive-linux.png"
if [ -f "${ICON_SRC}" ]; then
    cp "${ICON_SRC}" "${APPDIR}/gdrive-linux.png"
    cp "${ICON_SRC}" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/gdrive-linux.png"
else
    # Generate a minimal valid 1x1 PNG
    printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDAT\x78\x9c\x63\x00\x01\x00\x00\x05\x00\x01\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82' > "${APPDIR}/gdrive-linux.png"
    cp "${APPDIR}/gdrive-linux.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/gdrive-linux.png"
fi

# Download appimagetool if not available
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
if ! command -v "${APPIMAGETOOL}" &> /dev/null; then
    echo "Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" -O /tmp/appimagetool
    chmod +x /tmp/appimagetool
    APPIMAGETOOL="/tmp/appimagetool"
fi

# Create the AppImage
ARCH=x86_64 "${APPIMAGETOOL}" "${APPDIR}" "${OUTPUT_DIR}/gdrive-linux-${VERSION}-x86_64.AppImage"

rm -rf "${APPDIR}"

echo "Done: ${OUTPUT_DIR}/gdrive-linux-${VERSION}-x86_64.AppImage"
