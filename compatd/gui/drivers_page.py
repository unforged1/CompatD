"""Drivers page: current drivers per device and installable recommendations."""
from __future__ import annotations

from typing import List

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from .. import drivers, hardware  # noqa: E402
from ..models import Action, Recommendation  # noqa: E402
from ..sysutil import shell_join  # noqa: E402
from . import widgets as w  # noqa: E402

STATUS_PILL = {
    "installed": ("installed", "ok"),
    "available": ("available", "warning"),
    "partial": ("partly installed", "info"),
    "unavailable": ("not in your repos", "neutral"),
}


class DriversPage(Gtk.Box):
    def __init__(self, win) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.win = win
        self.set_border_width(24)
        w.add_class(self, "cd-page")
        self.recs: List[Recommendation] = []
        self.checks = {}

        top = Gtk.Box(spacing=12)
        top.pack_start(w.page_header("Drivers", "Devices on this machine, the driver each one uses, and packages that improve support."), True, True, 0)
        self.show_all_devices = Gtk.CheckButton(label="Show every device")
        self.show_all_devices.connect("toggled", lambda *_: self._fill_devices())
        top.pack_end(self.show_all_devices, False, False, 0)
        self.pack_start(top, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_wide_handle(True)
        self.pack_start(paned, True, True, 0)

        # Devices table
        self.dev_store = Gtk.ListStore(str, str, str, str, str, str, str, str, str)
        tv = Gtk.TreeView(model=self.dev_store)
        tv.set_enable_search(True)
        tv.set_search_column(4)
        cols = [("Bus", 0), ("Slot", 1), ("Type", 2), ("Vendor", 3), ("Device", 4), ("IDs", 5), ("Driver in use", 6), ("Kernel modules", 7)]
        for title, idx in cols:
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, r, text=idx)
            if idx == 6:
                col.add_attribute(r, "foreground", 8)
                r.set_property("weight", Pango.Weight.BOLD)
            col.set_resizable(True)
            col.set_sort_column_id(idx)
            col.set_expand(idx in (3, 4))
            col.set_min_width({1: 120, 2: 180, 5: 90, 6: 120}.get(idx, 40))
            tv.append_column(col)
        dev_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        dev_box.pack_start(w.label("DETECTED DEVICES", "cd-section"), False, False, 0)
        dev_box.pack_start(w.scrolled(tv), True, True, 0)
        paned.pack1(dev_box, True, False)

        # Recommendations
        rec_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        rec_box.set_margin_top(10)
        bar = Gtk.Box(spacing=8)
        bar.pack_start(w.label("RECOMMENDED DRIVERS & FIRMWARE", "cd-section"), True, True, 0)
        self.show_optional = Gtk.CheckButton(label="Optional")
        self.show_optional.set_active(win.config.show_optional)
        self.show_optional.connect("toggled", self._toggle_filter)
        self.show_installed = Gtk.CheckButton(label="Already installed")
        self.show_installed.set_active(win.config.show_installed)
        self.show_installed.connect("toggled", self._toggle_filter)
        bar.pack_start(self.show_optional, False, False, 0)
        bar.pack_start(self.show_installed, False, False, 0)
        rec_box.pack_start(bar, False, False, 0)

        self.rec_list = Gtk.ListBox()
        self.rec_list.set_selection_mode(Gtk.SelectionMode.NONE)
        w.add_class(self.rec_list, "cd-list")
        sw = w.scrolled(self.rec_list, framed=False)
        rec_box.pack_start(sw, True, True, 0)

        actions = Gtk.Box(spacing=8)
        self.summary = w.label("", "cd-muted")
        actions.pack_start(self.summary, True, True, 0)
        actions.pack_start(w.button("Select recommended", "object-select-symbolic", on_click=self.select_recommended), False, False, 0)
        actions.pack_start(w.button("Copy commands", "edit-copy-symbolic", on_click=self.copy_commands), False, False, 0)
        actions.pack_start(w.button("Simulate", "system-run-symbolic", tooltip="Run apt-get in simulation mode (no changes, no password)", on_click=self.simulate), False, False, 0)
        self.install_btn = w.button("Install selected", "document-save-symbolic", primary=True, on_click=self.install)
        actions.pack_start(self.install_btn, False, False, 0)
        rec_box.pack_start(actions, False, False, 0)
        paned.pack2(rec_box, True, False)
        paned.set_position(300)

        self.devices = []

    # -- data --------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.win.busy(True)
        self.summary.set_text("Detecting hardware and checking apt…")
        win = self.win

        def work():
            devices = hardware.all_devices()
            recs = drivers.recommend(devices, win.distro)
            return devices, recs

        def done(res, err):
            win.busy(False)
            if err:
                self.summary.set_text("Detection failed: %s" % err)
                win.log("Driver detection failed: %r" % err)
                return
            self.devices, self.recs = res
            self._fill_devices()
            self._fill_recs()
            win.on_hardware_loaded(self.devices, self.recs)

        w.run_async(work, done)

    def _fill_devices(self) -> None:
        self.dev_store.clear()
        colors = self.win.sev_colors
        ordered = sorted(self.devices, key=lambda d: (bool(d.driver) or not hardware.is_important(d), d.bus, d.slot))
        for d in ordered:
            important = hardware.is_important(d)
            if not self.show_all_devices.get_active() and not important:
                continue
            if d.driver:
                color = colors["ok"]
            elif important:
                color = colors["critical"]
            else:
                color = colors["info"]
            self.dev_store.append(
                [
                    d.bus.upper(),
                    d.slot,
                    d.class_name,
                    d.vendor,
                    d.name,
                    d.ids,
                    d.driver or ("NONE" if important else "-"),
                    ", ".join(d.modules),
                    color,
                ]
            )

    def _toggle_filter(self, *_):
        self.win.config.show_optional = self.show_optional.get_active()
        self.win.config.show_installed = self.show_installed.get_active()
        self.win.config.save()
        self._fill_recs()

    def _fill_recs(self) -> None:
        for child in self.rec_list.get_children():
            self.rec_list.remove(child)
        old = {k: c.get_active() for k, c in self.checks.items()}
        self.checks = {}
        shown = 0
        for r in self.recs:
            if r.optional and not self.show_optional.get_active():
                continue
            if r.status == "installed" and not self.show_installed.get_active():
                continue
            shown += 1
            self.rec_list.add(self._rec_row(r, old.get(r.key, r.recommended)))
        if not shown:
            row = Gtk.ListBoxRow()
            row.add(w.label("Nothing to recommend for this hardware.", "cd-muted"))
            self.rec_list.add(row)
        self.rec_list.show_all()
        self._update_summary()

    def _rec_row(self, r: Recommendation, checked: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        outer = Gtk.Box(spacing=12)
        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.START)
        installable = bool(r.missing or r.post_commands)
        check.set_sensitive(installable)
        check.set_active(checked and installable)
        check.connect("toggled", lambda *_: self._update_summary())
        self.checks[r.key] = check
        outer.pack_start(check, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box(spacing=8)
        title = w.label(r.title)
        title.set_markup("<b>%s</b>" % _esc(r.title))
        head.pack_start(title, False, False, 0)
        text, kind = STATUS_PILL.get(r.status, (r.status, "neutral"))
        head.pack_start(w.pill(text, kind), False, False, 0)
        if r.optional:
            head.pack_start(w.pill("optional", "neutral"), False, False, 0)
        elif r.recommended:
            head.pack_start(w.pill("recommended", "info"), False, False, 0)
        body.pack_start(head, False, False, 0)
        body.pack_start(w.label(r.reason, wrap=True), False, False, 0)
        if r.devices:
            body.pack_start(w.label("Devices: " + "; ".join(r.devices), "cd-muted", wrap=True), False, False, 0)
        pk = []
        for p in r.packages:
            pk.append(("<b>%s</b> (to install)" % _esc(p)) if p in r.missing else "%s ✓" % _esc(p))
        for p in r.unavailable:
            pk.append("<s>%s</s> (unavailable)" % _esc(p))
        if pk:
            body.pack_start(w.label("Packages: " + ", ".join(pk), "cd-muted", wrap=True, markup=True), False, False, 0)
        for c in r.post_commands:
            body.pack_start(w.label("Runs: " + shell_join(c), "cd-muted", wrap=True), False, False, 0)
        for n in r.notes:
            body.pack_start(w.label("• " + n, "cd-muted", wrap=True), False, False, 0)
        outer.pack_start(body, True, True, 0)
        row.add(outer)
        return row

    def selected(self) -> List[Recommendation]:
        return [r for r in self.recs if r.key in self.checks and self.checks[r.key].get_active()]

    def _update_summary(self) -> None:
        sel = self.selected()
        pkgs = sorted({p for r in sel for p in r.missing})
        self.summary.set_text("%d selected · %d package(s) to install" % (len(sel), len(pkgs)) if sel else "Nothing selected")
        self.install_btn.set_sensitive(bool(sel))
        missing_total = len({p for r in self.recs if r.recommended for p in r.missing})
        self.win.set_counter("drivers", str(missing_total) if missing_total else "", "warning")

    def select_recommended(self) -> None:
        for r in self.recs:
            c = self.checks.get(r.key)
            if c and c.get_sensitive():
                c.set_active(r.recommended)

    def _action(self) -> Action:
        sel = self.selected()
        cmds = drivers.install_commands(sel)
        pkgs = [p for r in sel for p in r.missing]
        label = "Install drivers: %s" % (", ".join(r.title for r in sel)[:120])
        return Action(label, cmds, needs_root=True, packages=pkgs)

    def install(self) -> None:
        if not self.selected():
            return
        need_reboot = any(r.key == "nvidia" for r in self.selected())
        self.win.confirm_and_run(
            self._action(),
            lambda rc: rc == 0 and need_reboot and not self.win.config.dry_run and self.win.message("Reboot required", "The NVIDIA driver will be active after a reboot."),
        )

    def simulate(self) -> None:
        if not self.selected():
            self.win.message("Nothing selected", "Select at least one recommendation to simulate.")
            return
        self.win.run_action(self._action(), None, dry=True)

    def copy_commands(self) -> None:
        cmds = drivers.install_commands(self.selected() or [r for r in self.recs if r.recommended])
        text = "\n".join("sudo " + shell_join(c) for c in cmds) or "# nothing to install"
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.win.log("Copied to clipboard:\n" + text)
        self.summary.set_text("Commands copied to the clipboard")


def _esc(s: str) -> str:
    from gi.repository import GLib

    return GLib.markup_escape_text(s)
