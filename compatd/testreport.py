# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Opt-in test reports, sent as a pre-filled GitHub issue that the user reviews and submits.

CompatD never uploads anything itself. ``issue_url`` only builds a link: the browser opens
GitHub's "Hardware test report" form with these fields filled in, and nothing is sent until the
user presses "Submit" there.
"""
from __future__ import annotations

import getpass
import os
import platform
import socket
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

from . import __version__, gpu, hardware, perf
from .distro import DistroInfo
from .models import SEVERITY_RANK, Device, Finding, Recommendation

REPO = "unforged1/CompatD"
TEMPLATE = "hardware_report.yml"
MAX_URL = 7500  # GitHub rejects much longer URLs; extra text goes to the clipboard instead

# Hardware report form: distribution dropdown options, by CompatD family
DISTRO_OPTIONS = {"debian": "Debian", "mx": "MX Linux", "lmde": "LMDE", "kali": "Kali Linux", "other": "Other Debian-based"}

# Form field id -> heading shown in the review dialog
FIELDS = OrderedDict(
    [
        ("info", "System"),
        ("drivers", "Devices and drivers"),
        ("system", "Device compatibility check"),
        ("performance", "Performance and errors"),
    ]
)


def _private_words() -> List[Tuple[str, str]]:
    """Strings that identify this machine or user, with what replaces them."""
    out = []
    home = os.path.expanduser("~")
    if home and home != "/":
        out.append((home, "~"))
    for value, repl in ((_safe(getpass.getuser), "<user>"), (_safe(socket.gethostname), "<host>")):
        if value and len(value) >= 3:
            out.append((value, repl))
    return out


def _safe(fn) -> str:
    try:
        return fn() or ""
    except Exception:  # noqa: BLE001 - no user or hostname is fine
        return ""


def scrub(text: str, words: Optional[List[Tuple[str, str]]] = None) -> str:
    """Replace the home directory, user name and host name."""
    pairs = words if words is not None else _private_words()
    # Longest first, so a host name that contains the user name ("alice-laptop") is replaced whole.
    for value, repl in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        text = text.replace(value, repl)
    return text


def _info(distro: DistroInfo, devices: List[Device]) -> str:
    s = hardware.summary()
    active = gpu.active_gpu(devices)
    lines = [
        "compatd:     %s" % __version__,
        "distro:      %s" % distro.pretty_name,
        "family:      %s" % distro.family,
        "kernel:      %s" % s["kernel"],
        "arch:        %s" % s["arch"],
        "cpu:         %s (%s cores)" % (s["cpu"], s["cores"]),
        "memory:      %s" % s["memory"],
        "boot:        %s, Secure Boot %s" % (s["boot"], s["secure_boot"]),
        "session:     %s on %s" % (s["desktop"], s["session"]),
        "python:      %s" % platform.python_version(),
    ]
    if active:
        entries = gpu.all_gpus(devices, active)
        lines.append("gpus:        %s" % ", ".join("%s %s%s" % (e.role, e.name, " (active)" if e.active else "") for e in entries))
        lines.append("gpu source:  %s" % active.source)
    return "\n".join(lines)


def _drivers(devices: List[Device], recs: Iterable[Recommendation]) -> str:
    lines = ["Devices:"]
    for d in devices:
        if hardware.is_important(d):
            lines.append("  %s %s %s | %s | driver: %s" % (d.bus, d.ids, d.class_name[:30], d.label[:70], d.driver or "NONE"))
    lines.append("")
    lines.append("Recommendations:")
    for r in recs:
        extra = (" (to install: %s)" % ", ".join(r.missing)) if r.missing else ""
        lines.append("  [%s]%s %s%s" % (r.status, " (optional)" if r.optional else "", r.title, extra))
    return "\n".join(lines)


def _system(findings: Optional[List[Finding]]) -> str:
    if findings is None:
        return "Not run."
    if not findings:
        return "No findings."
    lines = []
    for f in sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), f.category)):
        lines.append("[%s] %s: %s" % (f.severity.upper(), f.category, f.title))
    return "\n".join(lines)


def _performance() -> str:
    timings, errors = perf.snapshot()
    lines = []
    for name, runs in timings.items():
        for secs, detail in runs[-3:]:  # the last few runs of each step are enough
            rate = ""
            if detail.endswith(" files") and secs > 0:
                try:
                    rate = ", %d files/s" % (int(detail.split()[0]) / secs)
                except ValueError:
                    pass
            lines.append("%-34s %7.2f s%s%s" % (name + ":", secs, (" (%s)" % detail) if detail else "", rate))
    if not lines:
        lines.append("No timings recorded.")
    lines.append("")
    lines.append("Errors this session: %d" % len(errors))
    lines.extend("  " + e for e in errors)
    return "\n".join(lines)


def build(
    distro: DistroInfo,
    devices: List[Device],
    recs: Iterable[Recommendation],
    findings: Optional[List[Finding]] = None,
) -> Dict[str, str]:
    """Report fields keyed by the hardware report form's field ids, already scrubbed."""
    words = _private_words()
    active = gpu.active_gpu(devices)
    data = {
        "title": "Hardware report: %s%s" % (distro.pretty_name, (", " + active.name) if active else ""),
        "distro": DISTRO_OPTIONS.get(distro.family, DISTRO_OPTIONS["other"]),
        "info": _info(distro, devices),
        "drivers": _drivers(devices, recs),
        "system": _system(findings),
        "performance": _performance(),
    }
    return {k: scrub(v, words) for k, v in data.items()}


def as_text(report: Dict[str, str]) -> str:
    """The whole report as Markdown, for the clipboard."""
    parts = ["## %s" % report.get("title", "Hardware report"), "", "Distribution: %s" % report.get("distro", "")]
    for key, heading in FIELDS.items():
        parts += ["", "### " + heading, "", "```", report.get(key, "").rstrip(), "```"]
    return "\n".join(parts) + "\n"


def issue_url(report: Dict[str, str], repo: str = REPO) -> Tuple[str, bool]:
    """Link to a pre-filled hardware report issue, and whether some fields had to be shortened.

    GitHub limits URL length. Long fields are cut, largest first, and end with a note asking the
    reporter to paste the full text, which the caller puts on the clipboard.
    """
    note = "\n… (cut to fit the link; the full text is on your clipboard, paste it below)"
    params = OrderedDict([("template", TEMPLATE), ("title", report.get("title", ""))])
    # GitHub can't pre-fill dropdowns, so "distro" and "result" are picked in the form itself.
    for key in FIELDS:
        if report.get(key):
            params[key] = report[key]
    base = "https://github.com/%s/issues/new?" % repo
    url = base + urlencode(params)
    shortened = False
    while len(url) > MAX_URL:
        key = max(FIELDS, key=lambda k: len(params.get(k, "")))
        text = params.get(key, "")
        if len(text) < 200:
            break
        keep = max(len(text) - (len(url) - MAX_URL) - len(note) * 3 - 50, 100)
        params[key] = text[:keep].rsplit("\n", 1)[0] + note
        shortened = True
        url = base + urlencode(params)
    return url, shortened
