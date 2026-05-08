#!/bin/bash
# Build .rpm package (Fedora/RHEL/CentOS)
set -euo pipefail

VERSION="${1:-0.0.0}"
BINARY="${2:-dist/gdrive-linux}"
OUTPUT_DIR="${3:-.}"

# RPM doesn't allow dashes in version — replace with tilde
RPM_VERSION="${VERSION//-/\~}"

echo "Building .rpm package version ${VERSION} (RPM: ${RPM_VERSION})..."

# Build RPM using rpmbuild
mkdir -p "${HOME}/rpmbuild/SOURCES"
mkdir -p "${HOME}/rpmbuild/SPECS"

# Create a tarball of the binary and icon for rpmbuild
# Directory name must match RPM_VERSION (no dashes) for %%setup -q
TARBALL="${HOME}/rpmbuild/SOURCES/gdrive-linux-${RPM_VERSION}.tar.gz"
mkdir -p "gdrive-linux-${RPM_VERSION}"
cp "${BINARY}" "gdrive-linux-${RPM_VERSION}/gdrive-linux"
if [ -f "icons/gdrive-linux.png" ]; then
    cp "icons/gdrive-linux.png" "gdrive-linux-${RPM_VERSION}/gdrive-linux.png"
fi
tar czf "${TARBALL}" "gdrive-linux-${RPM_VERSION}"
rm -rf "gdrive-linux-${RPM_VERSION}"

cat > "${HOME}/rpmbuild/SPECS/gdrive-linux.spec" << EOF
%define debug_package %{nil}

Name:       gdrive-linux
Version:    ${RPM_VERSION}
Release:    1%{?dist}
Summary:    Native Linux Google Drive two-way sync application

License:    MIT
URL:        https://github.com/nithin/gdrive-linux
Source0:    gdrive-linux-${RPM_VERSION}.tar.gz

Requires:   fuse-libs

%description
A native Linux application for two-way synchronization with Google Drive,
featuring real-time file monitoring, system tray integration, and
on-demand file content management.

%prep
%setup -q

%install
install -D -m 0755 gdrive-linux %{buildroot}%{_bindir}/gdrive-linux

# Icon
install -D -m 0644 gdrive-linux.png %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/gdrive-linux.png

# Desktop entry
install -D -m 0644 /dev/null %{buildroot}%{_datadir}/applications/gdrive-linux.desktop
cat > %{buildroot}%{_datadir}/applications/gdrive-linux.desktop << 'DESKTOP_EOF'
[Desktop Entry]
Name=gdrive-linux
Comment=Google Drive two-way sync for Linux
Exec=/usr/bin/gdrive-linux
Icon=gdrive-linux
Terminal=false
Type=Application
Categories=Utility;FileTools;
StartupNotify=true
DESKTOP_EOF

%files
%{_bindir}/gdrive-linux
%{_datadir}/applications/gdrive-linux.desktop
%{_datadir}/icons/hicolor/256x256/apps/gdrive-linux.png

%changelog
* $(date '+%a %b %d %Y') gdrive-linux <noreply@github.com> - ${VERSION}-1
- Initial release
EOF

rpmbuild -ba "${HOME}/rpmbuild/SPECS/gdrive-linux.spec"

# Copy resulting RPM
cp "${HOME}/rpmbuild/RPMS/x86_64/"gdrive-linux-*.rpm "${OUTPUT_DIR}/"

echo "Done: $(ls ${OUTPUT_DIR}/gdrive-linux-*.rpm)"
