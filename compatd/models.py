# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Shared data structures."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

SEVERITIES = ("critical", "warning", "info")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Action:
    """A fix that can be applied. Commands are argv lists, never shell strings."""

    label: str
    commands: List[List[str]]
    needs_root: bool = True
    # apt packages this action installs (used for dry-run simulation)
    packages: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    severity: str  # critical | warning | info
    category: str  # e.g. "Missing library", "Driver", "Architecture"
    target: str  # path or device
    title: str
    detail: str = ""
    replacement: str = ""
    action: Optional[Action] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Device:
    bus: str  # pci | usb
    slot: str
    class_code: str  # hex, e.g. "0300" (pci) or "e0" (usb)
    class_name: str
    vendor_id: str
    device_id: str
    vendor: str
    name: str
    driver: Optional[str] = None
    modules: List[str] = field(default_factory=list)
    modalias: str = ""
    interface_classes: List[str] = field(default_factory=list)

    @property
    def ids(self) -> str:
        return "%s:%s" % (self.vendor_id, self.device_id)

    @property
    def label(self) -> str:
        return "%s %s" % (self.vendor, self.name)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    key: str
    title: str
    reason: str
    packages: List[str]
    devices: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    status: str = "available"  # installed | available | partial | unavailable
    recommended: bool = True
    optional: bool = False
    notes: List[str] = field(default_factory=list)
    requires_components: List[str] = field(default_factory=list)
    post_commands: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
