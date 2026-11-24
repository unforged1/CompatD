# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Hardware discovery from sysfs, lspci and friends."""
from __future__ import annotations

import glob
import os
import re
from typing import Dict, List, Optional

from .models import Device
from .sysutil import kernel_release, read_text, run, which

PCI_ROOT = "/sys/bus/pci/devices"
USB_ROOT = "/sys/bus/usb/devices"

PCI_CLASS_NAMES = {
    "01": "Storage controller",
    "02": "Network controller",
    "03": "Display controller",
    "04": "Multimedia controller",
    "05": "Memory controller",
    "06": "Bridge",
    "07": "Communication controller",
    "08": "System peripheral",
    "09": "Input device",
    "0c": "Serial bus controller",
    "0d": "Wireless controller",
    "10": "Encryption controller",
    "11": "Signal processing",
    "12": "Processing accelerator",
}

USB_CLASS_NAMES = {
    "01": "Audio",
    "02": "Communications",
    "03": "Human interface",
    "06": "Imaging",
    "07": "Printer",
    "08": "Mass storage",
    "09": "Hub",
    "0a": "CDC data",
    "0b": "Smart card",
    "0e": "Video",
    "10": "Audio/Video",
    "e0": "Wireless (Bluetooth)",
    "ef": "Miscellaneous",
    "fe": "Application specific",
    "ff": "Vendor specific",
}

# PCI class prefixes for which a missing driver is worth reporting.
IMPORTANT_PCI_CLASSES = ("01", "02", "03", "04", "07", "0c03", "0c80", "0d", "12")

_BRACKET_ID = re.compile(r"^(?P<name>.*?)\s*\[(?P<id>[0-9a-fA-F]{2,6})\]$")


def _split_name_id(value: str):
    m = _BRACKET_ID.match(value.strip())
    if m:
        return m.group("name").strip(), m.group("id").lower()
    return value.strip(), ""


def _sysfs_pci(slot: str, attr: str) -> str:
    return read_text(os.path.join(PCI_ROOT, slot, attr)).strip()


def _pci_driver(slot: str) -> Optional[str]:
    link = os.path.join(PCI_ROOT, slot, "driver")
    if os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return None


def pci_devices() -> List[Device]:
    devices: List[Device] = []
    rc, out, _ = run(["lspci", "-vmm", "-nn", "-k", "-D"], timeout=20) if which("lspci") else (127, "", "")
    if rc == 0 and out.strip():
        for block in out.strip().split("\n\n"):
            fields: Dict[str, List[str]] = {}
            for line in block.splitlines():
                if ":\t" in line:
                    k, v = line.split(":\t", 1)
                    fields.setdefault(k, []).append(v)
            slot = fields.get("Slot", [""])[0]
            if not slot:
                continue
            cls_name, cls_id = _split_name_id(fields.get("Class", [""])[0])
            vendor, vid = _split_name_id(fields.get("Vendor", [""])[0])
            name, did = _split_name_id(fields.get("Device", [""])[0])
            full_class = _sysfs_pci(slot, "class")
            if full_class.startswith("0x"):
                cls_id = full_class[2:6]
            driver = fields.get("Driver", [None])[0] or _pci_driver(slot)
            devices.append(
                Device(
                    bus="pci",
                    slot=slot,
                    class_code=cls_id,
                    class_name=cls_name,
                    vendor_id=vid,
                    device_id=did,
                    vendor=vendor,
                    name=name,
                    driver=driver,
                    modules=fields.get("Module", []),
                    modalias=_sysfs_pci(slot, "modalias"),
                )
            )
        return devices

    # Fallback: raw sysfs, no human names.
    for path in sorted(glob.glob(os.path.join(PCI_ROOT, "*"))):
        slot = os.path.basename(path)
        cls = _sysfs_pci(slot, "class")[2:6]
        devices.append(
            Device(
                bus="pci",
                slot=slot,
                class_code=cls,
                class_name=PCI_CLASS_NAMES.get(cls[:2], "PCI device"),
                vendor_id=_sysfs_pci(slot, "vendor")[2:],
                device_id=_sysfs_pci(slot, "device")[2:],
                vendor=_sysfs_pci(slot, "vendor"),
                name=_sysfs_pci(slot, "device"),
                driver=_pci_driver(slot),
                modalias=_sysfs_pci(slot, "modalias"),
            )
        )
    return devices


def usb_devices() -> List[Device]:
    devices: List[Device] = []
    for path in sorted(glob.glob(os.path.join(USB_ROOT, "*"))):
        base = os.path.basename(path)
        if ":" in base or base.startswith("usb"):
            continue
        vid = read_text(os.path.join(path, "idVendor")).strip()
        pid = read_text(os.path.join(path, "idProduct")).strip()
        if not vid or vid == "1d6b":  # skip root hubs
            continue
        dev_class = read_text(os.path.join(path, "bDeviceClass")).strip()
        drivers: List[str] = []
        iface_classes: List[str] = []
        modalias = ""
        for iface in sorted(glob.glob(os.path.join(path, base + ":*"))):
            ic = read_text(os.path.join(iface, "bInterfaceClass")).strip()
            if ic:
                iface_classes.append(ic)
            if not modalias:
                modalias = read_text(os.path.join(iface, "modalias")).strip()
            link = os.path.join(iface, "driver")
            if os.path.islink(link):
                d = os.path.basename(os.readlink(link))
                if d not in drivers:
                    drivers.append(d)
        effective_class = dev_class if dev_class not in ("00", "") else (iface_classes[0] if iface_classes else "00")
        devices.append(
            Device(
                bus="usb",
                slot=base,
                class_code=effective_class,
                class_name=USB_CLASS_NAMES.get(effective_class, "USB device"),
                vendor_id=vid,
                device_id=pid,
                vendor=read_text(os.path.join(path, "manufacturer")).strip() or vid,
                name=read_text(os.path.join(path, "product")).strip() or pid,
                driver=", ".join(drivers) or None,
                modalias=modalias,
                interface_classes=iface_classes,
            )
        )
    return devices


def all_devices() -> List[Device]:
    return pci_devices() + usb_devices()


def is_important(dev: Device) -> bool:
    if dev.bus == "pci":
        return dev.class_code.startswith(IMPORTANT_PCI_CLASSES)
    return dev.class_code not in ("09",)


def resolve_modalias(modalias: str) -> List[str]:
    """Which kernel modules claim this modalias (installed for the running kernel)."""
    if not modalias:
        return []
    rc, out, _ = run(["modprobe", "--resolve-alias", modalias], timeout=10)
    if rc != 0:
        return []
    return [m.strip() for m in out.splitlines() if m.strip()]


def loaded_modules() -> List[str]:
    return [line.split()[0] for line in read_text("/proc/modules").splitlines() if line.strip()]


def blacklisted_modules() -> Dict[str, str]:
    """Return {module: source} for modules blacklisted via modprobe.d or kernel cmdline."""
    result: Dict[str, str] = {}
    for pattern in ("/etc/modprobe.d/*.conf", "/usr/lib/modprobe.d/*.conf", "/lib/modprobe.d/*.conf"):
        for path in glob.glob(pattern):
            for line in read_text(path).splitlines():
                parts = line.split("#", 1)[0].split()
                if len(parts) >= 2 and parts[0] == "blacklist":
                    result.setdefault(parts[1].replace("-", "_"), path)
                elif len(parts) >= 3 and parts[0] == "install" and parts[2] in ("/bin/false", "/bin/true"):
                    result.setdefault(parts[1].replace("-", "_"), path)
    cmdline = read_text("/proc/cmdline")
    for tok in cmdline.split():
        if tok.startswith(("modprobe.blacklist=", "module_blacklist=")):
            for mod in tok.split("=", 1)[1].split(","):
                result.setdefault(mod.replace("-", "_"), "/proc/cmdline")
    return result


def cpu_info() -> Dict[str, str]:
    info = {"vendor": "", "model": "", "cores": "0"}
    text = read_text("/proc/cpuinfo")
    cores = 0
    for line in text.splitlines():
        if line.startswith("vendor_id") and not info["vendor"]:
            info["vendor"] = line.split(":", 1)[1].strip()
        elif line.startswith("model name") and not info["model"]:
            info["model"] = line.split(":", 1)[1].strip()
        elif line.startswith("processor"):
            cores += 1
    info["cores"] = str(cores or os.cpu_count() or 0)
    return info


def memory_total_kib() -> int:
    for line in read_text("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


def secure_boot_state() -> Optional[bool]:
    """True/False if determinable, None otherwise (legacy BIOS or no access)."""
    for path in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            return bool(data and data[-1] == 1)
        except OSError:
            break
    if which("mokutil"):
        rc, out, _ = run(["mokutil", "--sb-state"], timeout=10)
        if "enabled" in out.lower():
            return True
        if "disabled" in out.lower():
            return False
    return None


def boot_mode() -> str:
    return "UEFI" if os.path.isdir("/sys/firmware/efi") else "Legacy BIOS"


def kernel_log() -> str:
    """Kernel messages for this boot. Tries journalctl, then dmesg (may need adm group)."""
    rc, out, _ = run(["journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat"], timeout=30)
    if rc == 0 and out.strip():
        return out
    rc, out, _ = run(["dmesg"], timeout=15)
    return out if rc == 0 else ""


_FW_PATTERNS = (
    re.compile(r"Direct firmware load for (\S+) failed"),
    re.compile(r"firmware: failed to load (\S+)"),
    re.compile(r"[Ff]ailed to load firmware[: ]+\"?([\w./-]+\.(?:bin|fw|ucode|sbin|pnvm|zst|xz))"),
    re.compile(r"possible missing firmware (\S+)"),
)


def missing_firmware(log: Optional[str] = None) -> List[str]:
    log = kernel_log() if log is None else log
    found: List[str] = []
    for line in log.splitlines():
        for pat in _FW_PATTERNS:
            m = pat.search(line)
            if m:
                fw = m.group(1).strip("'\",()")
                if fw.startswith("/lib/firmware/"):
                    fw = fw[len("/lib/firmware/"):]
                # Firmware that loads from another path afterwards is not missing.
                if fw not in found and not _firmware_present(fw):
                    found.append(fw)
    return found


def _firmware_present(fw: str) -> bool:
    for base in ("/lib/firmware", "/usr/lib/firmware", "/lib/firmware/updates"):
        for ext in ("", ".zst", ".xz"):
            if os.path.exists(os.path.join(base, fw + ext)):
                return True
    return False


def session_info() -> Dict[str, str]:
    return {
        "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", os.environ.get("DESKTOP_SESSION", "unknown")),
    }


def summary() -> Dict[str, str]:
    cpu = cpu_info()
    sb = secure_boot_state()
    sess = session_info()
    from .sysutil import dpkg_arch, human_size

    return {
        "kernel": kernel_release(),
        "arch": dpkg_arch(),
        "cpu": cpu["model"] or cpu["vendor"],
        "cpu_vendor": cpu["vendor"],
        "cores": cpu["cores"],
        "memory": human_size(memory_total_kib() * 1024),
        "boot": boot_mode(),
        "secure_boot": "unknown" if sb is None else ("enabled" if sb else "disabled"),
        "session": sess["session"],
        "desktop": sess["desktop"],
    }
