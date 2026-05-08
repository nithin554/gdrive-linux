#!/bin/bash
# Build .deb package (Debian/Ubuntu/Linux Mint/Pop!_OS)
set -euo pipefail

VERSION="${1:-0.0.0}"
BINARY="${2:-dist/gdrive-linux}"
OUTPUT_DIR="${3:-.}"

echo "Building .deb package version ${VERSION}..."

PKG_DIR=$(mktemp -d)

mkdir -p "${PKG_DIR}/usr/bin"
mkdir -p "${PKG_DIR}/usr/share/applications"
mkdir -p "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${PKG_DIR}/DEBIAN"

cp "${BINARY}" "${PKG_DIR}/usr/bin/gdrive-linux"

# Copy icon if available
if [ -f "icons/gdrive-linux.png" ]; then
    cp "icons/gdrive-linux.png" "${PKG_DIR}/usr/share/icons/hicolor/256x256/apps/gdrive-linux.png"
fi

cat > "${PKG_DIR}/usr/share/applications/gdrive-linux.desktop" << 'EOF'
[Desktop Entry]
Name=gdrive-linux
Comment=Google Drive two-way sync for Linux
Exec=/usr/bin/gdrive-linux
Icon=gdrive-linux
Terminal=false
Type=Application
Categories=Utility;FileTools;
StartupNotify=true
EOF

cat > "${PKG_DIR}/DEBIAN/control" << EOF
Package: gdrive-linux
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Depends: libfuse2
Maintainer: gdrive-linux <noreply@github.com>
Description: Native Linux Google Drive two-way sync application
 A native Linux application for two-way synchronization with Google Drive,
 featuring real-time file monitoring, system tray integration, and
 on-demand file content management.
EOF

dpkg-deb --build "${PKG_DIR}" "${OUTPUT_DIR}/gdrive-linux-${VERSION}.deb"
rm -rf "${PKG_DIR}"

echo "Done: ${OUTPUT_DIR}/gdrive-linux-${VERSION}.deb"
