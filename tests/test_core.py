"""Unit tests for CompatD's non-GUI logic. Run with: python3 -m unittest discover -s tests"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from compatd import distro, elfinfo, packages, themes  # noqa: E402
from compatd.actions import build_argv  # noqa: E402
from compatd.config import Config  # noqa: E402
from compatd.models import Action, Finding  # noqa: E402
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
