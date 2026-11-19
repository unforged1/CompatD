"""Compatibility page: scan this device or a directory for faults and fix them."""
from __future__ import annotations

import os
import threading
from typing import List, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk, Pango  # noqa: E402

from .. import syscheck  # noqa: E402
from ..report import write_report  # noqa: E402
from ..models import SEVERITY_RANK, Finding  # noqa: E402
from ..scanner import Scanner, describe_action, fix_all_action  # noqa: E402
from . import widgets as w  # noqa: E402

COL_SEV, COL_CAT, COL_TITLE, COL_TARGET, COL_COLOR, COL_IDX, COL_RANK, COL_FIX = range(8)


class CompatPage(Gtk.Box):
    def __init__(self, win) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.win = win
        self.set_border_width(24)
        w.add_class(self, "cd-page")
        self.findings: List[Finding] = []
        self.cancel_event: Optional[threading.Event] = None
        self.current: Optional[Finding] = None
        self.scope_label = ""

        self.pack_start(
            w.page_header(
                "Compatibility",
                "Find programs, libraries, scripts, packages and devices that will not work on this system - and how to replace or fix them.",
            ),
            False,
            False,
            0,
        )

        # Scope selection
        scope = Gtk.Box(spacing=10)
        self.rb_device = Gtk.RadioButton.new_with_label(None, "This device")
        self.rb_dir = Gtk.RadioButton.new_with_label_from_widget(self.rb_device, "Directory:")
        self.folder = Gtk.FileChooserButton(title="Choose a directory to scan", action=Gtk.FileChooserAction.SELECT_FOLDER)
        start_dir = win.config.last_directory if os.path.isdir(win.config.last_directory) else os.path.expanduser("~")
        self.folder.set_filename(start_dir)
        self.folder.set_size_request(320, -1)
        self.folder.connect("file-set", lambda *_: self.rb_dir.set_active(True))
        self.scan_btn = w.button("Start scan", "system-search-symbolic", primary=True, on_click=self.start)
        self.cancel_btn = w.button("Cancel", "process-stop-symbolic", on_click=self.cancel)
        self.cancel_btn.set_sensitive(False)
        for wid in (self.rb_device, self.rb_dir, self.folder):
            scope.pack_start(wid, False, False, 0)
        scope.pack_end(self.cancel_btn, False, False, 0)
        scope.pack_end(self.scan_btn, False, False, 0)
        self.pack_start(scope, False, False, 0)

        prog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.progress = Gtk.ProgressBar()
        self.status = w.label("Choose what to scan and press Start scan.", "cd-muted")
        prog.pack_start(self.progress, False, False, 0)
        prog.pack_start(self.status, False, False, 0)
        self.pack_start(prog, False, False, 0)

        # Filters
        filt = Gtk.Box(spacing=8)
        self.sev_combo = Gtk.ComboBoxText()
        for key, text in (("all", "All severities"), ("critical", "Critical"), ("warning", "Warnings"), ("info", "Info")):
            self.sev_combo.append(key, text)
        self.sev_combo.set_active_id("all")
        self.sev_combo.connect("changed", lambda *_: self.filter.refilter())
        self.fixable = Gtk.CheckButton(label="Only fixable")
        self.fixable.connect("toggled", lambda *_: self.filter.refilter())
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Filter results…")
        self.search.connect("search-changed", lambda *_: self.filter.refilter())
        self.count_crit = w.pill("0 critical", "critical")
        self.count_warn = w.pill("0 warning", "warning")
        self.count_info = w.pill("0 info", "info")
        for wid in (self.sev_combo, self.fixable, self.search):
            filt.pack_start(wid, False, False, 0)
        for wid in (self.count_info, self.count_warn, self.count_crit):
            filt.pack_end(wid, False, False, 0)
        self.pack_start(filt, False, False, 0)

        # Results + details
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        self.pack_start(paned, True, True, 0)

        self.store = Gtk.ListStore(str, str, str, str, str, int, int, str)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.sorted = Gtk.TreeModelSort(model=self.filter)
        self.sorted.set_sort_column_id(COL_RANK, Gtk.SortType.ASCENDING)
        self.tree = Gtk.TreeView(model=self.sorted)
        for title, idx, expand in (("Severity", COL_SEV, False), ("Category", COL_CAT, False), ("Issue", COL_TITLE, True), ("Target", COL_TARGET, True), ("Fix", COL_FIX, False)):
            r = Gtk.CellRendererText()
            r.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE if idx == COL_TARGET else Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, r, text=idx)
            if idx == COL_SEV:
                col.add_attribute(r, "foreground", COL_COLOR)
                r.set_property("weight", Pango.Weight.BOLD)
                col.set_sort_column_id(COL_RANK)
            else:
                col.set_sort_column_id(idx)
            col.set_resizable(True)
            col.set_expand(expand)
            self.tree.append_column(col)
        self.tree.get_selection().connect("changed", self._on_select)
        paned.pack1(w.scrolled(self.tree), True, False)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        w.add_class(detail, "cd-card")
        detail.set_size_request(380, -1)
        self.d_head = Gtk.Box(spacing=8)
        self.d_title = w.label("Select a result to see details", wrap=True)
        detail.pack_start(self.d_head, False, False, 0)
        detail.pack_start(self.d_title, False, False, 0)
        self.d_target = w.label("", "cd-muted", wrap=True, selectable=True)
        detail.pack_start(self.d_target, False, False, 0)
        detail.pack_start(w.label("DETAILS", "cd-section"), False, False, 0)
        self.d_detail = w.label("", wrap=True, selectable=True)
        detail.pack_start(self.d_detail, False, False, 0)
        detail.pack_start(w.label("REPLACEMENT / RECOMMENDATION", "cd-section"), False, False, 0)
        self.d_repl = w.label("", wrap=True, selectable=True)
        detail.pack_start(self.d_repl, False, False, 0)
        detail.pack_start(w.label("FIX", "cd-section"), False, False, 0)
        self.d_fix = Gtk.TextView()
        self.d_fix.set_editable(False)
        self.d_fix.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.d_fix.set_monospace(True)
        w.add_class(self.d_fix, "cd-terminal")
        self.d_fix.set_left_margin(8)
        self.d_fix.set_top_margin(6)
        self.d_fix.set_bottom_margin(6)
        detail.pack_start(self.d_fix, False, False, 0)
        btns = Gtk.Box(spacing=8)
        self.apply_btn = w.button("Apply fix", "emblem-ok-symbolic", primary=True, on_click=self.apply_current)
        self.copy_btn = w.button("Copy", "edit-copy-symbolic", on_click=self.copy_current)
        self.open_btn = w.button("Open location", "folder-open-symbolic", on_click=self.open_current)
        for b in (self.apply_btn, self.copy_btn, self.open_btn):
            btns.pack_start(b, False, False, 0)
        detail.pack_start(btns, False, False, 0)
        detail_sw = w.scrolled(detail, framed=False)
        detail_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_sw.set_size_request(440, -1)
        detail_sw.set_margin_start(10)
        paned.pack2(detail_sw, False, False)
        self._set_detail(None)

        bottom = Gtk.Box(spacing=8)
        self.fix_all_btn = w.button("Apply all fixes", "system-software-install-symbolic", on_click=self.apply_all)
        self.export_btn = w.button("Export report…", "document-save-as-symbolic", on_click=self.export)
        bottom.pack_end(self.export_btn, False, False, 0)
        bottom.pack_end(self.fix_all_btn, False, False, 0)
        self.pack_start(bottom, False, False, 0)
        self._update_buttons()

    # -- scanning --------------------------------------------------------------------------
    def start_system_scan(self) -> None:
        self.rb_device.set_active(True)
        self.start()

    def choose_folder_and_scan(self) -> None:
        dlg = Gtk.FileChooserNative.new("Choose a directory to scan", self.win, Gtk.FileChooserAction.SELECT_FOLDER, "Scan", "Cancel")
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            if path:
                self.folder.set_filename(path)
                self.rb_dir.set_active(True)
                self.start(path)
        dlg.destroy()

    def start(self, path: Optional[str] = None) -> None:
        if self.cancel_event is not None:
            return
        device = self.rb_device.get_active() and path is None
        path = path or self.folder.get_filename()
        if not device and not (path and os.path.exists(path)):
            self.win.message("No directory selected", "Choose a directory to scan.")
            return
        self.store.clear()
        self.findings = []
        self._set_detail(None)
        self.cancel_event = threading.Event()
        self.scan_btn.set_sensitive(False)
        self.cancel_btn.set_sensitive(not device)
        self.win.busy(True)
        cfg = self.win.config
        cancel = self.cancel_event
        win = self.win

        if device:
            self.scope_label = "This device"
            self.status.set_text("Checking this device…")
            devices = list(win.devices)

            def progress(step: int, label: str) -> None:
                w.ui(self._progress, step / 5.0, label or "Finishing…")

            def work():
                return syscheck.run_all(win.distro, devices or None, progress)

        else:
            self.scope_label = path
            cfg.last_directory = path
            cfg.save()
            self.status.set_text("Scanning %s…" % path)
            scanner = Scanner(cfg.scan_options(), cancel=cancel)

            def progress(n: int, current: str) -> None:
                w.ui(self._progress, None, "%d files · %s" % (n, current) if current else "%d files scanned" % n)

            scanner.progress = progress

            def work():
                return scanner.scan(path)

        def done(res, err):
            self.cancel_event = None
            self.scan_btn.set_sensitive(True)
            self.cancel_btn.set_sensitive(False)
            win.busy(False)
            self.progress.set_fraction(1.0)
            if err:
                self.status.set_text("Scan failed: %s" % err)
                win.log("Scan failed: %r" % err)
                return
            self.set_findings(res)
            self.status.set_text("%s: %d finding(s)" % (self.scope_label, len(res)))
            win.log("Scan of %s finished with %d finding(s)" % (self.scope_label, len(res)))

        w.run_async(work, done)

    def _progress(self, fraction: Optional[float], text: str) -> None:
        if fraction is None:
            self.progress.pulse()
        else:
            self.progress.set_fraction(min(fraction, 1.0))
        self.status.set_text(text)

    def cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.status.set_text("Cancelling…")

    # -- results ------------------------------------------------------------------------------
    def set_findings(self, findings: List[Finding]) -> None:
        self.findings = findings
        self.store.clear()
        colors = self.win.sev_colors
        for i, f in enumerate(findings):
            self.store.append(
                [
                    f.severity.upper(),
                    f.category,
                    f.title,
                    f.target,
                    colors.get(f.severity, colors["info"]),
                    i,
                    SEVERITY_RANK.get(f.severity, 9) * 100000 + i,
                    "✓" if f.action else "",
                ]
            )
        crit = sum(1 for f in findings if f.severity == "critical")
        warn = sum(1 for f in findings if f.severity == "warning")
        info = sum(1 for f in findings if f.severity == "info")
        self.count_crit.set_text("%d critical" % crit)
        self.count_warn.set_text("%d warning" % warn)
        self.count_info.set_text("%d info" % info)
        self.win.set_counter("compat", str(crit + warn) if crit + warn else "", "critical" if crit else "warning")
        self.win.overview.update_faults(findings)
        self._update_buttons()
        if len(self.sorted):
            self.tree.get_selection().select_path(Gtk.TreePath.new_first())

    def recolor(self) -> None:
        if self.findings:
            self.set_findings(self.findings)

    def _visible(self, model, it, _data) -> bool:
        sev = self.sev_combo.get_active_id() or "all"
        if sev != "all" and model[it][COL_SEV].lower() != sev:
            return False
        if self.fixable.get_active() and not model[it][COL_FIX]:
            return False
        q = self.search.get_text().strip().lower()
        if q:
            hay = " ".join(str(model[it][c]) for c in (COL_CAT, COL_TITLE, COL_TARGET)).lower()
            f = self.findings[model[it][COL_IDX]] if model[it][COL_IDX] < len(self.findings) else None
            if f:
                hay += " " + (f.detail + " " + f.replacement).lower()
            return q in hay
        return True

    def _on_select(self, sel) -> None:
        model, it = sel.get_selected()
        if it is None:
            self._set_detail(None)
            return
        idx = model[it][COL_IDX]
        self._set_detail(self.findings[idx] if idx < len(self.findings) else None)

    def _set_detail(self, f: Optional[Finding]) -> None:
        self.current = f
        for c in self.d_head.get_children():
            self.d_head.remove(c)
        if f is None:
            self.d_title.set_text("Select a result to see details")
            for lbl in (self.d_target, self.d_detail, self.d_repl):
                lbl.set_text("")
            self.d_fix.get_buffer().set_text("")
            self.apply_btn.set_sensitive(False)
            self.copy_btn.set_sensitive(False)
            self.open_btn.set_sensitive(False)
            return
        self.d_head.pack_start(w.pill(f.severity, f.severity), False, False, 0)
        self.d_head.pack_start(w.pill(f.category, "neutral"), False, False, 0)
        self.d_head.show_all()
        self.d_title.set_markup("<b><big>%s</big></b>" % _esc(f.title))
        self.d_target.set_text(f.target)
        self.d_detail.set_text(f.detail or "—")
        self.d_repl.set_text(f.replacement or "—")
        self.d_fix.get_buffer().set_text(describe_action(f.action) if f.action else "No automatic fix available.")
        self.apply_btn.set_sensitive(f.action is not None)
        self.copy_btn.set_sensitive(f.action is not None)
        self.open_btn.set_sensitive(os.path.exists(f.target) or os.path.lexists(f.target))

    def _update_buttons(self) -> None:
        self.fix_all_btn.set_sensitive(fix_all_action(self.findings) is not None)
        self.export_btn.set_sensitive(bool(self.findings))

    # -- actions --------------------------------------------------------------------------------
    def apply_current(self) -> None:
        if self.current and self.current.action:
            self.win.confirm_and_run(self.current.action, lambda rc: self._after_fix(rc))

    def apply_all(self) -> None:
        act = fix_all_action(self.findings)
        if act:
            self.win.confirm_and_run(act, lambda rc: self._after_fix(rc))

    def _after_fix(self, rc: int) -> None:
        if rc == 0 and not self.win.config.dry_run:
            self.status.set_text("Fix applied - run the scan again to verify.")

    def copy_current(self) -> None:
        if self.current and self.current.action:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(describe_action(self.current.action), -1)
            self.status.set_text("Fix commands copied to the clipboard")

    def open_current(self) -> None:
        if not self.current:
            return
        target = self.current.target
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        try:
            Gio.AppInfo.launch_default_for_uri("file://" + folder, None)
        except Exception as exc:  # noqa: BLE001
            self.win.message("Cannot open folder", str(exc))

    def export(self) -> None:
        dlg = Gtk.FileChooserNative.new("Export report", self.win, Gtk.FileChooserAction.SAVE, "Export", "Cancel")
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name("compatd-report.md")
        for name, pattern in (("Markdown", "*.md"), ("JSON", "*.json")):
            ff = Gtk.FileFilter()
            ff.set_name(name)
            ff.add_pattern(pattern)
            dlg.add_filter(ff)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            try:
                write_report(path, self.findings, self.scope_label, self.win.distro.pretty_name)
                self.status.set_text("Report written to %s" % path)
                self.win.log("Report exported to %s" % path)
            except OSError as exc:
                self.win.message("Export failed", str(exc), Gtk.MessageType.ERROR)
        dlg.destroy()


def _esc(s: str) -> str:
    from gi.repository import GLib

    return GLib.markup_escape_text(s)
