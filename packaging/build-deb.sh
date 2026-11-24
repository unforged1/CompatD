#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
# Build a .deb of CompatD with dpkg-deb (no debhelper needed).
# Usage: packaging/build-deb.sh            -> dist/compatd_<version>_all.deb
set -eu

top="$(cd "$(dirname "$0")/.." && pwd)"
version="$(python3 -c "import sys; sys.path.insert(0, '$top'); import compatd; print(compatd.__version__)")"
build="$top/build/deb"
pkgroot="$build/compatd_${version}_all"

rm -rf "${build:?}"
mkdir -p "$pkgroot/DEBIAN" \
         "$pkgroot/usr/bin" \
         "$pkgroot/usr/lib/compatd" \
         "$pkgroot/usr/share/applications" \
         "$pkgroot/usr/share/icons/hicolor/scalable/apps" \
         "$pkgroot/usr/share/doc/compatd" \
         "$top/dist"

# Python package (without bytecode caches)
(cd "$top" && find compatd -name '*.py' -print | while read -r f; do
    install -D -m 0644 "$f" "$pkgroot/usr/lib/compatd/$f"
done)

install -m 0755 "$top/bin/compatd" "$pkgroot/usr/bin/compatd"
install -m 0644 "$top/data/compatd.desktop" "$pkgroot/usr/share/applications/compatd.desktop"
install -m 0644 "$top/data/compatd.svg" "$pkgroot/usr/share/icons/hicolor/scalable/apps/compatd.svg"
install -D -m 0644 "$top/data/gplv3.svg" "$pkgroot/usr/share/compatd/gplv3.svg"
install -m 0644 "$top/README.md" "$pkgroot/usr/share/doc/compatd/README.md"
install -m 0644 "$top/CHANGELOG.md" "$pkgroot/usr/share/doc/compatd/CHANGELOG.md"
cat > "$pkgroot/usr/share/doc/compatd/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: CompatD

Files: *
Copyright: 2026 unforged1
License: GPL-3+
 On Debian systems the full text is in /usr/share/common-licenses/GPL-3.

Files: usr/share/compatd/gplv3.svg
Copyright: Free Software Foundation
License: public-domain
 The GPLv3 logo is in the public domain. It is a trademark of the
 Free Software Foundation.
EOF

size="$(du -sk "$pkgroot" | cut -f1)"
cat > "$pkgroot/DEBIAN/control" <<EOF
Package: compatd
Version: $version
Section: admin
Priority: optional
Architecture: all
Installed-Size: $size
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-3.0, pciutils, usbutils, kmod, pkexec | policykit-1
Recommends: apt-file
Suggests: nvidia-detect, mokutil, dkms
Maintainer: CompatD developers <unforged.crl@gmail.com>
Description: driver and compatibility manager for Debian-based systems
 CompatD detects the drivers in use for each device, recommends and installs
 missing drivers and firmware, and scans the system or any directory for
 compatibility faults (missing libraries, wrong architecture, too-new glibc,
 foreign packages, Windows/macOS programs, broken scripts) with suggested
 replacements and one-click fixes. Its theme follows the distribution:
 Kali, Debian, MX Linux, LMDE or a neutral theme for others.
EOF

cat > "$pkgroot/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
fi
exit 0
EOF
chmod 0755 "$pkgroot/DEBIAN/postinst"

fakeroot_cmd=""
command -v fakeroot >/dev/null 2>&1 && fakeroot_cmd="fakeroot"
$fakeroot_cmd dpkg-deb --root-owner-group --build "$pkgroot" "$top/dist/compatd_${version}_all.deb" 2>/dev/null \
  || $fakeroot_cmd dpkg-deb --build "$pkgroot" "$top/dist/compatd_${version}_all.deb"
echo "Built $top/dist/compatd_${version}_all.deb"
