"""Activity page: live output of every action CompatD runs."""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import widgets as w  # noqa: E402


class ActivityPage(Gtk.Box):
    def __init__(self, win) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.win = win
        self.set_border_width(24)
        w.add_class(self, "cd-page")
        top = Gtk.Box(spacing=8)
        top.pack_start(w.page_header("Activity", "Output of installs, fixes and scans in this session."), True, True, 0)
        top.pack_end(w.button("Save log…", "document-save-symbolic", on_click=self.save), False, False, 0)
        top.pack_end(w.button("Clear", "edit-clear-all-symbolic", on_click=self.clear), False, False, 0)
        self.pack_start(top, False, False, 0)

        self.view = Gtk.TextView()
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.CHAR)
        for m in ("left", "right", "top", "bottom"):
            getattr(self.view, "set_%s_margin" % m)(12)
        w.add_class(self.view, "cd-terminal")
        self.buf = self.view.get_buffer()
        self.end_mark = self.buf.create_mark("end", self.buf.get_end_iter(), False)
        self.pack_start(w.scrolled(self.view), True, True, 0)
        self.append("CompatD started on %s" % win.distro.pretty_name)

    def append(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.buf.insert(self.buf.get_end_iter(), "[%s] %s\n" % (stamp, line))
        self.view.scroll_mark_onscreen(self.end_mark)

    def clear(self) -> None:
        self.buf.set_text("")

    def save(self) -> None:
        dlg = Gtk.FileChooserNative.new("Save log", self.win, Gtk.FileChooserAction.SAVE, "Save", "Cancel")
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name("compatd-activity.log")
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            start, end = self.buf.get_bounds()
            try:
                with open(dlg.get_filename(), "w", encoding="utf-8") as fh:
                    fh.write(self.buf.get_text(start, end, False))
            except OSError as exc:
                self.win.message("Save failed", str(exc), Gtk.MessageType.ERROR)
        dlg.destroy()
