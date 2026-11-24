# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Main application window."""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__, __app_name__, __copyright__, __version__, data_file, distro, license_text, themes  # noqa: E402
from ..actions import Runner  # noqa: E402
from ..config import Config  # noqa: E402
from ..models import Action  # noqa: E402
from ..sysutil import is_root, shell_join, which  # noqa: E402
from . import widgets as w  # noqa: E402

PAGES = [
    ("overview", "Overview", "computer-symbolic"),
    ("drivers", "Drivers", "video-display-symbolic"),
    ("compat", "Compatibility", "system-search-symbolic"),
    ("options", "Options", "preferences-system-symbolic"),
    ("activity", "Activity", "utilities-terminal-symbolic"),
]


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, theme_override: Optional[str], fullscreen: bool) -> None:
        super().__init__(application=app, title=__app_name__)
        self.config = Config.load()
        if theme_override:
            self.config.theme = theme_override
        self.distro = distro.detect()
        self.devices: list = []
        self.recommendations: list = []
        self._busy_runner: Optional[Runner] = None
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.get_style_context().add_class("compatd")
        self.set_icon_name("compatd")

        self._build()
        self.apply_theme()
        self._size_to_screen()
        if fullscreen or self.config.start_fullscreen:
            self.fullscreen()
        self.connect("key-press-event", self._on_key)
        self.connect("window-state-event", self._on_state)
        self._is_fullscreen = False

    # -- layout ---------------------------------------------------------------------------
    def _size_to_screen(self) -> None:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0) if display else None
        if monitor:
            geo = monitor.get_workarea()
            self.set_default_size(geo.width, geo.height)
        else:
            self.set_default_size(1280, 800)
        self.maximize()

    def _build(self) -> None:
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title(__app_name__)
        hb.set_subtitle(self.distro.pretty_name)
        self.badge = w.label("", "cd-distro-badge", xalign=0.5)
        self.badge.set_valign(Gtk.Align.CENTER)
        hb.pack_start(self.badge)

        menu = Gio.Menu()
        menu.append("Refresh everything", "win.refresh")
        menu.append("Toggle fullscreen (F11)", "win.fullscreen")
        menu.append("License", "win.license")
        menu.append("About CompatD", "win.about")
        menu.append("Quit", "app.quit")
        mb = Gtk.MenuButton()
        mb.set_menu_model(menu)
        mb.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        hb.pack_end(mb)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Re-detect hardware and drivers")
        refresh.connect("clicked", lambda *_: self.refresh_all())
        hb.pack_end(refresh)
        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)
        self.set_titlebar(hb)

        for name, cb in (("refresh", lambda *_: self.refresh_all()), ("fullscreen", lambda *_: self.toggle_fullscreen()), ("about", lambda *_: self.show_about()), ("license", lambda *_: self.show_license())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(root)

        # Sidebar
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        w.add_class(side, "cd-sidebar")
        side.set_size_request(230, -1)
        side.pack_start(w.label(__app_name__, "cd-brand"), False, False, 0)
        side.pack_start(w.label("v%s · compatibility manager" % __version__, "cd-version"), False, False, 0)
        self.nav = Gtk.ListBox()
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        for key, title, icon in PAGES:
            row = Gtk.ListBoxRow()
            row.page_key = key
            box = Gtk.Box(spacing=12)
            box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
            box.pack_start(w.label(title), True, True, 0)
            row.counter = w.pill("", "neutral")
            row.counter.set_no_show_all(True)
            box.pack_end(row.counter, False, False, 0)
            row.add(box)
            self.nav.add(row)
        self.nav.connect("row-selected", self._on_nav)
        side.pack_start(self.nav, False, False, 0)
        side.pack_start(Gtk.Box(), True, True, 0)
        self.root_note = w.label(
            "Running as root" if is_root() else ("Privileged actions use pkexec" if which("pkexec") else "pkexec not found: privileged actions will fail"),
            "cd-version",
            wrap=True,
        )
        side.pack_end(self.root_note, False, False, 0)
        root.pack_start(side, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        root.pack_start(self.stack, True, True, 0)

        from .activity_page import ActivityPage
        from .compat_page import CompatPage
        from .drivers_page import DriversPage
        from .options_page import OptionsPage
        from .overview_page import OverviewPage

        self.activity = ActivityPage(self)
        self.overview = OverviewPage(self)
        self.drivers = DriversPage(self)
        self.compat = CompatPage(self)
        self.options = OptionsPage(self)
        for key, page in (
            ("overview", self.overview),
            ("drivers", self.drivers),
            ("compat", self.compat),
            ("options", self.options),
            ("activity", self.activity),
        ):
            self.stack.add_named(page, key)
        self.nav.select_row(self.nav.get_row_at_index(0))

    # -- navigation / theme ------------------------------------------------------------------
    def _on_nav(self, _lb, row) -> None:
        if row is not None:
            self.stack.set_visible_child_name(row.page_key)

    def go(self, key: str) -> None:
        for i, (k, _t, _i) in enumerate(PAGES):
            if k == key:
                self.nav.select_row(self.nav.get_row_at_index(i))

    def set_counter(self, key: str, text: str, kind: str = "neutral") -> None:
        for i, (k, _t, _i) in enumerate(PAGES):
            if k == key:
                pill = self.nav.get_row_at_index(i).counter
                ctx = pill.get_style_context()
                for c in ("cd-pill-critical", "cd-pill-warning", "cd-pill-info", "cd-pill-ok", "cd-pill-neutral"):
                    ctx.remove_class(c)
                ctx.add_class("cd-pill-%s" % kind)
                pill.set_text(text)
                pill.set_visible(bool(text))

    def theme_family(self) -> str:
        return self.distro.family if self.config.theme == "auto" else self.config.theme

    def apply_theme(self) -> None:
        fam = self.theme_family()
        pal = themes.palette(fam, self.config.dark_mode)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", bool(pal["dark"]))
        self._css.load_from_data(themes.css(fam, self.config.dark_mode).encode("utf-8"))
        self.badge.set_text(distro.FAMILY_LABELS.get(fam, "Other") + (" theme" if self.config.theme != "auto" else ""))
        self.sev_colors = themes.severity_colors(bool(pal["dark"]))
        if hasattr(self, "compat"):
            self.compat.recolor()

    # -- window state ------------------------------------------------------------------------
    def _on_key(self, _w, event) -> bool:
        if event.keyval == Gdk.KEY_F11:
            self.toggle_fullscreen()
            return True
        if event.keyval == Gdk.KEY_F5:
            self.refresh_all()
            return True
        return False

    def _on_state(self, _w, event) -> None:
        self._is_fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()

    def show_about(self) -> None:
        dlg = Gtk.AboutDialog(transient_for=self, modal=True)
        dlg.set_program_name(__app_name__)
        dlg.set_version(__version__ + " (pre-release)")
        dlg.set_logo_icon_name("compatd")
        dlg.set_comments(
            "Driver and compatibility manager for Debian-based distributions.\n"
            "Detected: %s (%s theme)" % (self.distro.pretty_name, distro.FAMILY_LABELS.get(self.theme_family()))
        )
        dlg.set_copyright(__copyright__)
        dlg.set_license_type(Gtk.License.GPL_3_0)
        dlg.run()
        dlg.destroy()

    def show_license(self) -> None:
        dlg = Gtk.Dialog(title="License", transient_for=self, modal=True)
        dlg.get_style_context().add_class("compatd")  # use the app theme, not the system one
        dlg.add_button("Read online", 1)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.set_default_size(min(760, self.get_allocated_width() - 80), min(720, self.get_allocated_height() - 80))
        box = dlg.get_content_area()
        box.set_spacing(10)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + side)(18)

        top = Gtk.Box(spacing=18)
        logo_path = data_file("gplv3.svg")
        if logo_path:
            try:
                from gi.repository import GdkPixbuf

                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(logo_path, 180, -1, True)
                frame = Gtk.EventBox()  # white card: the logo's dark red has poor contrast on dark themes
                w.add_class(frame, "cd-logo-card")
                img = Gtk.Image.new_from_pixbuf(pix)
                for side in ("top", "bottom", "start", "end"):
                    getattr(img, "set_margin_" + side)(12)
                frame.add(img)
                frame.set_valign(Gtk.Align.CENTER)
                top.pack_start(frame, False, False, 0)
            except GLib.Error:
                pass
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info.set_valign(Gtk.Align.CENTER)
        info.pack_start(w.label("%s %s" % (__app_name__, __version__), "cd-page-title"), False, False, 0)
        info.pack_start(w.label(__copyright__, selectable=True), False, False, 0)
        info.pack_start(
            w.label(
                "This program is free software: you can redistribute it and/or modify it under the terms of the "
                "GNU General Public License as published by the Free Software Foundation, either version 3 of the "
                "License, or (at your option) any later version.\n\n"
                "This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even "
                "the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.",
                "cd-muted",
                wrap=True,
            ),
            False,
            False,
            4,
        )
        top.pack_start(info, True, True, 0)
        box.pack_start(top, False, False, 0)

        text = license_text() or "The full license text was not found. Read it at https://www.gnu.org/licenses/gpl-3.0.html"
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(text)
        for side in ("top", "bottom", "left", "right"):
            getattr(view, "set_%s_margin" % side)(10)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_propagate_natural_height(False)
        sw.set_min_content_height(240)
        w.add_class(sw, "cd-framed")
        sw.add(view)
        box.pack_start(sw, True, True, 0)
        dlg.show_all()
        while dlg.run() == 1:
            Gtk.show_uri_on_window(dlg, "https://www.gnu.org/licenses/gpl-3.0.html", Gdk.CURRENT_TIME)
        dlg.destroy()

    # -- busy state / refresh ------------------------------------------------------------------
    _busy = 0

    def busy(self, on: bool) -> None:
        self._busy += 1 if on else -1
        self._busy = max(self._busy, 0)
        if self._busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def refresh_all(self) -> None:
        from .. import packages

        packages.clear_cache()
        self.drivers.refresh()

    def on_hardware_loaded(self, devices, recs) -> None:
        self.devices = devices
        self.recommendations = recs
        self.overview.update_hardware(devices, recs)

    # -- actions ------------------------------------------------------------------------------
    def log(self, line: str) -> None:
        self.activity.append(line)

    def confirm_and_run(self, action: Action, on_done: Optional[Callable[[int], None]] = None) -> None:
        if self._busy_runner is not None:
            self.message("Another action is still running", "Wait for it to finish (see Activity).")
            return
        dry = self.config.dry_run
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE)
        dlg.set_markup("<b>%s</b>" % GLib.markup_escape_text(action.label))
        cmds = "\n".join(shell_join(c) for c in action.commands)
        note = ""
        if dry:
            note = "\n\nDry-run mode is ON: apt installs are simulated, nothing is changed."
        elif action.needs_root and not is_root():
            note = "\n\nThese commands run as root; you will be asked to authenticate."
        dlg.format_secondary_text("CompatD will run:\n\n%s%s" % (cmds, note))
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Simulate" if dry else "Run", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        self.run_action(action, on_done, dry)

    def run_action(self, action: Action, on_done: Optional[Callable[[int], None]], dry: bool) -> None:
        self.busy(True)
        self.set_counter("activity", "running", "info")
        self.go("activity")

        def done(rc: int) -> None:
            def finish():
                self._busy_runner = None
                self.busy(False)
                self.set_counter("activity", "ok" if rc == 0 else "failed", "ok" if rc == 0 else "critical")
                if on_done:
                    on_done(rc)
                if rc == 0 and not dry and action.needs_root:
                    from .. import packages
                    from ..libindex import get_index

                    packages.clear_cache()
                    get_index(refresh=True)
                    self.drivers.refresh()

            w.ui(finish)

        self._busy_runner = Runner(action, lambda line: w.ui(self.log, line), done, dry_run=dry)
        self._busy_runner.start()

    def message(self, title: str, text: str, kind=Gtk.MessageType.INFO) -> None:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=kind, buttons=Gtk.ButtonsType.CLOSE, text=title)
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()


class CompatDApp(Gtk.Application):
    def __init__(self, theme: Optional[str], fullscreen: bool) -> None:
        super().__init__(application_id=__app_id__, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.theme = theme
        self.start_fullscreen = fullscreen
        self.window: Optional[MainWindow] = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self) -> None:
        if self.window is None:
            GLib.set_application_name(__app_name__)
            self.window = MainWindow(self, self.theme, self.start_fullscreen)
            self.window.show_all()
            self.window.drivers.refresh()
            shot = os.environ.get("COMPATD_SCREENSHOT")
            if shot:
                GLib.timeout_add(int(os.environ.get("COMPATD_SCREENSHOT_DELAY", "4000")), self._screenshot, shot)
        self.window.present()

    def _snap_license(self, path: str) -> None:
        import cairo

        for top in Gtk.Window.list_toplevels():
            if isinstance(top, Gtk.Dialog) and top.get_title() == "License" and top.get_visible():
                a = top.get_allocation()
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, a.width, a.height)
                top.draw(cairo.Context(surface))
                surface.write_to_png("%s-license.png" % path)
                top.response(Gtk.ResponseType.CLOSE)

    def _screenshot(self, path: str) -> bool:
        """Testing aid: render the window to PNG files and quit (COMPATD_SCREENSHOT=/path/prefix)."""
        import cairo

        win = self.window
        pages = os.environ.get("COMPATD_SCREENSHOT_PAGES", "overview").split(",")

        def snap(i: int) -> bool:
            if i >= len(pages):
                self.quit()
                return False
            page = pages[i]
            if page.startswith("theme:"):
                win.config.theme = page.split(":", 1)[1]
                win.apply_theme()
                page = "overview"
            elif page == "license":
                GLib.timeout_add(300, lambda: self._snap_license(path) and False)
                self.window.show_license()
                GLib.idle_add(lambda: snap(i + 1) and False)
                return False
            elif page == "system-scan":
                win.go("compat")
                win.compat.start_system_scan()
                GLib.timeout_add(3000, lambda: (_draw(page), snap(i + 1))[0] and False)
                return False
            else:
                win.go(page)
            GLib.timeout_add(700, lambda: (_draw(page + str(i)), snap(i + 1))[0] and False)
            return False

        def _draw(name: str) -> bool:
            alloc = win.get_allocation()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
            win.draw(cairo.Context(surface))
            surface.write_to_png("%s-%s.png" % (path, name.replace(":", "_")))
            return True

        snap(0)
        return False


def run_gui(theme: Optional[str] = None, fullscreen: bool = False) -> int:
    app = CompatDApp(theme, fullscreen)
    return app.run([sys.argv[0]])
