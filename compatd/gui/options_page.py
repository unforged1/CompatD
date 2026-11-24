# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Options page: theme, behaviour and scanner settings (saved automatically)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from ..config import config_path  # noqa: E402
from ..distro import FAMILY_LABELS  # noqa: E402
from . import widgets as w  # noqa: E402


class OptionsPage(Gtk.ScrolledWindow):
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
        cfg = win.config
        box.pack_start(w.page_header("Options", "Settings are saved automatically to %s" % config_path()), False, False, 0)

        # Appearance
        box.pack_start(w.label("APPEARANCE", "cd-section"), False, False, 0)
        card = self._card(box)
        theme = Gtk.ComboBoxText()
        theme.append("auto", "Automatic (detected: %s)" % FAMILY_LABELS.get(win.distro.family))
        for fam in ("kali", "debian", "mx", "lmde", "other"):
            theme.append(fam, FAMILY_LABELS[fam])
        theme.set_active_id(cfg.theme)
        theme.connect("changed", lambda c: self._set("theme", c.get_active_id(), retheme=True))
        self._row(card, "Theme", "Colour scheme follows your distribution unless overridden.", theme)
        mode = Gtk.ComboBoxText()
        for key, text in (("theme", "Theme default"), ("dark", "Always dark"), ("light", "Always light")):
            mode.append(key, text)
        mode.set_active_id(cfg.dark_mode)
        mode.connect("changed", lambda c: self._set("dark_mode", c.get_active_id(), retheme=True))
        self._row(card, "Brightness", "Force a dark or light variant of the theme.", mode)
        self._switch(card, "start_fullscreen", "Start in fullscreen", "The window always opens maximised; this goes fullscreen (F11 toggles).")

        # Actions
        box.pack_start(w.label("ACTIONS", "cd-section"), False, False, 0)
        card = self._card(box)
        self._switch(card, "dry_run", "Dry-run mode", "Simulate installs with 'apt-get -s' and only print other commands. Nothing is changed.")
        self._switch(card, "show_optional", "Show optional recommendations", "e.g. CUDA toolkit, Bluetooth tools.", on_change=lambda: self._sync_driver_filters())
        self._switch(card, "show_installed", "Show already-installed recommendations", "", on_change=lambda: self._sync_driver_filters())

        # Scanner
        box.pack_start(w.label("DIRECTORY SCANNER", "cd-section"), False, False, 0)
        card = self._card(box)
        self._switch(card, "check_elf", "Linux binaries and libraries", "Architecture, missing libraries, too-new glibc/libstdc++, AppImages, kernel modules.")
        self._switch(card, "check_scripts", "Scripts", "Missing interpreters, macOS/BSD shebangs, Windows line endings.")
        self._switch(card, "check_python", "Python sources", "Detect Python 2 code that will not run on Python 3.")
        self._switch(card, "check_launchers", "Launchers and services", ".desktop files and systemd units pointing at missing programs.")
        self._switch(card, "check_packages", "Packages", ".deb dependency check, .rpm, Snap, Flatpak, Java archives.")
        self._switch(card, "check_foreign", "Foreign-OS files", "Windows and macOS programs - suggests native replacements.")
        self._switch(card, "report_broken_links", "Broken symbolic links", "")
        self._switch(card, "skip_hidden", "Skip hidden files and folders", "")
        self._switch(card, "follow_symlinks", "Follow symbolic links", "May scan the same files twice or leave the chosen folder.")
        self._switch(card, "use_apt_file", "Use apt-file for missing libraries", "Deeper search for the package providing a library (needs apt-file with an updated cache).")
        files = Gtk.SpinButton.new_with_range(1000, 5000000, 1000)
        files.set_value(cfg.max_files)
        files.connect("value-changed", lambda s: self._set("max_files", int(s.get_value())))
        self._row(card, "Maximum files per scan", "", files)
        size = Gtk.SpinButton.new_with_range(1, 4096, 16)
        size.set_value(cfg.max_file_mb)
        size.connect("value-changed", lambda s: self._set("max_file_mb", int(s.get_value())))
        self._row(card, "Largest source file to parse (MiB)", "", size)

        reset = w.button("Restore defaults", "edit-undo-symbolic", on_click=self._reset)
        reset.set_halign(Gtk.Align.START)
        box.pack_start(reset, False, False, 0)

    def _card(self, parent: Gtk.Box) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        w.add_class(card, "cd-card")
        parent.pack_start(card, False, False, 0)
        return card

    def _row(self, card: Gtk.Box, title: str, subtitle: str, widget: Gtk.Widget) -> None:
        row = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(w.label(title), False, False, 0)
        if subtitle:
            text.pack_start(w.label(subtitle, "cd-muted", wrap=True), False, False, 0)
        row.pack_start(text, True, True, 0)
        widget.set_valign(Gtk.Align.CENTER)
        row.pack_end(widget, False, False, 0)
        card.pack_start(row, False, False, 0)

    def _switch(self, card: Gtk.Box, key: str, title: str, subtitle: str, on_change=None) -> None:
        sw = Gtk.Switch()
        sw.set_active(bool(getattr(self.win.config, key)))

        def changed(s, _p):
            self._set(key, s.get_active())
            if on_change:
                on_change()

        sw.connect("notify::active", changed)
        self._row(card, title, subtitle, sw)

    def _set(self, key: str, value, retheme: bool = False) -> None:
        setattr(self.win.config, key, value)
        self.win.config.save()
        if retheme:
            self.win.apply_theme()
            self.win.drivers._fill_devices()

    def _sync_driver_filters(self) -> None:
        d = self.win.drivers
        d.show_optional.set_active(self.win.config.show_optional)
        d.show_installed.set_active(self.win.config.show_installed)

    def _reset(self) -> None:
        from ..config import Config

        self.win.config = Config()
        self.win.config.save()
        self.win.message("Defaults restored", "Restart CompatD to reload every option.")
        self.win.apply_theme()
