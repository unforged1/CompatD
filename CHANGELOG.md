# Changelog

## 0.1.0 (unreleased, testing)
First pre-release.

- Full-window GTK 3 interface with Overview, Drivers, Compatibility, Options and Activity pages.
- The theme follows the distribution (Kali, Debian, MX Linux, LMDE, other). Every default theme is dark. You can override it and force dark or light mode.
- Hardware detection from lspci and sysfs, showing the driver in use and the available kernel modules.
- Driver and firmware recommendations, installed through pkexec. Simulate (apt-get -s) and copy-commands modes are included.
- Device compatibility checks: unbound devices, missing firmware, repository mix-ups, dpkg health, DKMS, Secure Boot and Wayland.
- Directory scanner with a built-in ELF parser. It finds wrong architectures, missing libraries, too-new glibc, foreign loaders, AppImage FUSE problems, kernel module vermagic mismatches, script interpreter problems, Python 2 code, broken launchers and services, .deb dependency problems, foreign package formats, and Windows/macOS programs with native alternatives.
- Markdown and JSON report export, a CLI (`info`, `drivers`, `system`, `scan`) and a .deb builder.
