"""Distribution detection: Kali, Debian, MX Linux, LMDE or other."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

FAMILIES = ("kali", "debian", "mx", "lmde", "other")
FAMILY_LABELS = {
    "kali": "Kali Linux",
    "debian": "Debian",
    "mx": "MX Linux",
    "lmde": "LMDE",
    "other": "Other",
}


@dataclass
class DistroInfo:
    family: str
    id: str = ""
    name: str = ""
    pretty_name: str = ""
    version: str = ""
    codename: str = ""
    id_like: str = ""
    debian_based: bool = True
    debian_version: str = ""
    raw: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return FAMILY_LABELS.get(self.family, "Other")


def parse_os_release(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def _read(root: str, rel: str) -> str:
    try:
        with open(os.path.join(root, rel.lstrip("/")), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def detect(root: str = "/") -> DistroInfo:
    """Detect the running distribution. ``root`` lets tests point at a fake tree."""
    text = _read(root, "/etc/os-release") or _read(root, "/usr/lib/os-release")
    osr = parse_os_release(text)
    lsb = parse_os_release(_read(root, "/etc/lsb-release"))
    mint_info = parse_os_release(_read(root, "/etc/linuxmint/info"))
    debian_version = _read(root, "/etc/debian_version").strip()

    os_id = osr.get("ID", "").lower()
    like = osr.get("ID_LIKE", "").lower()
    name = osr.get("NAME", "")
    pretty = osr.get("PRETTY_NAME", name)

    if (
        os.path.exists(os.path.join(root, "etc/mx-version"))
        or os_id == "mx"
        or lsb.get("DISTRIB_ID", "").lower() == "mx"
    ):
        family = "mx"
        mxv = _read(root, "/etc/mx-version").strip()
        if mxv and "MX" not in pretty:
            pretty = mxv
    elif os_id == "kali" or "kali" in name.lower():
        family = "kali"
    elif (os_id == "linuxmint" and ("debian" in like or "lmde" in name.lower() or "lmde" in pretty.lower())) or (
        mint_info.get("EDITION", "").upper().startswith("LMDE")
    ):
        family = "lmde"
    elif os_id == "debian":
        family = "debian"
    else:
        family = "other"

    debian_based = bool(
        os_id == "debian" or "debian" in like or "ubuntu" in like or os_id == "ubuntu" or debian_version
    )

    return DistroInfo(
        family=family,
        id=os_id,
        name=name or family,
        pretty_name=pretty or name or family,
        version=osr.get("VERSION_ID", osr.get("VERSION", "")),
        codename=osr.get("VERSION_CODENAME", osr.get("DEBIAN_CODENAME", "")),
        id_like=like,
        debian_based=debian_based,
        debian_version=debian_version,
        raw=osr,
    )
