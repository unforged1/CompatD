# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Which GPU is active, and a short marketing name for it (NVIDIA RTX 4060, INTEL ARC A770, RADEON 680M)."""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .models import Device
from .sysutil import read_text, run, which

NVIDIA, AMD, INTEL = "10de", "1002", "8086"
VIRTUAL_VENDORS = {"15ad": "VMWARE", "1af4": "VIRTIO", "1b36": "QXL", "80ee": "VIRTUALBOX", "1234": "QEMU"}
PCI_ROOT = "/sys/bus/pci/devices"


@dataclass
class GpuInfo:
    device: Optional[Device]
    name: str  # short upper-case label, e.g. "RADEON 680M"
    vendor_id: str
    source: str  # how the active GPU was determined
    renderer: str = ""
    others: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # other GPUs: (name, driver)
    alternatives: str = ""  # lspci's model list when the exact model is unknown, e.g. "RX 6800/6800 XT / 6900 XT"


def _clean(text: str) -> str:
    text = re.sub(r"\((?:R|r|TM|tm)\)", "", text)
    text = re.sub(r"/PCIe.*$", "", text)  # NVIDIA renderer: "... Laptop GPU/PCIe/SSE2"
    text = re.sub(r"\s\(.*$", "", text)  # Mesa renderer: "AMD Radeon 680M (radeonsi, rembrandt, ...)"
    return re.sub(r"\s+", " ", text).strip()


def _bracket(text: str) -> str:
    """lspci names put the marketing name in brackets: 'AD107M [GeForce RTX 4060 Max-Q / Mobile]'."""
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1) if m else text


def gpu_name(vendor_id: str, text: str) -> Tuple[str, bool]:
    """Short upper-case GPU label and whether it names a specific model.

    ``text`` is an lspci device name or an OpenGL renderer string.
    """
    vendor_id = vendor_id.lower()
    t = _clean(text)
    if vendor_id == NVIDIA:
        m = re.search(r"\b(RTX|GTX|GT|MX)\s*(A?\d{3,4}[A-Z]?)((?:\s*(?:Ti|SUPER|Super))*)", t)
        if m:
            return ("NVIDIA %s %s%s" % (m.group(1), m.group(2), m.group(3))).upper(), True
        m = re.search(r"\b(Quadro|TITAN|Tesla)\s+([\w-]+)", t, re.I)
        if m:
            return ("NVIDIA %s %s" % (m.group(1), m.group(2))).upper(), True
        model = _bracket(t).split("/")[0].replace("GeForce", "").strip()
        return ("NVIDIA " + model).upper().strip() if model else "NVIDIA", bool(model)
    if vendor_id == INTEL:
        m = re.search(r"\bArc\b(?:\s*Pro)?\s*([A-Z]\d{2,3}[A-Z]?)?", t)
        if m:
            return ("INTEL ARC " + (m.group(1) or "")).strip().upper(), bool(m.group(1))
        if re.search(r"Iris\s*Xe", t, re.I):
            return "INTEL IRIS XE", True
        m = re.search(r"\b(UHD|HD|Iris(?:\s*(?:Pro|Plus))?)\s*Graphics\s*(P?\d{3,4})?", t, re.I)
        if m:
            return ("INTEL %s %s" % (m.group(1), m.group(2) or "")).strip().upper(), True
        return "INTEL GRAPHICS", False
    if vendor_id == AMD:
        model = _bracket(t).split("/")[0]
        model = re.sub(r"\b(AMD|ATI|Radeon|Series|Mobile|Graphics)\b", " ", model, flags=re.I)
        model = re.sub(r"\s+", " ", model).strip()
        return ("RADEON " + model).upper().strip(), bool(model)
    if vendor_id in VIRTUAL_VENDORS:
        return VIRTUAL_VENDORS[vendor_id] + " VIRTUAL GPU", True
    return _bracket(t).split("/")[0].strip().upper() or "UNKNOWN GPU", False


def ambiguous_models(text: str) -> str:
    """lspci's bracket text when it lists several models for one chip ("Radeon RX 6800/6800 XT / 6900 XT")."""
    inner = _bracket(text)
    return inner if re.search(r"/\s*(?:[A-Z]+\s*)?\d", inner) else ""


def _vendor_of_renderer(renderer: str) -> Optional[str]:
    r = renderer.lower()
    if "nvidia" in r or "geforce" in r or "quadro" in r:
        return NVIDIA
    if "radeon" in r or r.startswith("amd"):
        return AMD
    if "intel" in r:
        return INTEL
    return None


def opengl_renderer() -> str:
    """The desktop's OpenGL renderer from ``glxinfo -B``, or "" when unavailable."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or not which("glxinfo"):
        return ""
    rc, out, _ = run(["glxinfo", "-B"], timeout=5)
    if rc != 0:
        return ""
    for line in out.splitlines():
        if line.strip().startswith("OpenGL renderer string:"):
            return line.split(":", 1)[1].strip()
    return ""


def _drives_display(slot: str) -> bool:
    for status in glob.glob(os.path.join(PCI_ROOT, slot, "drm", "card*", "card*-*", "status")):
        if read_text(status).strip() == "connected":
            return True
    return False


def _boot_vga(slot: str) -> bool:
    return read_text(os.path.join(PCI_ROOT, slot, "boot_vga")).strip() == "1"


def gpus(devices: Iterable[Device]) -> List[Device]:
    return [d for d in devices if d.bus == "pci" and d.class_code.startswith("03")]


# AMD APU code names, for an integrated Radeon when its bridge name doesn't say "Internal".
_AMD_APU = re.compile(
    r"\b(Raven|Picasso|Renoir|Cezanne|Lucienne|Barcelo|Rembrandt|Mendocino|Phoenix|Hawk Point|Raphael|Granite Ridge|"
    r"Strix|Krackan|Van Gogh|Carrizo|Kaveri|Stoney|Wani|Mullins|Kabini|Beema|Kalindi|Godavari|Trinity|Richland)\b",
    re.I,
)


def _ancestors(slot: str) -> List[str]:
    """PCI bridges above a device, nearest first, from its sysfs path."""
    path = os.path.realpath(os.path.join(PCI_ROOT, slot))
    parts = [p for p in path.split("/") if re.match(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$", p)]
    return list(reversed(parts[:-1]))


def _removable(slot: str) -> bool:
    """The kernel marks PCI devices behind external Thunderbolt/USB4 ports as removable."""
    return read_text(os.path.join(PCI_ROOT, slot, "removable")).strip() == "removable"


def gpu_role(
    dev: Device,
    by_slot: Dict[str, Device],
    ancestors: Callable[[str], List[str]] = _ancestors,
    removable: Callable[[str], bool] = _removable,
) -> str:
    """'eGPU' (external, Thunderbolt/USB4), 'iGPU' (in the CPU), 'dGPU' (discrete) or 'vGPU' (virtual machine)."""
    vid = dev.vendor_id.lower()
    above = ancestors(dev.slot)
    bridge_names = " ".join(by_slot[a].name for a in above if a in by_slot)
    if removable(dev.slot) or any(removable(a) for a in above) or re.search(r"Thunderbolt|USB4", bridge_names, re.I):
        return "eGPU"
    if vid in VIRTUAL_VENDORS:
        return "vGPU"
    if vid == INTEL and dev.slot.endswith(":00:02.0"):
        return "iGPU"
    if vid == AMD and (re.search(r"\bInternal\b", bridge_names) or _AMD_APU.search(dev.name)):
        return "iGPU"
    return "dGPU"


@dataclass
class GpuEntry:
    device: Device
    name: str
    vendor_id: str
    role: str  # iGPU | dGPU | eGPU | vGPU
    active: bool  # renders the desktop
    alternatives: str = ""


_ROLE_ORDER = {"iGPU": 0, "dGPU": 1, "eGPU": 2, "vGPU": 3}


def all_gpus(devices: Iterable[Device], active: Optional[GpuInfo] = None, **role_kw) -> List[GpuEntry]:
    """Every GPU with its role, integrated first, then discrete, then external."""
    devices = list(devices)
    by_slot = {d.slot: d for d in devices if d.bus == "pci"}
    out = []
    for dev in gpus(devices):
        is_active = active is not None and active.device is dev
        if is_active:
            name, alts = active.name, active.alternatives
        else:
            name, alts = gpu_name(dev.vendor_id, dev.name)[0], ambiguous_models(dev.name)
        out.append(GpuEntry(dev, name, dev.vendor_id.lower(), gpu_role(dev, by_slot, **role_kw), is_active, alts))
    out.sort(key=lambda e: (_ROLE_ORDER.get(e.role, 9), e.device.slot))
    return out


def active_gpu(
    devices: Iterable[Device],
    renderer: Optional[str] = None,
    drives_display: Callable[[str], bool] = _drives_display,
    boot_vga: Callable[[str], bool] = _boot_vga,
) -> Optional[GpuInfo]:
    """Pick the GPU that renders the desktop.

    1. The OpenGL renderer, when glxinfo is installed (also right for NVIDIA PRIME "performance" mode).
    2. Otherwise the GPU with a connected display, then the one the firmware booted on, then the first.
    On a hybrid laptop in on-demand mode this is the integrated GPU; the discrete one is listed in ``others``.
    """
    cards = gpus(devices)
    if not cards:
        return None
    renderer = opengl_renderer() if renderer is None else renderer
    software = bool(re.search(r"llvmpipe|softpipe|swrast", renderer, re.I))

    chosen: Optional[Device] = None
    source = ""
    if renderer and not software:
        vid = _vendor_of_renderer(renderer)
        same = [d for d in cards if d.vendor_id.lower() == vid]
        if same:
            shown = [d for d in same if drives_display(d.slot)]
            chosen, source = (shown or same)[0], "OpenGL renderer"
    if chosen is None:
        for test, why in ((drives_display, "connected display"), (boot_vga, "boot GPU")):
            hit = [d for d in cards if test(d.slot)]
            if hit:
                chosen, source = hit[0], why
                break
    if chosen is None:
        chosen, source = cards[0], "only GPU" if len(cards) == 1 else "first GPU"

    name, specific = gpu_name(chosen.vendor_id, renderer) if source == "OpenGL renderer" else ("", False)
    alternatives = ""
    if not specific:
        name, _ = gpu_name(chosen.vendor_id, chosen.name)
        alternatives = ambiguous_models(chosen.name)
    others = [(gpu_name(d.vendor_id, d.name)[0], d.driver) for d in cards if d is not chosen]
    if software:
        source += "; the desktop uses software rendering (%s)" % renderer.split(" (")[0]
    return GpuInfo(chosen, name, chosen.vendor_id.lower(), source, renderer, others, alternatives)
