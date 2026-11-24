"""Overview page: system data at a glance plus quick actions."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .. import hardware  # noqa: E402
from ..distro import FAMILY_LABELS  # noqa: E402
from . import widgets as w  # noqa: E402


class OverviewPage(Gtk.ScrolledWindow):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = w.page_box()
        self.add(box)
        # Paint the scroller and its viewport too, so no unthemed strip shows around the page.
        w.add_class(self, "cd-page")
        viewport = self.get_child()
        w.add_class(viewport, "cd-page")
        viewport.set_shadow_type(Gtk.ShadowType.NONE)
        d = win.distro
        box.pack_start(w.page_header("System overview", "What CompatD found on this device. Press F5 to refresh, F11 for fullscreen."), False, False, 0)

        self.banner = Gtk.Box(spacing=12)
        w.add_class(self.banner, "cd-banner")
        self.banner_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.DND)
        self.banner_text = w.label("Detecting hardware…", wrap=True)
        self.banner.pack_start(self.banner_icon, False, False, 0)
        self.banner.pack_start(self.banner_text, True, True, 0)
        box.pack_start(self.banner, False, False, 0)

        # Stats
        stats = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.st_devices = w.InfoCard("Devices detected", "…", "cd-stat", "drive-harddisk-symbolic")
        self.st_nodriver = w.InfoCard("Without a driver", "…", "cd-stat", "dialog-warning-symbolic")
        self.st_updates = w.InfoCard("Driver packages to install", "…", "cd-stat", "software-update-available-symbolic")
        self.st_faults = w.InfoCard("Compatibility faults", "not scanned", "cd-stat", "system-search-symbolic")
        for i, c in enumerate((self.st_devices, self.st_nodriver, self.st_updates, self.st_faults)):
            stats.attach(c, i, 0, 1, 1)
        box.pack_start(stats, False, False, 0)

        # Quick actions
        actions = Gtk.Box(spacing=10)
        actions.pack_start(w.button("Review drivers", "video-display-symbolic", True, on_click=lambda: win.go("drivers")), False, False, 0)
        actions.pack_start(w.button("Check this device", "computer-symbolic", on_click=self._check_device), False, False, 0)
        actions.pack_start(w.button("Scan a folder…", "folder-open-symbolic", on_click=self._scan_folder), False, False, 0)
        box.pack_start(actions, False, False, 0)

        # System data
        box.pack_start(w.label("SYSTEM", "cd-section"), False, False, 0)
        summary = hardware.summary()
        cards = [
            ("Distribution", d.pretty_name, "start-here-symbolic"),
            ("Detected family", "%s%s" % (FAMILY_LABELS.get(d.family), "" if d.debian_based else " (not Debian-based!)"), "emblem-system-symbolic"),
            ("Debian base", d.debian_version or "n/a", "package-x-generic-symbolic"),
            ("Kernel", summary["kernel"], "system-run-symbolic"),
            ("Architecture", "%s (%s cores)" % (summary["arch"], summary["cores"]), "applications-engineering-symbolic"),
            ("Processor", summary["cpu"], "computer-symbolic"),
            ("Memory", summary["memory"], "media-flash-symbolic"),
            ("Firmware", "%s · Secure Boot %s" % (summary["boot"], summary["secure_boot"]), "security-high-symbolic"),
            ("Session", "%s · %s" % (summary["desktop"], summary["session"]), "video-display-symbolic"),
        ]
        grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        for i, (title, value, icon) in enumerate(cards):
            grid.attach(w.InfoCard(title, value, icon=icon), i % 3, i // 3, 1, 1)
        box.pack_start(grid, False, False, 0)

        box.pack_start(w.label("GRAPHICS & NETWORK", "cd-section"), False, False, 0)
        self.hw_grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        box.pack_start(self.hw_grid, False, False, 0)

    def _check_device(self) -> None:
        self.win.go("compat")
        self.win.compat.start_system_scan()

    def _scan_folder(self) -> None:
        self.win.go("compat")
        self.win.compat.choose_folder_and_scan()

    def update_hardware(self, devices, recs) -> None:
        important = [d for d in devices if hardware.is_important(d)]
        nodriver = [d for d in important if not d.driver]
        to_install = sorted({p for r in recs if r.recommended for p in r.missing})
        self.st_devices.set_value(str(len(devices)))
        self.st_nodriver.set_value(str(len(nodriver)), "cd-stat-critical" if nodriver else "cd-stat-ok")
        self.st_updates.set_value(str(len(to_install)), "cd-stat-warning" if to_install else "cd-stat-ok")

        if nodriver:
            names = ", ".join(d.name for d in nodriver[:3])
            self.banner_icon.set_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.DND)
            self.banner_text.set_text("%d device(s) have no working driver: %s. Open Drivers to fix this." % (len(nodriver), names))
        elif to_install:
            self.banner_icon.set_from_icon_name("software-update-available-symbolic", Gtk.IconSize.DND)
            self.banner_text.set_text("All devices have drivers. %d recommended package(s) can improve support." % len(to_install))
        else:
            w.set_icon(self.banner_icon, "emblem-ok-symbolic", Gtk.IconSize.DND)
            self.banner_text.set_text("All detected devices have drivers and the recommended packages are installed.")

        for child in self.hw_grid.get_children():
            self.hw_grid.remove(child)
        shown = [d for d in devices if d.bus == "pci" and d.class_code[:2] in ("03", "02", "0d")]
        shown += [d for d in devices if d.bus == "usb" and d.class_code == "e0"]
        for i, d in enumerate(shown):
            card = w.InfoCard(d.class_name, d.label, icon="video-display-symbolic" if d.class_code.startswith("03") else "network-wireless-symbolic")
            drv = w.pill("driver: %s" % (d.driver or "none"), "ok" if d.driver else "critical")
            card.pack_start(drv, False, False, 0)
            self.hw_grid.attach(card, i % 3, i // 3, 1, 1)
        self.hw_grid.show_all()

    def update_faults(self, findings) -> None:
        crit = sum(1 for f in findings if f.severity == "critical")
        warn = sum(1 for f in findings if f.severity == "warning")
        self.st_faults.set_value("%d critical · %d warn" % (crit, warn), "cd-stat-critical" if crit else ("cd-stat-warning" if warn else "cd-stat-ok"))
