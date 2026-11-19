"""Compatibility scanner for files in a directory.

Looks for things that will not run (or not run well) on this Debian-based system and
suggests a replacement or a fix for each one.
"""
from __future__ import annotations

import os
import re
import stat
import threading
import warnings
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from . import elfinfo, packages
from .libindex import get_index
from .models import Action, Finding
from .sysutil import dpkg_arch, foreign_archs, kernel_release, run, shell_join, which

PSEUDO_FS = ("/proc", "/sys", "/dev", "/run", "/tmp/.X11-unix", "/snap")


@dataclass
class ScanOptions:
    follow_symlinks: bool = False
    skip_hidden: bool = True
    max_file_mb: int = 256
    max_files: int = 200000
    check_elf: bool = True
    check_scripts: bool = True
    check_python: bool = True
    check_launchers: bool = True
    check_packages: bool = True
    check_foreign: bool = True
    report_broken_links: bool = True
    use_apt_file: bool = False
    exclude: List[str] = field(default_factory=lambda: list(PSEUDO_FS))


# --- knowledge tables ----------------------------------------------------------------

INTERPRETERS: Dict[str, Tuple[Optional[str], str]] = {
    "python": ("python-is-python3", "Debian only ships 'python3'. Install python-is-python3 or change the shebang to python3."),
    "python2": (None, "Python 2 is end-of-life and no longer packaged. Port the script to Python 3."),
    "python2.7": (None, "Python 2 is end-of-life and no longer packaged. Port the script to Python 3."),
    "python3": ("python3", "Install Python 3."),
    "perl": ("perl", "Install Perl."),
    "ruby": ("ruby", "Install Ruby."),
    "node": ("nodejs", "Install Node.js (package nodejs)."),
    "nodejs": ("nodejs", "Install Node.js."),
    "php": ("php-cli", "Install the PHP command-line interpreter."),
    "bash": ("bash", "Install bash."),
    "zsh": ("zsh", "Install zsh."),
    "ksh": ("ksh", "Install the Korn shell."),
    "mksh": ("mksh", "Install mksh."),
    "tcsh": ("tcsh", "Install tcsh."),
    "csh": ("csh", "Install csh."),
    "fish": ("fish", "Install the fish shell."),
    "lua": ("lua5.4", "Install Lua."),
    "tclsh": ("tcl", "Install Tcl."),
    "wish": ("tk", "Install Tk."),
    "expect": ("expect", "Install expect."),
    "Rscript": ("r-base-core", "Install R."),
    "gawk": ("gawk", "Install GNU awk."),
    "groovy": ("groovy", "Install Groovy."),
    "java": ("default-jre", "Install a Java runtime."),
    "pwsh": (None, "PowerShell is not in the Debian archive. Install it from Microsoft's repository or port the script to bash."),
    "osascript": (None, "AppleScript is macOS-only. Rewrite the script for Linux (bash/python3)."),
    "julia": (None, "Julia is not packaged for current Debian; use the official juliaup installer."),
    "deno": (None, "Deno is not packaged in Debian; install it from deno.land."),
    "bun": (None, "Bun is not packaged in Debian; install it from bun.sh."),
}

# Native alternatives for common Windows programs (lowercase filename substring -> suggestion)
WINDOWS_ALTERNATIVES: List[Tuple[str, str, Optional[str]]] = [
    ("putty", "PuTTY has a native build", "putty"),
    ("winscp", "FileZilla (SFTP/FTP client)", "filezilla"),
    ("notepad++", "Geany or Kate (code editors)", "geany"),
    ("7z", "7-Zip native build", "7zip"),
    ("winrar", "7-Zip / unrar", "7zip"),
    ("vlc", "VLC native build", "vlc"),
    ("wireshark", "Wireshark native build", "wireshark"),
    ("nmap", "Nmap native build", "nmap"),
    ("zenmap", "Nmap + zenmap", "zenmap"),
    ("burp", "Burp Suite (Linux installer / Kali package)", "burpsuite"),
    ("python", "Python 3", "python3"),
    ("chrome", "Chromium", "chromium"),
    ("firefox", "Firefox ESR", "firefox-esr"),
    ("thunderbird", "Thunderbird", "thunderbird"),
    ("gimp", "GIMP native build", "gimp"),
    ("inkscape", "Inkscape native build", "inkscape"),
    ("audacity", "Audacity native build", "audacity"),
    ("obs", "OBS Studio", "obs-studio"),
    ("keepass", "KeePassXC", "keepassxc"),
    ("vscode", "VS Code (.deb from Microsoft) or VSCodium", None),
    ("code", "VS Code (.deb from Microsoft) or VSCodium", None),
    ("steam", "Steam for Linux", "steam-installer"),
    ("discord", "Discord .deb from discord.com", None),
    ("teams", "Teams web app", None),
    ("office", "LibreOffice", "libreoffice"),
    ("winword", "LibreOffice Writer", "libreoffice-writer"),
    ("excel", "LibreOffice Calc", "libreoffice-calc"),
    ("acrord", "Okular / Evince (PDF viewers)", "okular"),
    ("foxit", "Okular / Evince (PDF viewers)", "okular"),
    ("hashcat", "hashcat native build", "hashcat"),
    ("john", "John the Ripper", "john"),
    ("ghidra", "Ghidra (Kali package / upstream zip)", "ghidra"),
    ("x64dbg", "gdb / edb-debugger", "edb-debugger"),
    ("ida", "Ghidra / radare2 (rizin)", "ghidra"),
    ("procmon", "htop / strace", "htop"),
    ("teamviewer", "TeamViewer .deb or Remmina", "remmina"),
    ("anydesk", "AnyDesk .deb or Remmina", "remmina"),
    ("mobaxterm", "Remmina + terminal SSH", "remmina"),
    ("rufus", "GNOME Disks / dd / balenaEtcher", "gnome-disk-utility"),
    ("etcher", "GNOME Disks / balenaEtcher AppImage", "gnome-disk-utility"),
    ("virtualbox", "VirtualBox / virt-manager", "virt-manager"),
    ("vmware", "VMware Workstation for Linux or virt-manager", "virt-manager"),
]

PACKAGE_FORMATS = {
    ".rpm": ("RPM package (Fedora/RHEL/openSUSE)", "Look for a .deb build of the software, or convert it with 'alien --to-deb' (may not work for complex packages).", "alien"),
    ".pkg.tar.zst": ("Arch Linux package", "Look for a .deb, Flatpak or AppImage build instead.", None),
    ".pkg.tar.xz": ("Arch Linux package", "Look for a .deb, Flatpak or AppImage build instead.", None),
    ".apk": ("Android / Alpine package", "Android apps need Waydroid; Alpine packages are not compatible with Debian.", None),
    ".dmg": ("macOS disk image", "macOS software does not run on Linux; look for a Linux build.", None),
    ".pkg": ("macOS installer package", "macOS software does not run on Linux; look for a Linux build.", None),
    ".msi": ("Windows installer", "Use a native Linux alternative, or run it with Wine.", "wine"),
    ".bat": ("Windows batch script", "Rewrite as a shell script (bash).", None),
    ".cmd": ("Windows batch script", "Rewrite as a shell script (bash).", None),
    ".ps1": ("PowerShell script", "Install PowerShell from Microsoft's repository or port to bash.", None),
    ".reg": ("Windows registry file", "Only meaningful inside a Wine prefix ('wine regedit file.reg').", None),
}

ProgressFn = Callable[[int, str], None]


class Scanner:
    def __init__(
        self,
        options: Optional[ScanOptions] = None,
        progress: Optional[ProgressFn] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        self.opt = options or ScanOptions()
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.host_arch = dpkg_arch()
        self.index = get_index()
        self.files_scanned = 0
        self._reported_global: set = set()

    # -- walking ------------------------------------------------------------------------
    def _walk(self, root: str) -> Iterator[str]:
        root = os.path.abspath(root)
        if os.path.isfile(root):
            yield root
            return
        stack = [root]
        seen = set()
        while stack:
            d = stack.pop()
            if self.cancel.is_set():
                return
            try:
                st = os.stat(d)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in sorted(entries, key=lambda x: x.name, reverse=True):
                if self.opt.skip_hidden and e.name.startswith("."):
                    continue
                path = e.path
                if any(path == x or path.startswith(x.rstrip("/") + "/") for x in self.opt.exclude if not root.startswith(x)):
                    continue
                try:
                    if e.is_symlink():
                        if not os.path.exists(path):
                            yield path  # broken link
                            continue
                        if not self.opt.follow_symlinks:
                            continue  # the target is scanned at its real location
                    if e.is_dir(follow_symlinks=self.opt.follow_symlinks):
                        stack.append(path)
                    elif e.is_file(follow_symlinks=self.opt.follow_symlinks):
                        yield path
                except OSError:
                    continue

    def scan(self, root: str) -> List[Finding]:
        findings: List[Finding] = []
        for path in self._walk(root):
            if self.cancel.is_set():
                findings.append(Finding("info", "Scan", root, "Scan cancelled", "Results are partial."))
                break
            if self.files_scanned >= self.opt.max_files:
                findings.append(
                    Finding("info", "Scan", root, "Scan stopped after %d files" % self.files_scanned, "Raise the file limit in Options to scan further.")
                )
                break
            self.files_scanned += 1
            if self.progress and self.files_scanned % 25 == 0:
                self.progress(self.files_scanned, path)
            try:
                findings.extend(self.check_file(path))
            except Exception as exc:  # never let one odd file kill the scan
                findings.append(Finding("info", "Scan", path, "Could not analyse file", str(exc)))
        if self.progress:
            self.progress(self.files_scanned, "")
        return findings

    # -- per-file dispatch ------------------------------------------------------------------
    def check_file(self, path: str) -> List[Finding]:
        if os.path.islink(path) and not os.path.exists(path):
            if not self.opt.report_broken_links:
                return []
            target = os.readlink(path)
            return [
                Finding(
                    "info",
                    "Broken link",
                    path,
                    "Broken symbolic link",
                    "Points to '%s', which does not exist." % target,
                    "Reinstall whatever provided the target, or remove the link.",
                    Action("Remove broken link", [["rm", "--", path]], needs_root=not _writable_dir(path)),
                )
            ]
        try:
            st = os.stat(path)
        except OSError:
            return []
        if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
            return []
        try:
            with open(path, "rb") as fh:
                head = fh.read(512)
        except OSError:
            return []
        lower = path.lower()
        out: List[Finding] = []

        if self.opt.check_elf and elfinfo.is_elf(head):
            return self.check_elf(path, st)
        if head[:2] == b"#!" and self.opt.check_scripts:
            out += self.check_shebang(path, head)
        if self.opt.check_python and lower.endswith(".py") and st.st_size <= self.opt.max_file_mb * 1024 * 1024:
            out += self.check_python(path)
        if self.opt.check_launchers and lower.endswith(".desktop"):
            out += self.check_desktop(path)
        if self.opt.check_launchers and lower.endswith(".service"):
            out += self.check_unit(path)
        if self.opt.check_packages:
            if head[:21] == b"!<arch>\ndebian-binary" or lower.endswith(".deb"):
                out += self.check_deb(path)
            elif head[:4] == b"\xed\xab\xee\xdb" or lower.endswith(".rpm"):
                out += self._package_format(path, ".rpm")
            elif lower.endswith(".snap"):
                out += self.check_snap(path)
            elif lower.endswith((".flatpakref", ".flatpak")):
                out += self.check_flatpak(path)
            elif lower.endswith(".jar"):
                out += self.check_jar(path)
        if self.opt.check_foreign:
            if head[:2] == b"MZ" and lower.endswith((".exe", ".scr", ".com")):
                out += self.check_windows(path, head)
            elif head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce") or (
                head[:4] == b"\xca\xfe\xba\xbe" and not lower.endswith(".class")
            ):
                out.append(
                    Finding(
                        "critical",
                        "Foreign binary",
                        path,
                        "macOS (Mach-O) executable",
                        "Mach-O binaries cannot run on Linux.",
                        "Look for a Linux build of this program or a native alternative.",
                    )
                )
            else:
                for ext, (label, repl, pkg) in PACKAGE_FORMATS.items():
                    if ext == ".rpm":
                        continue
                    if lower.endswith(ext):
                        out += self._package_format(path, ext)
                        break
        return out

    # -- ELF -------------------------------------------------------------------------------
    def check_elf(self, path: str, st: os.stat_result) -> List[Finding]:
        try:
            info = elfinfo.parse(path)
        except elfinfo.ElfError as exc:
            return [Finding("warning", "Corrupt binary", path, "Unreadable ELF file", str(exc), "Re-download or reinstall the file.")]
        except OSError:
            return []

        if info.is_appimage:
            return self.check_appimage(path, info, st)
        if info.etype == elfinfo.ET_REL:
            if path.endswith(".ko"):
                return self.check_kmod(path, info)
            return []
        if info.etype not in (elfinfo.ET_EXEC, elfinfo.ET_DYN):
            return []

        out: List[Finding] = []
        arch = info.arch
        if arch != self.host_arch:
            f = self._arch_finding(path, info)
            if f:
                out.append(f)
                if f.severity == "critical":
                    return out
        if info.truncated:
            out.append(Finding("warning", "Corrupt binary", path, "Truncated or damaged ELF file", "The file ends before its own headers do.", "Re-download or reinstall the file."))
            return out
        if info.is_static:
            return out

        if info.interp and not os.path.exists(info.interp):
            out.append(self._interp_finding(path, info))
            return out

        origin = os.path.dirname(path)
        dirs = [p.replace("$ORIGIN", origin).replace("${ORIGIN}", origin) for p in info.rpath + info.runpath]
        # Heuristic: bundled apps ship their libs next to the binary or in ../lib and
        # set LD_LIBRARY_PATH from a wrapper script.
        dirs += [origin, os.path.join(origin, "lib"), os.path.join(origin, "..", "lib"), os.path.join(origin, "libs")]
        missing = [lib for lib in info.needed if not self.index.find(lib, arch, dirs)]
        if missing:
            out.append(self._missing_libs_finding(path, info, missing))

        version_problems: List[str] = []
        for lib, versions in info.verneed.items():
            if lib in missing:
                continue
            lib_path = self.index.find(lib, arch, dirs)
            if not lib_path:
                continue
            provided = self.index.provided_versions(lib_path)
            if not provided:
                continue
            lacking = [v for v in versions if v not in provided]
            if lacking:
                version_problems.append("%s needs %s (system has up to %s)" % (lib, ", ".join(_newest(lacking, 3)), _newest(provided, 1)[0]))
        if version_problems:
            out.append(
                Finding(
                    "critical",
                    "Library too old",
                    path,
                    "Built for a newer system than this one",
                    "; ".join(version_problems),
                    "Install a build made for your release (check backports), use a Flatpak/AppImage of the program, "
                    "or rebuild it from source on this machine.",
                )
            )
        return out

    def _arch_finding(self, path: str, info: elfinfo.ElfInfo) -> Optional[Finding]:
        arch = info.arch
        pairs = {("amd64", "i386"), ("arm64", "armhf")}
        if (self.host_arch, arch) in pairs:
            loader = info.interp or ""
            if arch in foreign_archs() and (not loader or os.path.exists(loader)):
                return None
            cmds = []
            if arch not in foreign_archs():
                cmds.append(["dpkg", "--add-architecture", arch])
            cmds += [["apt-get", "update"], ["apt-get", "install", "-y", "libc6:%s" % arch]]
            return Finding(
                "critical",
                "Architecture",
                path,
                "%s binary needs %s multiarch support" % (info.arch_label, arch),
                "This system is %s and the %s runtime (multiarch) is not set up." % (self.host_arch, arch),
                "Enable the %s architecture and install its C library; or get a %s build." % (arch, self.host_arch),
                Action("Enable %s multiarch" % arch, cmds, packages=["libc6:%s" % arch]),
            )
        action = None
        if not packages.is_installed("qemu-user-binfmt") and not packages.is_installed("qemu-user-static"):
            pkg, _ = packages.pick_available(["qemu-user-binfmt", "qemu-user-static"])
            if pkg:
                action = Action("Install user-mode emulation", [["apt-get", "install", "-y", pkg]], packages=[pkg])
        return Finding(
            "critical",
            "Architecture",
            path,
            "Built for %s, this system is %s" % (info.arch_label or arch, self.host_arch),
            "ELF machine type %d (%s) cannot run natively here." % (info.machine, arch),
            "Download the %s build of this program; otherwise run it through QEMU user-mode emulation (slow)." % self.host_arch,
            action,
        )

    def _interp_finding(self, path: str, info: elfinfo.ElfInfo) -> Finding:
        interp = info.interp or ""
        if "musl" in interp:
            return Finding(
                "critical",
                "Missing loader",
                path,
                "Built against musl libc (Alpine-style)",
                "Program loader %s is missing." % interp,
                "Install the musl runtime, or use a glibc build of the program.",
                Action("Install musl", [["apt-get", "install", "-y", "musl"]], packages=["musl"]),
            )
        if interp.startswith("/nix/"):
            return Finding(
                "critical",
                "Missing loader",
                path,
                "Built for NixOS",
                "Loader %s lives in the Nix store." % interp,
                "Install it through Nix, or patch it with 'patchelf --set-interpreter /lib64/ld-linux-x86-64.so.2'.",
                Action("Install patchelf", [["apt-get", "install", "-y", "patchelf"]], packages=["patchelf"]),
            )
        return Finding(
            "critical",
            "Missing loader",
            path,
            "Program loader not found",
            "The binary asks for %s, which does not exist." % interp,
            "Get a build for this distribution, or fix the loader path with patchelf.",
            Action("Install patchelf", [["apt-get", "install", "-y", "patchelf"]], packages=["patchelf"]),
        )

    def _missing_libs_finding(self, path: str, info: elfinfo.ElfInfo, missing: List[str]) -> Finding:
        pkgs: List[str] = []
        notes: List[str] = []
        arch_suffix = "" if info.arch == self.host_arch else ":" + info.arch
        for lib in missing:
            found = packages.packages_for_soname(lib, self.opt.use_apt_file)
            if found:
                pkgs.append(found[0] + arch_suffix)
                notes.append("%s -> %s" % (lib, found[0]))
            else:
                others = [s for s in self.index.soname_versions(lib) if s != lib]
                if others:
                    notes.append("%s has no package; this system has %s instead (ABI change - the program needs a rebuild or a compat build)" % (lib, ", ".join(others[:3])))
                else:
                    notes.append("%s: no Debian package found%s" % (lib, "" if self.opt.use_apt_file else " (enable apt-file lookup in Options for a deeper search)"))
        action = None
        if pkgs:
            action = Action("Install %s" % ", ".join(pkgs), [["apt-get", "install", "-y", *pkgs]], packages=pkgs)
        return Finding(
            "critical",
            "Missing library",
            path,
            "Missing %d shared librar%s: %s" % (len(missing), "y" if len(missing) == 1 else "ies", ", ".join(missing[:4]) + ("…" if len(missing) > 4 else "")),
            "\n".join(notes),
            "Install the packages providing the libraries." if pkgs else "Get a build of this program made for your release, or a Flatpak/AppImage.",
            action,
        )

    def check_appimage(self, path: str, info: elfinfo.ElfInfo, st: os.stat_result) -> List[Finding]:
        out: List[Finding] = []
        if not (st.st_mode & 0o111):
            out.append(
                Finding(
                    "warning",
                    "AppImage",
                    path,
                    "AppImage is not executable",
                    "The file lacks the executable bit.",
                    "Mark it executable.",
                    Action("Make executable", [["chmod", "+x", "--", path]], needs_root=not os.access(path, os.W_OK)),
                )
            )
        if info.arch != self.host_arch:
            out.append(Finding("critical", "Architecture", path, "AppImage built for %s" % info.arch_label, "", "Download the %s AppImage." % self.host_arch))
        if not self.index.find("libfuse.so.2", info.arch):
            pkg, _ = packages.pick_available(["libfuse2t64", "libfuse2"])
            out.append(
                Finding(
                    "warning",
                    "AppImage",
                    path,
                    "AppImage runtime needs FUSE 2",
                    "libfuse.so.2 is not installed. Most AppImages (type 2 runtime) fail with 'dlopen(): error loading libfuse.so.2'.",
                    "Install libfuse2, or run with '--appimage-extract-and-run'.",
                    Action("Install %s" % pkg, [["apt-get", "install", "-y", pkg]], packages=[pkg]) if pkg else None,
                )
            )
        return out

    def check_kmod(self, path: str, info: elfinfo.ElfInfo) -> List[Finding]:
        vermagic = info.modinfo.get("vermagic", "")
        built_for = vermagic.split(" ")[0] if vermagic else ""
        running = kernel_release()
        if built_for and built_for != running:
            return [
                Finding(
                    "warning",
                    "Kernel module",
                    path,
                    "Module built for kernel %s (running %s)" % (built_for, running),
                    "vermagic: %s" % vermagic,
                    "Rebuild the module for the running kernel - ideally package it with DKMS so it rebuilds on kernel updates.",
                    Action(
                        "Install DKMS and kernel headers",
                        [["apt-get", "install", "-y", "dkms", "linux-headers-" + running]],
                        packages=["dkms", "linux-headers-" + running],
                    ),
                )
            ]
        return []

    # -- scripts ----------------------------------------------------------------------------
    def check_shebang(self, path: str, head: bytes) -> List[Finding]:
        line = head.split(b"\n", 1)[0][2:].decode("utf-8", "replace").strip()
        if line.endswith("\r"):
            line = line[:-1]
        parts = line.split()
        if not parts:
            return []
        out: List[Finding] = []
        if head.split(b"\n", 1)[0].endswith(b"\r"):
            out.append(
                Finding(
                    "critical",
                    "Script",
                    path,
                    "Windows (CRLF) line endings in script",
                    "The kernel looks for an interpreter called '%s\\r', which fails with 'bad interpreter'." % parts[0],
                    "Convert line endings to Unix format.",
                    Action("Convert to LF", [["sed", "-i", "s/\\r$//", "--", path]], needs_root=not os.access(path, os.W_OK)),
                )
            )
        interp = parts[0]
        name = os.path.basename(interp)
        args = parts[1:]
        if name == "env":
            args = [a for a in args if not a.startswith("-") and "=" not in a]
            if not args:
                return out
            name = args[0]
            if which(name):
                return out
        elif os.path.exists(interp):
            return out
        elif which(name):
            fixed = "#!/usr/bin/env %s" % name if name not in ("sh", "bash") else "#!" + (which(name) or "/bin/" + name)
            out.append(
                Finding(
                    "warning",
                    "Script",
                    path,
                    "Interpreter path '%s' does not exist" % interp,
                    "'%s' is installed at %s - the script was probably written for another OS (macOS/BSD/Homebrew)." % (name, which(name)),
                    "Change the first line to '%s'." % fixed,
                    Action(
                        "Rewrite shebang",
                        [["sed", "-i", "1s|^#!.*|%s|" % fixed, "--", path]],
                        needs_root=not os.access(path, os.W_OK),
                    ),
                )
            )
            return out
        if name in INTERPRETERS:
            pkg, hint = INTERPRETERS[name]
        else:
            # Unknown interpreter: a package with the same name is the usual provider.
            pkg = packages.pick_available([name])[0]
            hint = ("Install the '%s' package." % pkg) if pkg else "Install the interpreter '%s' or port the script." % name
        action = None
        if pkg:
            real, _ = packages.pick_available([pkg])
            if real:
                action = Action("Install %s" % real, [["apt-get", "install", "-y", real]], packages=[real])
        out.append(
            Finding(
                "critical" if pkg is None else "warning",
                "Script",
                path,
                "Script interpreter '%s' not installed" % name,
                "Shebang: #!%s" % line,
                hint,
                action,
            )
        )
        return out

    def check_python(self, path: str) -> List[Finding]:
        try:
            with open(path, "rb") as fh:
                src = fh.read()
        except OSError:
            return []
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compile(src, path, "exec", dont_inherit=True)
            return []
        except SyntaxError as exc:
            text = src.decode("utf-8", "replace")
            py2 = (
                "Missing parentheses in call to 'print'" in str(exc)
                or re.search(r"^\s*print\s+[\"'\w]", text, re.M) is not None
                or re.search(r"except\s+\w+(\.\w+)*\s*,\s*\w+\s*:", text) is not None
            )
            import sys

            if py2:
                return [
                    Finding(
                        "warning",
                        "Python 2",
                        path,
                        "Python 2 source code",
                        "Line %s: %s" % (exc.lineno, exc.msg),
                        "Python 2 is not available on current Debian releases. Port it to Python 3 (e.g. with 'pyupgrade' or the 'future' package's futurize).",
                    )
                ]
            return [
                Finding(
                    "info",
                    "Python",
                    path,
                    "Does not compile with Python %d.%d" % sys.version_info[:2],
                    "Line %s: %s" % (exc.lineno, exc.msg),
                    "The code may need a newer (or older) Python than this system's, or it is simply broken.",
                )
            ]
        except (ValueError, MemoryError, RecursionError):
            return []

    def check_desktop(self, path: str) -> List[Finding]:
        out = []
        in_entry = False
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        for line in lines:
            s = line.strip()
            if s.startswith("["):
                in_entry = s == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in s:
                continue
            key, _, val = s.partition("=")
            if key.strip() not in ("Exec", "TryExec"):
                continue
            prog = _first_program(val)
            if prog and not _program_exists(prog):
                out.append(
                    Finding(
                        "warning",
                        "Launcher",
                        path,
                        "Launcher points to missing program '%s'" % prog,
                        "%s=%s" % (key.strip(), val.strip()),
                        "Reinstall the application, or edit/remove this .desktop file.",
                    )
                )
                break
        return out

    def check_unit(self, path: str) -> List[Finding]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return []
        for line in text.splitlines():
            s = line.strip()
            if not s.startswith(("ExecStart=", "ExecStartPre=")):
                continue
            val = s.split("=", 1)[1].lstrip("-@:+!")
            prog = val.split()[0] if val.split() else ""
            if prog.startswith("/") and not os.path.exists(prog):
                return [
                    Finding(
                        "warning",
                        "Service",
                        path,
                        "systemd unit runs missing program '%s'" % prog,
                        s,
                        "Reinstall the package providing the service, or disable the unit.",
                    )
                ]
        return []

    # -- packages / foreign formats --------------------------------------------------------
    def check_deb(self, path: str) -> List[Finding]:
        rc, out, err = run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture", "Depends", "Pre-Depends"], timeout=30)
        if rc != 0:
            return [Finding("warning", "Package", path, "Unreadable .deb package", err.strip(), "Re-download the package.")]
        fields = _parse_control(out)
        name = fields.get("Package", os.path.basename(path))
        arch = fields.get("Architecture", "all")
        version = fields.get("Version", "")
        findings: List[Finding] = []
        if arch not in ("all", self.host_arch) and arch not in foreign_archs():
            findings.append(
                Finding(
                    "critical",
                    "Package",
                    path,
                    "%s is built for %s (system: %s)" % (name, arch, self.host_arch),
                    "",
                    "Download the %s build of %s." % (self.host_arch, name),
                )
            )
            return findings
        if re.search(r"ubuntu|~(bionic|focal|jammy|noble|oracular|plucky)", version):
            findings.append(
                Finding(
                    "warning",
                    "Package",
                    path,
                    "%s %s was built for Ubuntu" % (name, version),
                    "Ubuntu packages may depend on library versions Debian does not have ('FrankenDebian').",
                    "Prefer a Debian build, a Flatpak, or the upstream generic Linux build.",
                )
            )
        unmet = _unmet_depends(", ".join(x for x in (fields.get("Pre-Depends", ""), fields.get("Depends", "")) if x))
        if unmet:
            findings.append(
                Finding(
                    "critical",
                    "Package",
                    path,
                    "%s has %d unsatisfiable dependenc%s" % (name, len(unmet), "y" if len(unmet) == 1 else "ies"),
                    "\n".join(unmet),
                    "Get a version of the package built for your release, or install the missing dependencies from backports.",
                )
            )
        elif not findings:
            findings.append(
                Finding(
                    "info",
                    "Package",
                    path,
                    "%s %s is installable" % (name, version),
                    "All dependencies can be satisfied from your apt sources.",
                    "",
                    Action("Install %s" % name, [["apt-get", "install", "-y", os.path.abspath(path)]], packages=[]),
                )
            )
        return findings

    def _package_format(self, path: str, ext: str) -> List[Finding]:
        label, repl, pkg = PACKAGE_FORMATS[ext]
        action = None
        if pkg and not packages.is_installed(pkg):
            real, _ = packages.pick_available([pkg])
            if real:
                action = Action("Install %s" % real, [["apt-get", "install", "-y", real]], packages=[real])
        severity = "info" if ext in (".reg",) else "warning"
        return [Finding(severity, "Foreign format", path, label, "Not a native Debian format.", repl, action)]

    def check_windows(self, path: str, head: bytes) -> List[Finding]:
        base = os.path.basename(path).lower()
        alt = next(((label, pkg) for key, label, pkg in WINDOWS_ALTERNATIVES if key in base), None)
        action = None
        if alt and alt[1]:
            real, installed = packages.pick_available([alt[1]])
            if real and not installed:
                action = Action("Install %s" % real, [["apt-get", "install", "-y", real]], packages=[real])
        elif not which("wine"):
            real, _ = packages.pick_available(["wine"])
            if real:
                action = Action("Install Wine", [["apt-get", "install", "-y", real]], packages=[real])
        replacement = ("Native alternative: %s." % alt[0]) if alt else "No known native alternative; run it with Wine."
        if alt:
            replacement += " Wine is a fallback if you need this exact program."
        return [Finding("warning", "Windows program", path, "Windows executable", "PE/MZ binaries need Wine to run on Linux.", replacement, action)]

    def check_snap(self, path: str) -> List[Finding]:
        if which("snap"):
            return []
        return [
            Finding(
                "warning",
                "Foreign format",
                path,
                "Snap package but snapd is not installed",
                "",
                "Install snapd, or prefer a .deb/Flatpak build (LMDE blocks snapd by default).",
                Action("Install snapd", [["apt-get", "install", "-y", "snapd"]], packages=["snapd"]),
            )
        ]

    def check_flatpak(self, path: str) -> List[Finding]:
        if which("flatpak"):
            return []
        return [
            Finding(
                "warning",
                "Foreign format",
                path,
                "Flatpak bundle but Flatpak is not installed",
                "",
                "Install flatpak, then open the file again.",
                Action("Install Flatpak", [["apt-get", "install", "-y", "flatpak"]], packages=["flatpak"]),
            )
        ]

    def check_jar(self, path: str) -> List[Finding]:
        if which("java") or "jar:java" in self._reported_global:
            return []
        self._reported_global.add("jar:java")
        return [
            Finding(
                "warning",
                "Java",
                path,
                "Java archive but no Java runtime installed",
                "",
                "Install the default Java runtime.",
                Action("Install default-jre", [["apt-get", "install", "-y", "default-jre"]], packages=["default-jre"]),
            )
        ]


# --- helpers ------------------------------------------------------------------------------


def _writable_dir(path: str) -> bool:
    return os.access(os.path.dirname(path) or ".", os.W_OK)


def _version_key(v: str):
    nums = re.findall(r"\d+", v)
    return (re.sub(r"[\d.]+$", "", v), [int(n) for n in nums])


def _newest(versions, n: int) -> List[str]:
    vs = sorted(set(versions), key=_version_key, reverse=True)
    # prefer the main ABI tag family (GLIBC_, GLIBCXX_...) over private tags
    vs = [v for v in vs if not v.startswith("GLIBC_PRIVATE")] or vs
    return vs[:n] if vs else ["?"]


def _first_program(exec_line: str) -> str:
    tokens = exec_line.strip().split()
    while tokens and (tokens[0] == "env" or ("=" in tokens[0] and not tokens[0].startswith("/"))):
        tokens.pop(0)
    while tokens and tokens[0].startswith("-"):
        tokens.pop(0)
    return tokens[0].strip("\"'") if tokens else ""


def _program_exists(prog: str) -> bool:
    if "%" in prog:
        return True
    if prog.startswith("/"):
        return os.path.exists(prog)
    return which(prog) is not None


def _parse_control(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    last = None
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and last:
            fields[last] += " " + line.strip()
        elif ":" in line:
            k, _, v = line.partition(":")
            last = k.strip()
            fields[last] = v.strip()
    return fields


_DEP_RE = re.compile(r"^\s*([a-z0-9][a-z0-9+.\-]*)(?::[a-z0-9-]+)?\s*(?:\(\s*(<<|<=|=|>=|>>)\s*([^)\s]+)\s*\))?")
_OPS = {"<<": "lt", "<=": "le", "=": "eq", ">=": "ge", ">>": "gt"}


def _unmet_depends(depends: str) -> List[str]:
    if not depends.strip():
        return []
    groups = []
    names = set()
    for group in depends.split(","):
        alts = []
        for alt in group.split("|"):
            alt = re.sub(r"\[[^\]]*\]|<[^>]*>", "", alt)
            m = _DEP_RE.match(alt)
            if m:
                alts.append((m.group(1), m.group(2), m.group(3)))
                names.add(m.group(1))
        if alts:
            groups.append((group.strip(), alts))
    inst = packages.installed_versions(names)
    cand = packages.candidates(names)
    unmet = []
    for text, alts in groups:
        ok = False
        for name, op, ver in alts:
            have = inst.get(name) or cand.get(name)
            if not have:
                if _is_virtual_provided(name):
                    ok = True
                    break
                continue
            if not op:
                ok = True
                break
            rc, _, _ = run(["dpkg", "--compare-versions", have, _OPS[op], ver], timeout=5)
            if rc == 0:
                ok = True
                break
        if not ok:
            unmet.append(text)
    return unmet


def _is_virtual_provided(name: str) -> bool:
    rc, out, _ = run(["apt-cache", "showpkg", name], timeout=20)
    if rc != 0 or "Reverse Provides:" not in out:
        return False
    tail = out.split("Reverse Provides:", 1)[1].strip()
    return bool(tail)


def fix_all_action(findings: List[Finding]) -> Optional[Action]:
    """Merge the apt installs of all fixable findings into one action."""
    pkgs: List[str] = []
    other: List[List[str]] = []
    for f in findings:
        # Only privileged system fixes are merged; per-file edits stay individual so
        # they are not run as root (which would change file ownership).
        if not f.action or not f.action.needs_root or f.severity == "info":
            continue
        for cmd in f.action.commands:
            if cmd[:3] == ["apt-get", "install", "-y"] and f.action.packages:
                for p in cmd[3:]:
                    if p not in pkgs:
                        pkgs.append(p)
            elif cmd not in other and cmd[:2] != ["rm", "--"]:
                other.append(cmd)
    if not pkgs and not other:
        return None
    pre = [c for c in other if c[:2] == ["dpkg", "--add-architecture"]]
    rest = [c for c in other if c not in pre and c != ["apt-get", "update"]]
    cmds = pre + [["apt-get", "update"]] + ([["apt-get", "install", "-y", *pkgs]] if pkgs else []) + rest
    return Action("Apply all fixes", cmds, packages=pkgs)


def describe_action(action: Action) -> str:
    return "\n".join(shell_join(c) for c in action.commands)
