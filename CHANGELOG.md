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

- License dialog in the main menu with the GPLv3 logo, copyright notice and full license text. About now shows the copyright holder (unforged1).

### Fixes from LMDE 7 testing
- Repository checks now recognise Debian mirrors (any `/debian` or `/debian-security` archive), not only `*.debian.org`. A mixed-release source on LMDE's regional mirror, or a Debian mirror added to Kali, was going unreported.
- Packages from older kernels get their own finding with a "Remove old kernels" fix, instead of being lumped in with unmaintained .debs. Kernels newer than the running one are never offered for removal.
- The scanner no longer reports missing libraries for plugins inside app bundles (e.g. Firefox's `gmp-clearkey`) when the library ships higher up in the same bundle.
- Icons that the Mint-Y and current Adwaita themes lack (the Overview success banner, the Apply fix button, the Detected family card) now fall back to available ones.
