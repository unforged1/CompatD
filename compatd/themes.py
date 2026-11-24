# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Per-distribution colour themes, rendered to GTK3 CSS."""
from __future__ import annotations

from typing import Dict

# Each palette: dark flag + colour tokens. Colours are original, inspired by each
# distribution's general look (no logos or artwork are used).
PALETTES: Dict[str, Dict[str, object]] = {
    "kali": {
        "dark": True,
        "bg": "#14161c",
        "surface": "#1d2029",
        "surface2": "#262a36",
        "border": "#323848",
        "fg": "#e4e8f1",
        "muted": "#8c95ab",
        "header": "#0d0f14",
        "header_fg": "#ffffff",
        "accent": "#367bf0",
        "accent_fg": "#ffffff",
        "accent_soft": "rgba(54,123,240,0.18)",
        "terminal_bg": "#0b0d11",
        "terminal_fg": "#9fd0ff",
    },
    "debian": {
        "dark": True,
        "bg": "#1f1d1e",
        "surface": "#2a2729",
        "surface2": "#343032",
        "border": "#453f42",
        "fg": "#ece6e8",
        "muted": "#a3979c",
        "header": "#a80030",
        "header_fg": "#ffffff",
        "accent": "#d70a53",
        "accent_fg": "#ffffff",
        "accent_soft": "rgba(215,10,83,0.20)",
        "terminal_bg": "#151314",
        "terminal_fg": "#ffd9e5",
    },
    "mx": {
        "dark": True,
        "bg": "#303338",
        "surface": "#3a3e44",
        "surface2": "#43484f",
        "border": "#525860",
        "fg": "#e8ebef",
        "muted": "#a7afba",
        "header": "#1f2a36",
        "header_fg": "#f1f5f9",
        "accent": "#3b8be0",
        "accent_fg": "#ffffff",
        "accent_soft": "rgba(59,139,224,0.20)",
        "terminal_bg": "#1f2429",
        "terminal_fg": "#cfe3ff",
    },
    "lmde": {
        "dark": True,
        "bg": "#1f2220",
        "surface": "#292d2a",
        "surface2": "#323733",
        "border": "#3d443e",
        "fg": "#e6ebe5",
        "muted": "#97a395",
        "header": "#161917",
        "header_fg": "#ffffff",
        "accent": "#8bc34a",
        "accent_fg": "#14200a",
        "accent_soft": "rgba(139,195,74,0.16)",
        "terminal_bg": "#111311",
        "terminal_fg": "#c9f29b",
    },
    "other": {
        "dark": True,
        "bg": "#1b1e22",
        "surface": "#24282d",
        "surface2": "#2d3238",
        "border": "#3a4047",
        "fg": "#e8eaed",
        "muted": "#9aa0a6",
        "header": "#15171a",
        "header_fg": "#ffffff",
        "accent": "#26a69a",
        "accent_fg": "#ffffff",
        "accent_soft": "rgba(38,166,154,0.16)",
        "terminal_bg": "#101214",
        "terminal_fg": "#a7f3eb",
    },
}

SEVERITY_COLORS = {
    "dark": {"critical": "#ff5c6c", "warning": "#ffb74d", "info": "#64b5f6", "ok": "#66d17a"},
    "light": {"critical": "#c62828", "warning": "#b26a00", "info": "#1565c0", "ok": "#2e7d32"},
}


def palette(family: str, mode: str = "theme") -> Dict[str, object]:
    pal = dict(PALETTES.get(family, PALETTES["other"]))
    if mode in ("dark", "light") and (mode == "dark") != pal["dark"]:
        # Derive the opposite brightness variant while keeping the accent colours.
        if mode == "dark":
            pal.update(
                dark=True, bg="#17191d", surface="#202329", surface2="#2a2e35", border="#363b44",
                fg="#e8eaed", muted="#9aa0a6", terminal_bg="#0e1013",
            )
        else:
            pal.update(
                dark=False, bg="#f4f5f7", surface="#ffffff", surface2="#eceef2", border="#d6d9df",
                fg="#1f2329", muted="#6b7280", terminal_bg="#1f2329",
            )
    return pal


def severity_colors(dark: bool) -> Dict[str, str]:
    return SEVERITY_COLORS["dark" if dark else "light"]


def css(family: str, mode: str = "theme") -> str:
    p = palette(family, mode)
    sev = severity_colors(bool(p["dark"]))
    defs = "".join("@define-color cd_%s %s;\n" % (k, v) for k, v in p.items() if k != "dark")
    defs += "".join("@define-color cd_sev_%s %s;\n" % (k, v) for k, v in sev.items())
    return defs + _CSS


_CSS = """
window.compatd, window.compatd > .background, .compatd .cd-page { background-color: @cd_bg; color: @cd_fg; }
.compatd label { color: inherit; }
.compatd scrolledwindow, .compatd viewport, .compatd stack { background-color: @cd_bg; }
.compatd scrolledwindow.cd-framed { background-color: @cd_surface; }
.compatd .cd-page-pad { padding: 24px; }

.compatd headerbar {
  background-image: none; background-color: @cd_header; color: @cd_header_fg;
  border: none; border-bottom: 3px solid @cd_accent; box-shadow: none; min-height: 48px;
}
.compatd headerbar label, .compatd headerbar .title { color: @cd_header_fg; }
.compatd headerbar .subtitle { color: alpha(@cd_header_fg, 0.7); }
.compatd headerbar button { color: @cd_header_fg; background-image: none; background-color: transparent; border-color: transparent; box-shadow: none; }
.compatd headerbar button:hover { background-color: alpha(@cd_header_fg, 0.12); }
.compatd .cd-distro-badge {
  background-color: @cd_accent; color: @cd_accent_fg; border-radius: 12px;
  padding: 3px 12px; font-weight: bold; font-size: 0.9em;
}
.compatd headerbar .cd-gpu-badge {
  background-color: alpha(#000000, 0.35); color: alpha(@cd_header_fg, 0.75); border: 2px solid alpha(@cd_header_fg, 0.35);
  border-radius: 12px; padding: 2px 11px; font-weight: bold; font-size: 0.9em;
}
.compatd headerbar .cd-gpu-badge.cd-gpu-nvidia { border-color: #76b900; }
.compatd headerbar .cd-gpu-badge.cd-gpu-amd { border-color: #ed1c24; }
.compatd headerbar .cd-gpu-badge.cd-gpu-intel { border-color: #00c7fd; }
.compatd headerbar .cd-gpu-badge.cd-gpu-active { color: @cd_header_fg; background-color: alpha(#000000, 0.6); }
.compatd headerbar .cd-gpu-badge.cd-gpu-nodriver { border-style: dashed; border-color: #ff6b6b; }

.compatd .cd-sidebar { background-color: @cd_surface; border-right: 1px solid @cd_border; }
.compatd .cd-sidebar list, .compatd .cd-sidebar row { background-color: transparent; color: @cd_fg; }
.compatd .cd-sidebar row { padding: 12px 18px; border-radius: 8px; margin: 2px 8px; }
.compatd .cd-sidebar row:hover { background-color: @cd_accent_soft; }
.compatd .cd-sidebar row:selected { background-color: @cd_accent; color: @cd_accent_fg; }
.compatd .cd-sidebar row:selected label, .compatd .cd-sidebar row:selected image { color: @cd_accent_fg; }
.compatd .cd-sidebar .cd-brand { font-size: 20px; font-weight: 800; color: @cd_accent; padding: 18px 18px 2px 18px; }
.compatd .cd-sidebar .cd-version { color: @cd_muted; padding: 0 18px 14px 18px; font-size: 0.85em; }

.compatd .cd-page-title { font-size: 22px; font-weight: 800; color: @cd_fg; }
.compatd .cd-page-sub { color: @cd_muted; }
.compatd .cd-muted { color: @cd_muted; }
.compatd .cd-section { font-weight: bold; color: @cd_muted; font-size: 0.85em; letter-spacing: 1px; }

.compatd .cd-card {
  background-color: @cd_surface; border: 1px solid @cd_border; border-radius: 12px; padding: 16px;
}
.compatd .cd-card-title { color: @cd_muted; font-size: 0.85em; }
.compatd .cd-card-value { font-size: 15px; font-weight: bold; color: @cd_fg; }
.compatd .cd-stat { font-size: 30px; font-weight: 800; color: @cd_accent; }
.compatd .cd-stat-critical { color: @cd_sev_critical; }
.compatd .cd-stat-warning { color: @cd_sev_warning; }
.compatd .cd-stat-ok { color: @cd_sev_ok; }

.compatd button {
  background-image: none; background-color: @cd_surface2; color: @cd_fg;
  border: 1px solid @cd_border; border-radius: 8px; padding: 6px 14px; box-shadow: none; text-shadow: none;
}
.compatd button:hover { background-color: @cd_accent_soft; border-color: @cd_accent; }
.compatd button:disabled { opacity: 0.5; }
.compatd button.suggested-action, .compatd button.cd-primary {
  background-color: @cd_accent; color: @cd_accent_fg; border-color: @cd_accent; font-weight: bold;
}
.compatd button.suggested-action label, .compatd button.cd-primary label { color: @cd_accent_fg; }
.compatd button.suggested-action:hover, .compatd button.cd-primary:hover { background-color: shade(@cd_accent, 1.1); }
.compatd button.destructive-action { background-color: @cd_sev_critical; color: #ffffff; border-color: @cd_sev_critical; }

.compatd entry, .compatd spinbutton, .compatd combobox button {
  background-image: none; background-color: @cd_surface; color: @cd_fg; border: 1px solid @cd_border; border-radius: 8px;
}
.compatd entry:focus { border-color: @cd_accent; }
.compatd switch { background-image: none; background-color: @cd_surface2; border: 1px solid @cd_border; }
.compatd switch:checked { background-color: @cd_accent; border-color: @cd_accent; }
.compatd switch slider { background-image: none; background-color: #ffffff; border: none; }
.compatd checkbutton check, .compatd check {
  background-image: none; background-color: @cd_surface; border: 1px solid @cd_muted; border-radius: 4px;
}
.compatd checkbutton check:checked, .compatd check:checked { background-color: @cd_accent; border-color: @cd_accent; color: @cd_accent_fg; }
.compatd radiobutton radio { background-image: none; background-color: @cd_surface; border: 1px solid @cd_muted; color: @cd_accent_fg; }
.compatd radiobutton radio:checked { background-color: @cd_accent; border-color: @cd_accent; color: @cd_accent_fg; }

.compatd progressbar trough { background-color: @cd_surface2; border: none; min-height: 6px; border-radius: 3px; }
.compatd progressbar progress { background-image: none; background-color: @cd_accent; border: none; min-height: 6px; border-radius: 3px; }

.compatd treeview.view { background-color: @cd_surface; color: @cd_fg; }
.compatd treeview.view:selected, .compatd treeview.view:selected:focus { background-color: @cd_accent; color: @cd_accent_fg; }
.compatd treeview.view header button {
  background-color: @cd_surface2; color: @cd_muted; border: none; border-bottom: 1px solid @cd_border;
  border-radius: 0; font-weight: bold; padding: 6px 8px;
}
.compatd scrolledwindow.cd-framed { border: 1px solid @cd_border; border-radius: 10px; }
.compatd list.cd-list { background-color: transparent; }
.compatd list.cd-list row { background-color: @cd_surface; border: 1px solid @cd_border; border-radius: 10px; margin: 4px 2px; padding: 10px 12px; }
.compatd list.cd-list row:selected { background-color: @cd_accent_soft; border-color: @cd_accent; }
.compatd list.cd-list row label { color: @cd_fg; }
.compatd list.cd-list row .cd-muted { color: @cd_muted; }

.compatd .cd-pill { border-radius: 10px; padding: 1px 9px; font-size: 0.8em; font-weight: bold; }
.compatd .cd-pill-critical { background-color: @cd_sev_critical; color: #ffffff; }
.compatd .cd-pill-warning { background-color: @cd_sev_warning; color: #1a1300; }
.compatd .cd-pill-info { background-color: @cd_sev_info; color: #ffffff; }
.compatd .cd-pill-ok { background-color: @cd_sev_ok; color: #08210c; }
.compatd .cd-pill-neutral { background-color: @cd_surface2; color: @cd_muted; }

.compatd textview.cd-terminal, .compatd textview.cd-terminal text {
  background-color: @cd_terminal_bg; color: @cd_terminal_fg; font-family: monospace; font-size: 10pt;
}
.compatd textview.cd-detail, .compatd textview.cd-detail text { background-color: @cd_surface; color: @cd_fg; }
.compatd .cd-logo-card { background-color: #ffffff; border-radius: 10px; }
.compatd .cd-banner { background-color: @cd_accent_soft; border: 1px solid @cd_accent; border-radius: 10px; padding: 10px 14px; }
.compatd separator { background-color: @cd_border; }
.compatd paned > separator { background-color: @cd_border; min-width: 1px; min-height: 1px; }
.compatd tooltip, .compatd tooltip * { background-color: @cd_surface2; color: @cd_fg; }
"""
