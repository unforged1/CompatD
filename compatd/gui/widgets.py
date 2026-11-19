"""Reusable GTK widgets and helpers."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402


def run_async(work: Callable[[], Any], done: Callable[[Any, Optional[BaseException]], None]) -> threading.Thread:
    """Run ``work`` in a thread and deliver (result, error) to ``done`` on the GTK main loop."""

    def target():
        try:
            res, err = work(), None
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            res, err = None, exc
        GLib.idle_add(lambda: (done(res, err), False)[1])

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t


def ui(fn: Callable, *args) -> None:
    """Schedule a call on the GTK main loop from any thread."""
    GLib.idle_add(lambda: (fn(*args), False)[1])


def label(text: str = "", css: Optional[str] = None, xalign: float = 0.0, wrap: bool = False, selectable: bool = False, markup: bool = False) -> Gtk.Label:
    lbl = Gtk.Label()
    if markup:
        lbl.set_markup(text)
    else:
        lbl.set_text(text)
    lbl.set_xalign(xalign)
    if wrap:
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    else:
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.set_selectable(selectable)
    if css:
        for c in css.split():
            lbl.get_style_context().add_class(c)
    return lbl


def add_class(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


def pill(text: str, kind: str = "neutral") -> Gtk.Label:
    lbl = label(text, "cd-pill cd-pill-%s" % kind, xalign=0.5)
    lbl.set_valign(Gtk.Align.CENTER)
    lbl.set_halign(Gtk.Align.START)
    return lbl


def page_header(title: str, subtitle: str = "") -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.pack_start(label(title, "cd-page-title"), False, False, 0)
    if subtitle:
        box.pack_start(label(subtitle, "cd-page-sub", wrap=True), False, False, 0)
    return box


class InfoCard(Gtk.Box):
    def __init__(self, title: str, value: str = "", value_class: str = "cd-card-value", icon: Optional[str] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        add_class(self, "cd-card")
        head = Gtk.Box(spacing=6)
        if icon:
            img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
            add_class(img, "cd-muted")
            head.pack_start(img, False, False, 0)
        head.pack_start(label(title.upper(), "cd-card-title"), True, True, 0)
        self.pack_start(head, False, False, 0)
        self.value = label(value, value_class, wrap=True, selectable=True)
        self.pack_start(self.value, False, False, 0)
        self._value_class = value_class

    def set_value(self, text: str, extra_class: Optional[str] = None) -> None:
        self.value.set_text(text)
        ctx = self.value.get_style_context()
        for c in ("cd-stat-critical", "cd-stat-warning", "cd-stat-ok"):
            ctx.remove_class(c)
        if extra_class:
            ctx.add_class(extra_class)


def button(text: str, icon: Optional[str] = None, primary: bool = False, tooltip: Optional[str] = None, on_click: Optional[Callable] = None) -> Gtk.Button:
    btn = Gtk.Button()
    box = Gtk.Box(spacing=6)
    if icon:
        box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    btn.add(box)
    if primary:
        add_class(btn, "suggested-action", "cd-primary")
    if tooltip:
        btn.set_tooltip_text(tooltip)
    if on_click:
        btn.connect("clicked", lambda *_: on_click())
    return btn


def scrolled(child: Gtk.Widget, framed: bool = True) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sw.add(child)
    if framed:
        add_class(sw, "cd-framed")
    return sw


def page_box() -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    box.set_border_width(24)
    add_class(box, "cd-page")
    return box
