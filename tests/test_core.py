# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""Unit tests for CompatD's non-GUI logic. Run with: python3 -m unittest discover -s tests"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compatd import distro, elfinfo, gpu, packages, perf, testreport, themes  # noqa: E402
from compatd.actions import build_argv  # noqa: E402
from compatd.config import Config  # noqa: E402
from compatd.models import Action, Device, Finding  # noqa: E402
from compatd.report import write_report  # noqa: E402
from compatd.scanner import ScanOptions, Scanner, _unmet_depends, fix_all_action  # noqa: E402
from compatd import syscheck  # noqa: E402
from compatd.syscheck import firmware_package, is_debian_archive, old_kernel_packages  # noqa: E402


def fake_root(files):
    root = tempfile.mkdtemp(prefix="compatd-root-")
    for rel, content in files.items():
        path = os.path.join(root, rel.lstrip("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    return root


class DistroTests(unittest.TestCase):
    def check(self, files, family):
        root = fake_root(files)
        try:
            self.assertEqual(distro.detect(root).family, family)
        finally:
            shutil.rmtree(root)

    def test_kali(self):
        self.check({"/etc/os-release": 'ID=kali\nID_LIKE=debian\nNAME="Kali GNU/Linux"\n'}, "kali")

    def test_debian(self):
        self.check({"/etc/os-release": 'ID=debian\nNAME="Debian GNU/Linux"\nVERSION_CODENAME=trixie\n', "/etc/debian_version": "13.1\n"}, "debian")

    def test_mx(self):
        # MX ships Debian's os-release; /etc/mx-version is the tell.
        self.check({"/etc/os-release": 'ID=debian\nNAME="Debian GNU/Linux"\n', "/etc/mx-version": "MX-23.4_x64 Libretto\n"}, "mx")

    def test_lmde(self):
        self.check({"/etc/os-release": 'ID=linuxmint\nID_LIKE=debian\nNAME="LMDE"\nPRETTY_NAME="LMDE 6 (faye)"\n'}, "lmde")

    def test_other(self):
        self.check({"/etc/os-release": 'ID=ubuntu\nID_LIKE=debian\nNAME="Ubuntu"\n'}, "other")
        self.check({"/etc/os-release": 'ID=fedora\nNAME="Fedora Linux"\n'}, "other")

    def test_quoting(self):
        d = distro.parse_os_release("A='x y'\nB=\"z\"\n# c\nC=plain\n")
        self.assertEqual(d, {"A": "x y", "B": "z", "C": "plain"})


class ElfTests(unittest.TestCase):
    def test_parse_host_binary(self):
        info = elfinfo.parse(os.path.realpath(sys.executable))
        self.assertIn(info.etype, (elfinfo.ET_EXEC, elfinfo.ET_DYN))
        self.assertTrue(info.arch)
        self.assertTrue(info.interp)
        self.assertTrue(any(lib.startswith("libc.so") for lib in info.needed) or info.needed)

    def test_verdef_of_libc(self):
        from compatd.libindex import get_index
        from compatd.sysutil import dpkg_arch

        path = get_index().find("libc.so.6", dpkg_arch())
        self.assertIsNotNone(path)
        self.assertTrue(any(v.startswith("GLIBC_2.") for v in elfinfo.parse(path).verdef))

    def test_not_elf(self):
        with tempfile.NamedTemporaryFile(suffix=".bin") as fh:
            fh.write(b"hello world" * 10)
            fh.flush()
            with self.assertRaises(elfinfo.ElfError):
                elfinfo.parse(fh.name)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="compatd-scan-")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, data, mode=0o644):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode())
        os.chmod(path, mode)
        return path

    def scan(self, **opts):
        return Scanner(ScanOptions(**opts)).scan(self.dir)

    def titles(self, findings):
        return [f.title for f in findings]

    def test_scripts(self):
        self.write("a.sh", "#!/bin/sh\r\necho\r\n", 0o755)
        self.write("b.sh", "#!/opt/homebrew/bin/bash\necho\n", 0o755)
        self.write("c.scpt", "#!/usr/bin/osascript\n", 0o755)
        self.write("ok.sh", "#!/bin/sh\necho ok\n", 0o755)
        f = self.scan()
        titles = " | ".join(self.titles(f))
        self.assertIn("CRLF", titles)
        self.assertIn("'/opt/homebrew/bin/bash' does not exist", titles)
        self.assertIn("'osascript' not installed", titles)
        self.assertEqual(len(f), 3)

    def test_python2(self):
        self.write("old.py", 'print "hello"\n')
        self.write("new.py", 'print("hello")\n')
        f = self.scan()
        self.assertEqual([x.category for x in f], ["Python 2"])

    def test_foreign_and_launchers(self):
        self.write("putty.exe", b"MZ\x90\x00" + b"\x00" * 100)
        self.write("pkg.rpm", b"\xed\xab\xee\xdb" + b"\x00" * 100)
        self.write("app.desktop", "[Desktop Entry]\nName=x\nExec=/nonexistent/prog %U\n")
        self.write("x.service", "[Service]\nExecStart=/nonexistent/daemon --flag\n")
        os.symlink("/nonexistent/target", os.path.join(self.dir, "dead"))
        cats = sorted(x.category for x in self.scan())
        self.assertEqual(cats, ["Broken link", "Foreign format", "Launcher", "Service", "Windows program"])

    def test_hidden_skipped(self):
        self.write(".hidden/a.sh", "#!/bin/sh\r\n", 0o755)
        self.assertEqual(self.scan(), [])
        self.assertEqual(len(self.scan(skip_hidden=False)), 1)

    def test_foreign_arch_elf(self):
        with open(os.path.realpath(sys.executable), "rb") as fh:
            data = bytearray(fh.read(4096 * 4))
        struct.pack_into("<H", data, 18, 183 if elfinfo.MACHINES.get(62) and os.uname().machine == "x86_64" else 62)
        self.write("foreign_bin", bytes(data), 0o755)
        f = self.scan()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].category, "Architecture")
        self.assertEqual(f[0].severity, "critical")

    @unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
    def test_missing_library(self):
        src = self.write("l.c", "void f(void){}\n")
        main = self.write("m.c", "int main(void){return 0;}\n")
        lib = os.path.join(self.dir, "libcompatdgone.so.7")
        subprocess.check_call(["gcc", "-shared", "-fPIC", "-Wl,-soname,libcompatdgone.so.7", "-o", lib, src])
        os.makedirs(os.path.join(self.dir, "bin"))
        exe = os.path.join(self.dir, "bin", "prog")
        subprocess.check_call(["gcc", "-o", exe, main, "-Wl,--no-as-needed", lib])
        os.remove(lib)
        for p in (src, main):
            os.remove(p)
        f = self.scan()
        self.assertEqual(len(f), 1, self.titles(f))
        self.assertEqual(f[0].category, "Missing library")
        self.assertIn("libcompatdgone.so.7", f[0].title)

    def test_newer_glibc_detected(self):
        """Patch a binary's verneed entry to ask for a glibc version that cannot exist."""
        exe = os.path.realpath(sys.executable)
        info = elfinfo.parse(exe)
        if "libc.so.6" not in info.verneed:
            self.skipTest("interpreter has no libc verneed")
        with open(exe, "rb") as fh:
            data = bytearray(fh.read())
        old = max((v for v in info.verneed["libc.so.6"] if v.startswith("GLIBC_2.")), key=len).encode()
        new = (b"GLIBC_9." + b"9" * 20)[: len(old)]
        idx = data.find(old + b"\x00")
        self.assertGreater(idx, 0)
        data[idx: idx + len(old)] = new
        self.write("newglibc", bytes(data), 0o755)
        f = self.scan()
        self.assertTrue(any(x.category == "Library too old" for x in f), self.titles(f))

    def test_fix_all_merges_root_actions_only(self):
        fs = [
            Finding("critical", "x", "a", "t", action=Action("a", [["apt-get", "install", "-y", "p1"]], packages=["p1"])),
            Finding("warning", "x", "b", "t", action=Action("b", [["apt-get", "install", "-y", "p2", "p1"]], packages=["p2", "p1"])),
            Finding("warning", "x", "c", "t", action=Action("c", [["sed", "-i", "x", "f"]], needs_root=False)),
            Finding("critical", "x", "d", "t", action=Action("d", [["dpkg", "--add-architecture", "i386"], ["apt-get", "update"]], packages=["libc6:i386"])),
        ]
        act = fix_all_action(fs)
        self.assertEqual(act.commands[0], ["dpkg", "--add-architecture", "i386"])
        self.assertEqual(act.commands[1], ["apt-get", "update"])
        self.assertEqual(act.commands[2], ["apt-get", "install", "-y", "p1", "p2"])
        self.assertNotIn(["sed", "-i", "x", "f"], act.commands)


    @unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
    def test_plugin_finds_bundle_libs(self):
        """A dlopen'd plugin deep inside an app bundle uses the bundle's private libs."""
        src = self.write("l.c", "void f(void){}\n")
        app = os.path.join(self.dir, "app")
        plugdir = os.path.join(app, "plugins", "x", "0.1")
        os.makedirs(plugdir)
        lib = os.path.join(app, "libcompatdbundled.so")
        subprocess.check_call(["gcc", "-shared", "-fPIC", "-Wl,-soname,libcompatdbundled.so", "-o", lib, src])
        subprocess.check_call(["gcc", "-shared", "-fPIC", "-o", os.path.join(plugdir, "libplug.so"), src, "-Wl,--no-as-needed", lib])
        os.remove(src)
        self.assertEqual(self.scan(), [])
        # Without the bundle lib the plugin is still reported.
        os.remove(lib)
        f = self.scan()
        self.assertEqual([x.category for x in f], ["Missing library"], self.titles(f))


class PackageTests(unittest.TestCase):
    def test_soname_guesses(self):
        self.assertIn("libssl1.1", packages.soname_package_guesses("libssl.so.1.1"))
        self.assertIn("libfoo3t64", packages.soname_package_guesses("libfoo.so.3"))
        self.assertIn("libgtk-3-0", packages.soname_package_guesses("libgtk-3.so.0"))

    def test_installed_query(self):
        res = packages.installed_versions(["dpkg", "compatd-surely-not-a-package"])
        self.assertTrue(res["dpkg"])
        self.assertIsNone(res["compatd-surely-not-a-package"])

    def test_unmet_depends(self):
        self.assertEqual(_unmet_depends("dpkg, libc6 (>= 2.0) | foo"), [])
        self.assertEqual(_unmet_depends("compatd-nonexistent-pkg-xyz"), ["compatd-nonexistent-pkg-xyz"])
        self.assertEqual(_unmet_depends("dpkg (>= 999:1)"), ["dpkg (>= 999:1)"])

    def test_firmware_map(self):
        self.assertEqual(firmware_package("iwlwifi-ty-a0-gf-a0-89.ucode"), ["firmware-iwlwifi"])
        self.assertEqual(firmware_package("rtw89/rtw8852b_fw.bin"), ["firmware-realtek"])
        self.assertIsNone(firmware_package("totally/unknown.bin"))


class RepoTests(unittest.TestCase):
    def test_debian_archive_detection(self):
        for uri in ("http://deb.debian.org/debian", "http://security.debian.org/", "http://ftp.cz.debian.org/debian/",
                    "http://debian.superhosting.cz/debian", "https://mirror.example.net/debian-security"):
            self.assertTrue(is_debian_archive(uri), uri)
        for uri in ("http://http.kali.org/kali", "https://mirror.karneval.cz/pub/linux/linuxmint/packages",
                    "https://repo.steampowered.com/steam/", "http://mxrepo.com/mx/repo/", "https://kali.download/debian"):
            self.assertFalse(is_debian_archive(uri), uri)

    def _check(self, sources, family="lmde"):
        real = packages.apt_sources
        packages.apt_sources = lambda: [dict(file="/etc/apt/test.list", type="deb", uri=u, suite=s, components=["main", "non-free-firmware"]) for u, s in sources]
        try:
            return [f.title for f in syscheck.check_repositories(distro.DistroInfo(family=family))]
        finally:
            packages.apt_sources = real

    def test_lmde_mirror_mixed_release(self):
        lmde = [("https://mirror.karneval.cz/pub/linux/linuxmint/packages", "gigi"),
                ("http://debian.superhosting.cz/debian", "trixie"),
                ("http://debian.superhosting.cz/debian", "trixie-backports"),
                ("http://security.debian.org/", "trixie-security"),
                ("https://repo.steampowered.com/steam/", "stable")]
        self.assertFalse([t for t in self._check(lmde) if "Mixed" in t])
        mixed = self._check(lmde + [("http://debian.superhosting.cz/debian", "bookworm")])
        self.assertIn("Mixed Debian releases: bookworm, trixie", mixed)

    def test_debian_mirror_on_kali(self):
        titles = self._check([("http://http.kali.org/kali", "kali-rolling"), ("http://ftp.de.debian.org/debian", "trixie")], "kali")
        self.assertEqual(titles.count("Debian repository added to Kali"), 1)

    def test_old_kernels(self):
        pkgs = ["czgram", "linux-image-6.12.48+deb13-amd64", "linux-headers-6.12.48+deb13-common", "linux-kbuild-6.12.48+deb13",
                "linux-image-6.15.2-custom", "linux-image-amd64", "linux-image-6.12.117+deb13-amd64"]
        self.assertEqual(
            old_kernel_packages(pkgs, "6.12.117+deb13-amd64"),
            ["linux-image-6.12.48+deb13-amd64", "linux-headers-6.12.48+deb13-common", "linux-kbuild-6.12.48+deb13"],
        )


def gpu_dev(slot, vid, name, driver):
    return Device("pci", slot, "0300", "VGA compatible controller", vid, "0000", "", name, driver)


class GpuTests(unittest.TestCase):
    def test_names(self):
        cases = [
            ("10de", "AD107M [GeForce RTX 4060 Max-Q / Mobile]", "NVIDIA RTX 4060"),
            ("10de", "NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2", "NVIDIA RTX 4060"),
            ("10de", "TU116 [GeForce GTX 1660 SUPER]", "NVIDIA GTX 1660 SUPER"),
            ("10de", "GA104 [GeForce RTX 3070 Ti]", "NVIDIA RTX 3070 TI"),
            ("10de", "GP107GL [Quadro P1000]", "NVIDIA QUADRO P1000"),
            ("8086", "DG2 [Arc A770]", "INTEL ARC A770"),
            ("8086", "Mesa Intel(R) Arc(tm) A750 Graphics (DG2)", "INTEL ARC A750"),
            ("8086", "TigerLake-LP GT2 [Iris Xe Graphics]", "INTEL IRIS XE"),
            ("8086", "Mesa Intel(R) UHD Graphics 620 (KBL GT2)", "INTEL UHD 620"),
            ("1002", "Rembrandt [Radeon 680M]", "RADEON 680M"),
            ("1002", "AMD Radeon RX 7900 XTX (radeonsi, navi31, LLVM 19)", "RADEON RX 7900 XTX"),
            ("1002", "Renoir [Radeon Vega Series / Radeon Vega Mobile Series]", "RADEON VEGA"),
        ]
        for vid, text, want in cases:
            self.assertEqual(gpu.gpu_name(vid, text)[0], want, text)
        self.assertFalse(gpu.gpu_name("1002", "AMD Radeon Graphics (radeonsi, renoir)")[1])

    def test_hybrid_uses_renderer(self):
        amd = gpu_dev("0000:35:00.0", "1002", "Rembrandt [Radeon 680M]", "amdgpu")
        nv = gpu_dev("0000:01:00.0", "10de", "AD107M [GeForce RTX 4060 Max-Q / Mobile]", "nvidia")
        no = lambda slot: False  # noqa: E731
        info = gpu.active_gpu([nv, amd], renderer="AMD Radeon 680M (radeonsi, rembrandt)", drives_display=no, boot_vga=no)
        self.assertEqual((info.name, info.source, info.others), ("RADEON 680M", "OpenGL renderer", [("NVIDIA RTX 4060", "nvidia")]))
        # PRIME "performance" mode: NVIDIA renders the desktop although the iGPU drives the panel.
        info = gpu.active_gpu([nv, amd], renderer="NVIDIA GeForce RTX 4060 Laptop GPU/PCIe/SSE2",
                              drives_display=lambda s: s == amd.slot, boot_vga=no)
        self.assertEqual(info.name, "NVIDIA RTX 4060")

    def test_fallbacks_without_glxinfo(self):
        intel = gpu_dev("0000:00:02.0", "8086", "Alder Lake-P GT2 [Iris Xe Graphics]", "i915")
        amd = gpu_dev("0000:03:00.0", "1002", "Navi 21 [Radeon RX 6800/6800 XT / 6900 XT]", "amdgpu")
        info = gpu.active_gpu([intel, amd], renderer="", drives_display=lambda s: s == amd.slot, boot_vga=lambda s: s == intel.slot)
        self.assertEqual((info.name, info.source), ("RADEON RX 6800", "connected display"))
        self.assertIn("6900 XT", info.alternatives)
        info = gpu.active_gpu([intel, amd], renderer="", drives_display=lambda s: False, boot_vga=lambda s: s == intel.slot)
        self.assertEqual((info.name, info.source), ("INTEL IRIS XE", "boot GPU"))
        info = gpu.active_gpu([intel], renderer="llvmpipe (LLVM 19.1.7, 256 bits)", drives_display=lambda s: False, boot_vga=lambda s: False)
        self.assertIn("software rendering", info.source)
        self.assertIsNone(gpu.active_gpu([], renderer=""))


class GpuRoleTests(unittest.TestCase):
    def setUp(self):
        bridge = lambda slot, name: Device("pci", slot, "0604", "PCI bridge", "8086", "0000", "", name)  # noqa: E731
        self.bridges = [
            bridge("0000:00:08.1", "Family 17h-19h Internal PCIe GPP Bridge"),
            bridge("0000:00:01.1", "Family 17h-19h PCIe GPP Bridge"),
            bridge("0000:05:00.0", "JHL7540 Thunderbolt 3 Bridge [Titan Ridge 4C 2018]"),
            bridge("0000:06:01.0", "JHL7540 Thunderbolt 3 Bridge [Titan Ridge 4C 2018]"),
        ]
        self.parents = {
            "0000:35:00.0": ["0000:00:08.1"],
            "0000:01:00.0": ["0000:00:01.1"],
            "0000:07:00.0": ["0000:06:01.0", "0000:05:00.0", "0000:00:1c.4"],
            "0000:00:02.0": [],
        }

    def roles(self, cards, removable=lambda s: False):
        entries = gpu.all_gpus(self.bridges + cards, None, ancestors=lambda s: self.parents.get(s, []), removable=removable)
        return [(e.role, e.name) for e in entries]

    def test_amd_apu_plus_nvidia(self):
        cards = [gpu_dev("0000:01:00.0", "10de", "AD107M [GeForce RTX 4060 Max-Q / Mobile]", "nvidia"),
                 gpu_dev("0000:35:00.0", "1002", "Rembrandt [Radeon 680M]", "amdgpu")]
        self.assertEqual(self.roles(cards), [("iGPU", "RADEON 680M"), ("dGPU", "NVIDIA RTX 4060")])

    def test_intel_igpu_dgpu_and_thunderbolt_egpu(self):
        cards = [gpu_dev("0000:00:02.0", "8086", "Alder Lake-P GT2 [Iris Xe Graphics]", "i915"),
                 gpu_dev("0000:01:00.0", "8086", "DG2 [Arc A770]", "i915"),
                 gpu_dev("0000:07:00.0", "1002", "Navi 31 [Radeon RX 7900 XT/7900 XTX/7900 GRE/7900M]", None)]
        self.assertEqual(self.roles(cards), [("iGPU", "INTEL IRIS XE"), ("dGPU", "INTEL ARC A770"), ("eGPU", "RADEON RX 7900 XT")])

    def test_egpu_from_removable_flag(self):
        # USB4 enclosure whose bridges have no telling name: the kernel's removable flag decides.
        self.parents["0000:09:00.0"] = ["0000:08:00.0"]
        card = gpu_dev("0000:09:00.0", "10de", "GA102 [GeForce RTX 3080]", "nvidia")
        self.assertEqual(self.roles([card], removable=lambda s: s == "0000:09:00.0"), [("eGPU", "NVIDIA RTX 3080")])
        self.assertEqual(self.roles([card]), [("dGPU", "NVIDIA RTX 3080")])

    def test_amd_apu_by_code_name_and_vm(self):
        self.parents["0000:04:00.0"] = ["0000:00:08.1x"]
        apu = gpu_dev("0000:04:00.0", "1002", "Cezanne [Radeon Vega Series / Radeon Vega Mobile Series]", "amdgpu")
        vm = gpu_dev("0000:00:0f.0", "15ad", "SVGA II Adapter", "vmwgfx")
        self.assertEqual(self.roles([apu, vm]), [("iGPU", "RADEON VEGA"), ("vGPU", "VMWARE VIRTUAL GPU")])


class TestReportTests(unittest.TestCase):
    FORM = os.path.join(os.path.dirname(__file__), "..", ".github", "ISSUE_TEMPLATE", "hardware_report.yml")

    def test_fields_match_issue_form(self):
        with open(self.FORM) as fh:
            form = fh.read()
        ids = set(re.findall(r"^\s+id: (\w+)", form, re.M))
        for key in list(testreport.FIELDS) + ["distro"]:
            self.assertIn(key, ids)
        for option in testreport.DISTRO_OPTIONS.values():
            self.assertIn("- " + option, form)

    def test_scrub(self):
        words = [("/home/alice", "~"), ("alice", "<user>"), ("alice-laptop", "<host>")]
        # Longest first: a host name containing the user name is still replaced whole.
        self.assertEqual(testreport.scrub("/home/alice/x on alice-laptop by alice", words), "~/x on <host> by <user>")
        self.assertEqual(testreport.scrub("nothing here", words), "nothing here")

    def test_issue_url_prefills_and_shortens(self):
        report = {"title": "Hardware report: Debian 13", "distro": "Debian", "info": "kernel: 6.12", "drivers": "x" * 50,
                  "system": "No findings.", "performance": "fast"}
        url, short = testreport.issue_url(report)
        self.assertFalse(short)
        self.assertTrue(url.startswith("https://github.com/unforged1/CompatD/issues/new?template=hardware_report.yml&"))
        self.assertIn("title=Hardware+report%3A+Debian+13", url)
        self.assertNotIn("distro=", url)  # GitHub can't pre-fill dropdowns
        self.assertIn("info=kernel%3A+6.12", url)
        report["drivers"] = "\n".join("device line %d with a fairly long description" % i for i in range(600))
        url, short = testreport.issue_url(report)
        self.assertTrue(short)
        self.assertLessEqual(len(url), testreport.MAX_URL)
        self.assertIn("clipboard", url.replace("+", " "))
        self.assertIn("info=kernel%3A+6.12", url)  # small fields are kept whole

    def test_perf_errors_and_text(self):
        perf.reset()
        try:
            {}["missing"]
        except KeyError as exc:
            perf.error("test", exc)
        perf.record("Directory scan", 2.0, "1000 files")
        text = testreport._performance()
        self.assertIn("Errors this session: 1", text)
        self.assertIn("KeyError", text)
        self.assertIn("500 files/s", text)
        perf.reset()


class MiscTests(unittest.TestCase):
    def test_themes_render_for_all_families(self):
        for fam in distro.FAMILIES:
            for mode in ("theme", "dark", "light"):
                css = themes.css(fam, mode)
                self.assertIn("@define-color cd_accent", css)

    def test_dry_run_argv_needs_no_root(self):
        argv = build_argv(Action("x", [["apt-get", "update"], ["apt-get", "install", "-y", "foo"]]), dry_run=True)
        self.assertEqual(argv[:2], ["/bin/sh", "-c"])
        self.assertIn("apt-get -s install -y foo", argv[2])
        self.assertNotIn("pkexec", argv[0])

    def test_dry_run_executes(self):
        argv = build_argv(Action("x", [["touch", "/should-not-exist"]]), dry_run=True)
        out = subprocess.run(argv, stdout=subprocess.PIPE, check=True).stdout.decode()
        self.assertIn("[dry-run] would run: touch /should-not-exist", out)
        self.assertFalse(os.path.exists("/should-not-exist"))

    def test_config_roundtrip(self):
        d = tempfile.mkdtemp()
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = d
        try:
            c = Config()
            c.theme = "mx"
            c.max_files = 1234
            c.save()
            c2 = Config.load()
            self.assertEqual((c2.theme, c2.max_files), ("mx", 1234))
            with open(os.path.join(d, "compatd", "config.json"), "w") as fh:
                fh.write('{"theme": 5, "dry_run": true}')
            c3 = Config.load()
            self.assertEqual(c3.theme, "auto")  # wrong type ignored
            self.assertTrue(c3.dry_run)
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME")
            else:
                os.environ["XDG_CONFIG_HOME"] = old
            shutil.rmtree(d)

    def test_report(self):
        d = tempfile.mkdtemp()
        try:
            fs = [Finding("critical", "Cat", "/x", "Broken", "detail", "replace", Action("fix", [["apt-get", "install", "-y", "a"]]))]
            write_report(os.path.join(d, "r.md"), fs, "scope", "Test OS")
            write_report(os.path.join(d, "r.json"), fs, "scope", "Test OS")
            md = open(os.path.join(d, "r.md")).read()
            self.assertIn("## Critical (1)", md)
            self.assertIn("apt-get install -y a", md)
            import json

            self.assertEqual(json.load(open(os.path.join(d, "r.json")))["findings"][0]["title"], "Broken")
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
