# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Index of shared libraries available on the host (from the ld.so cache)."""
from __future__ import annotations

import os
import re
import threading
from typing import Dict, List, Optional, Set

from . import elfinfo
from .sysutil import dpkg_arch, run, which

_LINE = re.compile(r"^\s*(\S+)\s+\(([^)]*)\)\s+=>\s+(\S+)")


class LibIndex:
    def __init__(self) -> None:
        self._by_name: Dict[str, List[tuple]] = {}
        self._verdef_cache: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        ldconfig = which("ldconfig") or "/sbin/ldconfig"
        rc, out, _ = run([ldconfig, "-p"], timeout=30)
        for line in out.splitlines():
            m = _LINE.match(line)
            if m:
                self._by_name.setdefault(m.group(1), []).append((m.group(2), m.group(3)))

    def soname_versions(self, prefix: str) -> List[str]:
        """Other installed sonames of the same library, e.g. libssl.so.3 for libssl.so.1.1."""
        stem = prefix.split(".so")[0] + ".so"
        return sorted(n for n in self._by_name if n.startswith(stem + ".") or n == stem)

    def find(self, soname: str, arch: str, extra_dirs: Optional[List[str]] = None) -> Optional[str]:
        for d in extra_dirs or []:
            p = os.path.join(d, soname)
            if os.path.exists(p):
                return p
        tag = elfinfo.LDCONFIG_TAGS.get(arch)
        for flags, path in self._by_name.get(soname, []):
            if tag is None or _flags_tag(flags) == tag:
                return path
        # ld.so default directories not always in the cache
        for d in _default_dirs(arch):
            p = os.path.join(d, soname)
            if os.path.exists(p):
                return p
        return None

    def provided_versions(self, path: str) -> Set[str]:
        real = os.path.realpath(path)
        with self._lock:
            if real in self._verdef_cache:
                return self._verdef_cache[real]
        try:
            versions = set(elfinfo.parse(real).verdef)
        except (OSError, elfinfo.ElfError):
            versions = set()
        with self._lock:
            self._verdef_cache[real] = versions
        return versions


def _flags_tag(flags: str) -> str:
    """'libc6,x86-64, OS ABI: Linux 3.2.0' -> 'libc6,x86-64'; 'libc6, OS ABI: ...' -> 'libc6'."""
    parts = [p.strip() for p in flags.split(",")]
    keep = [parts[0]] + [p for p in parts[1:2] if not p.startswith("OS ABI")]
    return ",".join(keep)


def _default_dirs(arch: str) -> List[str]:
    triplets = {
        "amd64": "x86_64-linux-gnu",
        "i386": "i386-linux-gnu",
        "arm64": "aarch64-linux-gnu",
        "armhf": "arm-linux-gnueabihf",
        "riscv64": "riscv64-linux-gnu",
        "ppc64el": "powerpc64le-linux-gnu",
        "s390x": "s390x-linux-gnu",
    }
    t = triplets.get(arch)
    dirs = []
    if t:
        dirs += ["/lib/" + t, "/usr/lib/" + t]
    if arch == dpkg_arch():
        dirs += ["/lib", "/usr/lib"]
    if arch == "i386":
        dirs += ["/lib32", "/usr/lib32"]
    return dirs


_INDEX: Optional[LibIndex] = None
_INDEX_LOCK = threading.Lock()


def get_index(refresh: bool = False) -> LibIndex:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None or refresh:
            _INDEX = LibIndex()
        return _INDEX
