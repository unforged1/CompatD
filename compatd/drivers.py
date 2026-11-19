"""Driver recommendation engine.

Rules map detected hardware to Debian packages. Each rule lists *slots*, and each slot
is a list of alternative package names (newest naming first) - the first one apt knows
about is used, so the same rule works on Debian 12/13, Kali rolling, MX and LMDE.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import hardware, packages
from .distro import DistroInfo
from .models import Device, Recommendation
from .sysutil import dpkg_arch, kernel_release, run, which

NONFREE = ["non-free", "non-free-firmware"]
NONFREE_CONTRIB = ["contrib", "non-free", "non-free-firmware"]


@dataclass
class Rule:
    key: str
    title: str
    reason: str
    match: Callable[[Device], bool]
    slots: List[List[str]]
    components: List[str] = field(default_factory=list)
    optional: bool = False
    notes: List[str] = field(default_factory=list)
    families: Optional[List[str]] = None  # restrict to distro families


def _pci(vendor: str, cls_prefix: str) -> Callable[[Device], bool]:
    return lambda d: d.bus == "pci" and d.vendor_id == vendor and d.class_code.startswith(cls_prefix)


def _usb(vendor: str, cls: Optional[str] = None) -> Callable[[Device], bool]:
    return lambda d: d.bus == "usb" and d.vendor_id == vendor and (cls is None or d.class_code == cls or cls in d.interface_classes)


def _headers_slot() -> List[str]:
    rel = kernel_release()
    arch = dpkg_arch()
    flavour = rel.rsplit("-", 1)[-1] if "-" in rel else arch
    return ["linux-headers-" + rel, "linux-headers-" + flavour, "linux-headers-" + arch]


def build_rules(distro: DistroInfo) -> List[Rule]:
    is_wifi = lambda d: d.bus == "pci" and (d.class_code.startswith("0280") or d.class_code.startswith("0d"))
    is_net = lambda d: d.bus == "pci" and d.class_code.startswith("02")
    rules = [
        Rule(
            "nvidia",
            "NVIDIA proprietary graphics driver",
            "NVIDIA GPU detected. The proprietary driver gives full 3D/Vulkan/CUDA support and power management.",
            _pci("10de", "03"),
            [["nvidia-driver"], ["firmware-nvidia-graphics", "firmware-misc-nonfree"], _headers_slot()],
            NONFREE_CONTRIB,
            notes=[
                "A reboot is required after installation.",
                "With Secure Boot enabled the DKMS module must be signed (MOK enrolment).",
                "Open-source alternative: nouveau (in-kernel, limited performance).",
            ],
        ),
        Rule(
            "nvidia-cuda",
            "NVIDIA CUDA toolkit",
            "Kali recommends the CUDA toolkit for GPU-accelerated tools such as hashcat.",
            _pci("10de", "03"),
            [["nvidia-cuda-toolkit"]],
            NONFREE_CONTRIB,
            optional=True,
            families=["kali"],
            notes=["Large download (several GB)."],
        ),
        Rule(
            "amdgpu",
            "AMD graphics firmware and Mesa",
            "AMD/ATI GPU detected. amdgpu needs firmware blobs plus Mesa for OpenGL/Vulkan/VA-API.",
            _pci("1002", "03"),
            [
                ["firmware-amd-graphics"],
                ["libgl1-mesa-dri"],
                ["mesa-vulkan-drivers"],
                ["mesa-va-drivers"],
            ],
            NONFREE,
        ),
        Rule(
            "intel-gpu",
            "Intel graphics firmware and media driver",
            "Intel GPU detected. GuC/HuC firmware and the VA-API media driver enable hardware video decoding.",
            _pci("8086", "03"),
            [
                ["firmware-intel-graphics", "firmware-misc-nonfree"],
                ["intel-media-va-driver-non-free", "intel-media-va-driver"],
                ["mesa-vulkan-drivers"],
            ],
            NONFREE,
        ),
        Rule(
            "iwlwifi",
            "Intel Wi-Fi / Bluetooth firmware",
            "Intel wireless adapter detected; it does not work without iwlwifi firmware.",
            lambda d: (_pci("8086", "02")(d) or _pci("8086", "0d")(d)) or _usb("8087", "e0")(d),
            [["firmware-iwlwifi"]],
            NONFREE,
        ),
        Rule(
            "realtek",
            "Realtek network / Wi-Fi / Bluetooth firmware",
            "Realtek network hardware detected (r8169, rtw88/rtw89, rtl8xxxu, btrtl).",
            lambda d: (d.bus == "pci" and d.vendor_id == "10ec" and is_net(d))
            or _usb("0bda")(d)
            or (d.bus == "usb" and "realtek" in d.vendor.lower() and d.class_code == "e0"),
            [["firmware-realtek"]],
            NONFREE,
        ),
        Rule(
            "broadcom",
            "Broadcom Wi-Fi firmware",
            "Broadcom wireless adapter detected (brcmfmac/brcmsmac). Older BCM43xx chips may need broadcom-sta-dkms instead.",
            lambda d: d.bus == "pci" and d.vendor_id == "14e4" and is_wifi(d),
            [["firmware-brcm80211"]],
            NONFREE,
            notes=["If the adapter is still not detected, try the 'broadcom-sta-dkms' package (contrib/non-free)."],
        ),
        Rule(
            "atheros",
            "Qualcomm Atheros firmware",
            "Qualcomm/Atheros wireless adapter detected (ath9k/ath10k/ath11k/ath12k).",
            lambda d: (d.bus == "pci" and d.vendor_id in ("168c", "17cb") and is_net(d)) or _usb("0cf3")(d),
            [["firmware-atheros"]],
            NONFREE,
        ),
        Rule(
            "mediatek",
            "MediaTek Wi-Fi / Bluetooth firmware",
            "MediaTek wireless adapter detected (mt76 / mt7921 / mt7922).",
            lambda d: (d.bus == "pci" and d.vendor_id == "14c3" and is_net(d))
            or _usb("0e8d", "e0")(d)
            or (d.bus == "usb" and d.vendor_id in ("0489", "13d3") and d.class_code == "e0" and "realtek" not in d.vendor.lower()),
            [["firmware-mediatek", "firmware-misc-nonfree"]],
            NONFREE,
        ),
        Rule(
            "sof",
            "Sound Open Firmware (laptop audio DSP)",
            "Audio DSP detected that needs SOF firmware for speakers and microphones.",
            lambda d: d.bus == "pci" and d.class_code.startswith("04") and any(
                m.startswith(("snd_sof", "snd_pci_acp", "snd_soc_avs")) for m in d.modules + [d.driver or ""]
            ),
            [["firmware-sof-signed"], ["alsa-ucm-conf"]],
            NONFREE,
        ),
        Rule(
            "bluetooth",
            "Bluetooth stack",
            "Bluetooth controller detected; BlueZ provides the daemon and pairing tools.",
            lambda d: d.bus == "usb" and (d.class_code == "e0" or "e0" in d.interface_classes),
            [["bluez"], ["bluez-firmware"]],
            optional=True,
        ),
        Rule(
            "printer",
            "Printer drivers (CUPS)",
            "USB printer detected.",
            lambda d: d.bus == "usb" and (d.class_code == "07" or "07" in d.interface_classes),
            [["cups"], ["printer-driver-all"]],
        ),
        Rule(
            "scanner",
            "Scanner support (SANE)",
            "USB imaging device detected.",
            lambda d: d.bus == "usb" and (d.class_code == "06" or "06" in d.interface_classes),
            [["sane-utils"], ["simple-scan"]],
            optional=True,
        ),
    ]
    return [r for r in rules if r.families is None or distro.family in r.families]


def _nvidia_detect_package() -> Optional[str]:
    """Use Debian's nvidia-detect, when installed, to pick the right driver branch."""
    if not which("nvidia-detect"):
        return None
    rc, out, _ = run(["nvidia-detect"], timeout=20)
    m = re.search(r"install the\s+(\S+)\s+package", out)
    return m.group(1) if m else None


def _cpu_rule(distro: DistroInfo) -> Optional[Rule]:
    vendor = hardware.cpu_info()["vendor"]
    if vendor == "AuthenticAMD":
        pkg = "amd64-microcode"
    elif vendor == "GenuineIntel":
        pkg = "intel-microcode"
    else:
        return None
    return Rule(
        "microcode",
        "CPU microcode updates",
        "Security and stability fixes for your %s CPU, loaded early at boot." % ("AMD" if "AMD" in vendor else "Intel"),
        lambda d: True,
        [[pkg]],
        NONFREE,
    )


def _resolve(rule: Rule, devices: List[Device], comps: set) -> Recommendation:
    pkgs: List[str] = []
    missing: List[str] = []
    unavailable: List[str] = []
    for slot in rule.slots:
        pkg, installed = packages.pick_available(slot)
        if pkg is None:
            unavailable.append(slot[0])
            continue
        pkgs.append(pkg)
        if not installed:
            missing.append(pkg)
    if not pkgs:
        status = "unavailable"
    elif not missing and not unavailable:
        status = "installed"
    elif missing and len(missing) < len(pkgs):
        status = "partial"
    elif not missing and unavailable:
        status = "partial"
    else:
        status = "available"
    notes = list(rule.notes)
    need_comps = [c for c in rule.components if c not in comps]
    if unavailable and need_comps:
        notes.insert(0, "Enable the apt components: %s" % " ".join(need_comps))
    return Recommendation(
        key=rule.key,
        title=rule.title,
        reason=rule.reason,
        packages=pkgs,
        devices=[d.label for d in devices],
        missing=missing,
        unavailable=unavailable,
        status=status,
        recommended=not rule.optional and status in ("available", "partial"),
        optional=rule.optional,
        notes=notes,
        requires_components=rule.components,
    )


def recommend(devices: List[Device], distro: DistroInfo) -> List[Recommendation]:
    """Build the list of driver recommendations for the detected devices."""
    comps = packages.enabled_components()
    rules = build_rules(distro)

    nv_pkg = _nvidia_detect_package()
    if nv_pkg:
        for r in rules:
            if r.key == "nvidia":
                r.slots[0] = [nv_pkg, "nvidia-driver"]

    recs: List[Recommendation] = []
    for rule in rules:
        matched = [d for d in devices if rule.match(d)]
        if matched:
            recs.append(_resolve(rule, matched, comps))

    cpu = _cpu_rule(distro)
    if cpu:
        recs.append(_resolve(cpu, [], comps))

    # Devices with no driver bound for which the kernel has a module that is not loaded.
    blacklist = hardware.blacklisted_modules()
    covered = {d.slot for r in rules for d in devices if r.match(d)}
    for dev in devices:
        if dev.driver or not hardware.is_important(dev) or dev.slot in covered:
            continue
        mods = hardware.resolve_modalias(dev.modalias) or dev.modules
        mods = [m for m in mods if m.replace("-", "_") not in blacklist]
        if mods:
            recs.append(
                Recommendation(
                    key="module:%s" % mods[0],
                    title="Load kernel module '%s'" % mods[0],
                    reason="%s has no driver bound, but the running kernel ships a matching module." % dev.label,
                    packages=[],
                    devices=[dev.label],
                    status="available",
                    post_commands=[["modprobe", mods[0]]],
                )
            )

    order = {"available": 0, "partial": 1, "unavailable": 2, "installed": 3}
    recs.sort(key=lambda r: (r.optional, order.get(r.status, 9), r.title))
    return recs


def install_commands(recs: List[Recommendation]) -> List[List[str]]:
    pkgs: List[str] = []
    post: List[List[str]] = []
    for r in recs:
        for p in r.missing:
            if p not in pkgs:
                pkgs.append(p)
        post.extend(r.post_commands)
    cmds: List[List[str]] = []
    if pkgs:
        cmds.append(["apt-get", "update"])
        cmds.append(["apt-get", "install", "-y", *pkgs])
    cmds.extend(post)
    return cmds
