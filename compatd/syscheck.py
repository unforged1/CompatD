# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Whole-device compatibility checks (drivers, firmware, apt health, repositories)."""
from __future__ import annotations

import os
import re
from typing import Callable, List, Optional

from . import hardware, packages
from .distro import DistroInfo
from .models import Action, Device, Finding
from .sysutil import kernel_release, read_text, run, which

# Firmware path prefix -> package alternatives
FIRMWARE_PACKAGES = [
    ("iwlwifi-", ["firmware-iwlwifi"]),
    ("intel/ibt-", ["firmware-iwlwifi"]),
    ("intel/sof", ["firmware-sof-signed"]),
    ("intel/", ["firmware-intel-misc", "firmware-misc-nonfree"]),
    ("i915/", ["firmware-intel-graphics", "firmware-misc-nonfree"]),
    ("xe/", ["firmware-intel-graphics", "firmware-misc-nonfree"]),
    ("amdgpu/", ["firmware-amd-graphics"]),
    ("radeon/", ["firmware-amd-graphics"]),
    ("amd/", ["firmware-amd-graphics", "firmware-misc-nonfree"]),
    ("nvidia/", ["firmware-nvidia-graphics", "firmware-misc-nonfree"]),
    ("rtl_nic/", ["firmware-realtek"]),
    ("rtl_bt/", ["firmware-realtek"]),
    ("rtlwifi/", ["firmware-realtek"]),
    ("rtw88/", ["firmware-realtek"]),
    ("rtw89/", ["firmware-realtek"]),
    ("brcm/", ["firmware-brcm80211"]),
    ("ath", ["firmware-atheros"]),
    ("qca/", ["firmware-atheros"]),
    ("mediatek/", ["firmware-mediatek", "firmware-misc-nonfree"]),
    ("mt7", ["firmware-mediatek", "firmware-misc-nonfree"]),
    ("sof", ["firmware-sof-signed"]),
    ("ti-connectivity/", ["firmware-ti-connectivity"]),
    ("cirrus/", ["firmware-cirrus", "firmware-misc-nonfree"]),
    ("qcom/", ["firmware-qcom-soc", "firmware-atheros"]),
    ("regulatory.db", ["wireless-regdb"]),
]

UBUNTU_CODENAMES = {"bionic", "focal", "jammy", "lunar", "mantic", "noble", "oracular", "plucky", "questing", "xenial"}
DEBIAN_SUITES = {"stable", "testing", "unstable", "sid", "bullseye", "bookworm", "trixie", "forky", "duke", "oldstable", "experimental"}

ProgressFn = Callable[[int, str], None]

# Hosts of other distributions' archives. Their URLs can contain "/debian" too
# (e.g. Kali's mirrors, MX's repo), so they never count as a Debian archive.
NON_DEBIAN_HOSTS = ("kali.org", "kali.download", "linuxmint", "mxrepo", "mxlinux", "mx-linux")


def is_debian_archive(uri: str) -> bool:
    """True for deb.debian.org, security.debian.org and Debian mirrors.

    LMDE's mirror picker (and many users) select a regional mirror such as
    http://ftp.cz.debian.org/debian or http://debian.example.net/debian, so
    matching "debian.org" alone misses them. Mirrors serve the archive at a
    /debian or /debian-security path.
    """
    uri = uri.lower().rstrip("/")
    if any(h in uri for h in NON_DEBIAN_HOSTS):
        return False
    if "debian.org" in uri:
        return True
    path = re.sub(r"^[a-z0-9+]+://[^/]*", "", uri)
    return any(seg in ("debian", "debian-security") for seg in path.split("/"))


def firmware_package(fw: str) -> Optional[List[str]]:
    for prefix, pkgs in FIRMWARE_PACKAGES:
        if fw.startswith(prefix):
            return pkgs
    return None


def check_unbound_devices(devices: List[Device]) -> List[Finding]:
    out: List[Finding] = []
    blacklist = hardware.blacklisted_modules()
    for dev in devices:
        if dev.driver or not hardware.is_important(dev):
            continue
        if dev.bus == "usb" and not dev.interface_classes:
            continue
        mods = hardware.resolve_modalias(dev.modalias) or dev.modules
        blocked = [m for m in mods if m.replace("-", "_") in blacklist]
        is_gpu = dev.bus == "pci" and dev.class_code.startswith("03")
        sev = "critical" if (is_gpu or dev.class_code.startswith("02")) else "warning"
        if dev.vendor_id == "10de" and is_gpu:
            nv = packages.installed_versions(["nvidia-driver", "nvidia-kernel-dkms", "nvidia-open-kernel-dkms"])
            if not any(nv.values()):
                detail = "No driver is bound to this GPU."
                if blocked:
                    detail += " The open-source 'nouveau' driver is blacklisted (%s), but the NVIDIA driver is not installed, so the GPU is unusable." % blacklist.get(blocked[0], "")
                out.append(
                    Finding(
                        sev,
                        "Driver",
                        "%s [%s]" % (dev.label, dev.slot),
                        "NVIDIA GPU has no working driver",
                        detail,
                        "Install the NVIDIA driver (see the Drivers page), or remove the nouveau blacklist to use the open-source driver.",
                        Action(
                            "Install NVIDIA driver",
                            [["apt-get", "update"], ["apt-get", "install", "-y", "nvidia-driver", "linux-headers-" + kernel_release()]],
                            packages=["nvidia-driver", "linux-headers-" + kernel_release()],
                        ),
                    )
                )
                continue
            out.append(
                Finding(
                    sev,
                    "Driver",
                    "%s [%s]" % (dev.label, dev.slot),
                    "NVIDIA driver installed but not loaded",
                    "The nvidia module is not bound. The DKMS build may have failed for kernel %s, or a reboot is pending." % kernel_release(),
                    "Reboot; if it persists reinstall kernel headers and rebuild with 'dkms autoinstall'.",
                    Action(
                        "Rebuild NVIDIA module",
                        [["apt-get", "install", "-y", "--reinstall", "linux-headers-" + kernel_release(), "nvidia-kernel-dkms"]],
                        packages=["linux-headers-" + kernel_release()],
                    ),
                )
            )
            continue
        if blocked:
            out.append(
                Finding(
                    "warning",
                    "Driver",
                    "%s [%s]" % (dev.label, dev.slot),
                    "Driver '%s' is blacklisted" % blocked[0],
                    "Blacklisted in %s and no other driver is bound." % blacklist.get(blocked[0], "?"),
                    "Remove the blacklist entry or install an alternative driver.",
                )
            )
        elif mods:
            out.append(
                Finding(
                    "warning",
                    "Driver",
                    "%s [%s]" % (dev.label, dev.slot),
                    "Driver '%s' available but not loaded" % mods[0],
                    "The kernel module exists for %s but is not bound." % kernel_release(),
                    "Load the module.",
                    Action("Load %s" % mods[0], [["modprobe", mods[0]]]),
                )
            )
        else:
            out.append(
                Finding(
                    sev if dev.bus == "pci" else "info",
                    "Driver",
                    "%s [%s]" % (dev.label, dev.slot),
                    "No driver for %s" % dev.class_name.lower(),
                    "ID %s - no module in kernel %s claims this device." % (dev.ids, kernel_release()),
                    "Search for an out-of-tree (DKMS) driver for %s, or use a newer kernel (backports)." % dev.ids,
                )
            )
    return out


def check_firmware(log: Optional[str] = None) -> List[Finding]:
    out: List[Finding] = []
    for fw in hardware.missing_firmware(log):
        alts = firmware_package(fw)
        pkg, installed = packages.pick_available(alts) if alts else (None, False)
        action = Action("Install %s" % pkg, [["apt-get", "install", "-y", pkg]], packages=[pkg]) if pkg and not installed else None
        out.append(
            Finding(
                "warning",
                "Firmware",
                fw,
                "Missing firmware %s" % fw,
                "The kernel failed to load this firmware during boot.",
                ("Install %s (from non-free-firmware)." % pkg) if pkg else "Firmware not packaged; check the vendor or linux-firmware upstream.",
                action,
            )
        )
    return out


def check_repositories(distro: DistroInfo) -> List[Finding]:
    out: List[Finding] = []
    sources = packages.apt_sources()
    debs = [s for s in sources if s["type"] == "deb"]
    if not debs:
        return [Finding("warning", "Repositories", "/etc/apt", "No apt sources found", "", "Configure apt sources for your distribution.")]
    for s in debs:
        uri = str(s["uri"])
        suite = str(s["suite"]).split("/")[0].split("-")[0]
        target = "%s (%s)" % (s["file"], uri)
        if "ppa.launchpad" in uri or suite in UBUNTU_CODENAMES:
            out.append(
                Finding(
                    "critical",
                    "Repositories",
                    target,
                    "Ubuntu repository on a Debian-based system",
                    "Suite '%s' from %s. Mixing Ubuntu packages into Debian ('FrankenDebian') breaks upgrades." % (s["suite"], uri),
                    "Remove this source and use Debian packages, backports or Flatpak instead.",
                )
            )
        elif distro.family == "kali" and is_debian_archive(uri):
            out.append(
                Finding(
                    "critical",
                    "Repositories",
                    target,
                    "Debian repository added to Kali",
                    "Kali is based on Debian testing; adding Debian repos causes dependency breakage.",
                    "Remove this source; Kali's own repo (kali-rolling) is the only supported one.",
                )
            )
    suites = {str(s["suite"]).split("/")[0].split("-")[0] for s in debs if is_debian_archive(str(s["uri"]))}
    releases = suites & DEBIAN_SUITES
    if len(releases) > 1 and distro.family in ("debian", "mx", "lmde"):
        out.append(
            Finding(
                "warning",
                "Repositories",
                "/etc/apt",
                "Mixed Debian releases: %s" % ", ".join(sorted(releases)),
                "Mixing releases without pinning can pull in incompatible library versions.",
                "Keep a single release (plus its -updates/-security/-backports) or add apt pinning.",
            )
        )
    comps = packages.enabled_components()
    if "non-free-firmware" not in comps and distro.family in ("debian", "mx", "lmde", "kali"):
        out.append(
            Finding(
                "info",
                "Repositories",
                "/etc/apt",
                "non-free-firmware component not enabled",
                "Most Wi-Fi, GPU and CPU-microcode firmware lives in non-free-firmware (Debian 12+).",
                "Add 'non-free-firmware' (and 'contrib non-free' for NVIDIA) to the Components of your sources.",
            )
        )
    return out


_KERNEL_PKG = re.compile(r"^linux-(?:image|headers|kbuild|modules|modules-extra|support)-(\d+)\.(\d+)(?:\.(\d+))?")


def _kernel_version(text: str) -> Optional[tuple]:
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(x or 0) for x in m.groups()) if m else None


def old_kernel_packages(pkgs: List[str], running: str) -> List[str]:
    """Kernel packages for versions strictly older than the running kernel.

    Newer ones are left alone: they may be a custom build or an upgrade that
    is waiting for a reboot.
    """
    cur = _kernel_version(running)
    if cur is None:
        return []
    out = []
    for pkg in pkgs:
        m = _KERNEL_PKG.match(pkg)
        if m and tuple(int(x or 0) for x in m.groups()) < cur:
            out.append(pkg)
    return out


def check_dpkg_health() -> List[Finding]:
    out: List[Finding] = []
    rc, audit, _ = run(["dpkg", "--audit"], timeout=60)
    if audit.strip():
        out.append(
            Finding(
                "critical",
                "Packages",
                "dpkg",
                "Broken or half-installed packages",
                audit.strip()[:2000],
                "Finish configuration and repair dependencies.",
                Action("Repair packages", [["dpkg", "--configure", "-a"], ["apt-get", "-f", "install", "-y"]]),
            )
        )
    rc, check_out, check_err = run(["apt-get", "check", "-q"], timeout=60)
    if rc != 0 and "unmet" in (check_out + check_err).lower():
        out.append(
            Finding(
                "critical",
                "Packages",
                "apt",
                "Unmet dependencies",
                (check_out + check_err).strip()[:2000],
                "Let apt repair the dependency tree.",
                Action("Fix dependencies", [["apt-get", "-f", "install", "-y"]]),
            )
        )
    rc, held, _ = run(["apt-mark", "showhold"], timeout=30)
    if held.strip():
        out.append(
            Finding(
                "info",
                "Packages",
                "apt",
                "%d package(s) on hold" % len(held.split()),
                held.strip(),
                "Held packages are not upgraded; release them with 'apt-mark unhold' when no longer needed.",
            )
        )
    rc, out_list, _ = run(["apt", "list", "--installed"], timeout=90)
    local = [line.split("/")[0] for line in out_list.splitlines() if line.endswith("[installed,local]")]
    old_kernels = old_kernel_packages(local, kernel_release())
    if old_kernels:
        out.append(
            Finding(
                "info",
                "Packages",
                "linux-image",
                "%d package(s) from older kernels" % len(old_kernels),
                ", ".join(old_kernels),
                "These kernels were dropped from the archive and are no longer updated. You are running %s. "
                "Remove them to free space in /boot, or keep the newest one as a fallback if you have not tested the running kernel yet."
                % kernel_release(),
                Action("Remove old kernels", [["apt-get", "purge", "-y"] + old_kernels], packages=old_kernels),
            )
        )
        local = [p for p in local if p not in old_kernels]
    if local:
        out.append(
            Finding(
                "info",
                "Packages",
                "apt",
                "%d installed package(s) not available from any repository" % len(local),
                ", ".join(local[:40]) + (" …" if len(local) > 40 else ""),
                "These are obsolete or manually installed .debs; they get no security updates. Look for maintained replacements.",
            )
        )
    return out


def check_kernel_modules(devices: List[Device]) -> List[Finding]:
    out: List[Finding] = []
    rel = kernel_release()
    has_headers = os.path.isdir("/lib/modules/%s/build" % rel) or packages.is_installed("linux-headers-" + rel)
    if which("dkms"):
        rc, status, _ = run(["dkms", "status"], timeout=60)
        bad = [l for l in status.splitlines() if l.strip() and rel in l and "installed" not in l]
        missing_for_kernel = []
        modules = {l.split(",")[0].split(":")[0].strip() for l in status.splitlines() if l.strip()}
        for mod in modules:
            if not any(rel in l and l.startswith(mod) and "installed" in l for l in status.splitlines()):
                missing_for_kernel.append(mod)
        if bad or missing_for_kernel:
            out.append(
                Finding(
                    "critical",
                    "Kernel module",
                    "dkms",
                    "DKMS modules not built for kernel %s" % rel,
                    "\n".join(bad) or ("Not built: " + ", ".join(sorted(missing_for_kernel))),
                    "Install the headers for the running kernel and rebuild.",
                    Action(
                        "Rebuild DKMS modules",
                        [["apt-get", "install", "-y", "linux-headers-" + rel], ["dkms", "autoinstall", "-k", rel]],
                        packages=["linux-headers-" + rel],
                    ),
                )
            )
        if not has_headers and modules:
            out.append(
                Finding(
                    "warning",
                    "Kernel module",
                    "linux-headers-" + rel,
                    "Kernel headers missing for DKMS",
                    "DKMS cannot build modules without headers for %s." % rel,
                    "Install the matching headers.",
                    Action("Install headers", [["apt-get", "install", "-y", "linux-headers-" + rel]], packages=["linux-headers-" + rel]),
                )
            )
    if hardware.secure_boot_state():
        third_party = [m for m in hardware.loaded_modules() if m in ("nvidia", "vboxdrv", "wl", "zfs")]
        nv = packages.is_installed("nvidia-kernel-dkms") or packages.is_installed("nvidia-open-kernel-dkms")
        if nv and "nvidia" not in third_party:
            out.append(
                Finding(
                    "warning",
                    "Secure Boot",
                    "nvidia",
                    "Secure Boot may block the NVIDIA module",
                    "Secure Boot is enabled and out-of-tree modules must be signed with an enrolled MOK key.",
                    "Enrol the DKMS signing key with 'mokutil --import /var/lib/dkms/mok.pub' and reboot, or disable Secure Boot.",
                )
            )
    if os.path.exists("/var/run/reboot-required"):
        out.append(Finding("info", "System", "/var/run/reboot-required", "Reboot required", read_text("/var/run/reboot-required.pkgs").strip(), "Reboot to finish updates."))
    modeset = read_text("/sys/module/nvidia_drm/parameters/modeset").strip()
    if modeset == "N" and os.environ.get("XDG_SESSION_TYPE") == "wayland":
        out.append(
            Finding(
                "warning",
                "Display",
                "nvidia_drm",
                "NVIDIA DRM modesetting disabled under Wayland",
                "Wayland compositors need nvidia-drm.modeset=1.",
                "Add 'options nvidia-drm modeset=1' to /etc/modprobe.d/nvidia.conf and reboot.",
            )
        )
    return out


def run_all(distro: DistroInfo, devices: Optional[List[Device]] = None, progress: Optional[ProgressFn] = None) -> List[Finding]:
    steps = [
        ("Checking device drivers", lambda: check_unbound_devices(devices if devices is not None else hardware.all_devices())),
        ("Checking firmware", check_firmware),
        ("Checking repositories", lambda: check_repositories(distro)),
        ("Checking package health", check_dpkg_health),
        ("Checking kernel modules", lambda: check_kernel_modules(devices or [])),
    ]
    findings: List[Finding] = []
    for i, (label, fn) in enumerate(steps):
        if progress:
            progress(i, label)
        try:
            findings.extend(fn())
        except Exception as exc:
            findings.append(Finding("info", "Scan", label, "Check failed", str(exc)))
    if progress:
        progress(len(steps), "")
    return findings
