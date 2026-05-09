#!/bin/bash
# Build Arch Linux package (PKGBUILD → .pkg.tar.zst)
set -euo pipefail

VERSION="${1:-0.0.0}"
BINARY="${2:-dist/gdrive-linux}"
OUTPUT_DIR="${3:-.}"

# Arch PKGBUILD pkgver doesn't allow colons, forward slashes, hyphens or whitespace
ARCH_VERSION="${VERSION//-/.}"

echo "Building Arch Linux package version ${VERSION} (Arch: ${ARCH_VERSION})..."

PKGDIR="gdrive-linux-pkg"
mkdir -p "${PKGDIR}/usr/bin"
mkdir -p "${PKGDIR}/usr/share/applications"
mkdir -p "${PKGDIR}/usr/share/icons/hicolor/256x256/apps"

cp "${BINARY}" "${PKGDIR}/usr/bin/gdrive-linux"

# Copy icon if available
if [ -f "icons/gdrive-linux.png" ]; then
    cp "icons/gdrive-linux.png" "${PKGDIR}/usr/share/icons/hicolor/256x256/apps/gdrive-linux.png"
fi

cat > "${PKGDIR}/usr/share/applications/gdrive-linux.desktop" << 'EOF'
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

# Create PKGBUILD in current dir
cat > PKGBUILD << EOF
# Maintainer: gdrive-linux <noreply@github.com>
pkgname=gdrive-linux
pkgver=${ARCH_VERSION}
pkgrel=1
pkgdesc="Native Linux Google Drive two-way sync application"
arch=('x86_64')
url="https://github.com/nithin/gdrive-linux"
license=('MIT')
depends=('fuse2')
source=("gdrive-linux-${ARCH_VERSION}.tar.gz")
md5sums=('SKIP')

package() {
  cd "\${srcdir}"
  install -D -m 0755 gdrive-linux "\${pkgdir}/usr/bin/gdrive-linux"
  install -D -m 0644 gdrive-linux.desktop "\${pkgdir}/usr/share/applications/gdrive-linux.desktop"
  install -D -m 0644 gdrive-linux.png "\${pkgdir}/usr/share/icons/hicolor/256x256/apps/gdrive-linux.png"
}
EOF

# Create source tarball — flat structure (no top-level dir) to match PKGBUILD's %%setup -q
mkdir -p "gdrive-linux-${ARCH_VERSION}"
cp "${BINARY}" "gdrive-linux-${ARCH_VERSION}/gdrive-linux"
cp "${PKGDIR}/usr/share/applications/gdrive-linux.desktop" "gdrive-linux-${ARCH_VERSION}/"
if [ -f "icons/gdrive-linux.png" ]; then
    cp "icons/gdrive-linux.png" "gdrive-linux-${ARCH_VERSION}/gdrive-linux.png"
fi
# Create flat tarball by cd-ing into the dir
cd "gdrive-linux-${ARCH_VERSION}"
tar czf "../gdrive-linux-${ARCH_VERSION}.tar.gz" .
cd ..
rm -rf "gdrive-linux-${ARCH_VERSION}"

# Build
makepkg -c --noconfirm

# Copy result — ensure output dir exists and is writable
mkdir -p "${OUTPUT_DIR}"
cp gdrive-linux-*.pkg.tar.zst "${OUTPUT_DIR}/"
rm -rf "${PKGDIR}" PKGBUILD gdrive-linux-*.tar.gz

echo "Done: $(ls ${OUTPUT_DIR}/gdrive-linux-*.pkg.tar.zst)"
