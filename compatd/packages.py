"""Queries against dpkg / apt. All read-only; nothing here needs root."""
from __future__ import annotations

import glob
import re
import threading
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .sysutil import run, which

_lock = threading.Lock()
_installed_cache: Dict[str, Optional[str]] = {}
_candidate_cache: Dict[str, Optional[str]] = {}


def clear_cache() -> None:
    with _lock:
        _installed_cache.clear()
        _candidate_cache.clear()


def installed_versions(pkgs: Iterable[str]) -> Dict[str, Optional[str]]:
    """Return {pkg: version or None} for each package (None = not installed)."""
    pkgs = [p for p in dict.fromkeys(pkgs) if p]
    with _lock:
        todo = [p for p in pkgs if p not in _installed_cache]
    if todo:
        rc, out, _ = run(["dpkg-query", "-W", "-f=${Package}\t${db:Status-Abbrev}\t${Version}\n", *todo], timeout=30)
        found: Dict[str, Optional[str]] = {p: None for p in todo}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            name = parts[0].split(":")[0]
            if parts[1].startswith("ii") and name in found:
                found[name] = parts[2]
        with _lock:
            _installed_cache.update(found)
    with _lock:
        return {p: _installed_cache.get(p) for p in pkgs}


def is_installed(pkg: str) -> bool:
    return installed_versions([pkg]).get(pkg) is not None


def candidates(pkgs: Iterable[str]) -> Dict[str, Optional[str]]:
    """Return {pkg: candidate version or None} using apt-cache policy."""
    pkgs = [p for p in dict.fromkeys(pkgs) if p]
    with _lock:
        todo = [p for p in pkgs if p not in _candidate_cache]
    if todo:
        rc, out, _ = run(["apt-cache", "policy", *todo], timeout=60)
        found: Dict[str, Optional[str]] = {p: None for p in todo}
        current = None
        for line in out.splitlines():
            if line and not line.startswith(" ") and line.endswith(":"):
                current = line[:-1].split(":")[0]
            elif current and line.strip().startswith("Candidate:"):
                val = line.split(":", 1)[1].strip()
                if current in found:
                    found[current] = None if val == "(none)" else val
        with _lock:
            _candidate_cache.update(found)
    with _lock:
        return {p: _candidate_cache.get(p) for p in pkgs}


def pick_available(alternatives: List[str]) -> Tuple[Optional[str], bool]:
    """Pick the first alternative that is installed, else the first with an apt candidate.

    Returns (package or None, installed?).
    """
    inst = installed_versions(alternatives)
    for alt in alternatives:
        if inst.get(alt):
            return alt, True
    cand = candidates(alternatives)
    for alt in alternatives:
        if cand.get(alt):
            return alt, False
    return None, False


def apt_sources() -> List[Dict[str, object]]:
    """Parse one-line (.list) and deb822 (.sources) apt sources."""
    entries: List[Dict[str, object]] = []
    files = ["/etc/apt/sources.list"] + sorted(glob.glob("/etc/apt/sources.list.d/*.list"))
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    if not line.startswith(("deb ", "deb-src ")):
                        continue
                    line = re.sub(r"\[[^\]]*\]", "", line)
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    entries.append(
                        {
                            "file": path,
                            "type": parts[0],
                            "uri": parts[1],
                            "suite": parts[2],
                            "components": parts[3:],
                        }
                    )
        except OSError:
            continue
    for path in sorted(glob.glob("/etc/apt/sources.list.d/*.sources")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for stanza in re.split(r"\n\s*\n", text):
            fields: Dict[str, str] = {}
            last = None
            for line in stanza.splitlines():
                if line.startswith("#"):
                    continue
                if line[:1] in (" ", "\t") and last:
                    fields[last] += " " + line.strip()
                    continue
                if ":" in line:
                    k, _, v = line.partition(":")
                    last = k.strip().lower()
                    fields[last] = v.strip()
            if not fields.get("uris") or fields.get("enabled", "yes").lower() == "no":
                continue
            for typ in fields.get("types", "deb").split():
                for uri in fields["uris"].split():
                    for suite in fields.get("suites", "").split():
                        entries.append(
                            {
                                "file": path,
                                "type": typ,
                                "uri": uri,
                                "suite": suite,
                                "components": fields.get("components", "").split(),
                            }
                        )
    return entries


def enabled_components() -> Set[str]:
    comps: Set[str] = set()
    for e in apt_sources():
        if e["type"] == "deb":
            comps.update(e["components"])  # type: ignore[arg-type]
    return comps


def dpkg_search(path: str) -> Optional[str]:
    """Which installed package owns a file path."""
    rc, out, _ = run(["dpkg", "-S", path], timeout=15)
    if rc == 0 and out:
        return out.split(":", 1)[0].split(",")[0].strip()
    return None


def apt_file_search(filename: str) -> List[str]:
    """Search for packages providing a file name (requires apt-file with an updated cache)."""
    if not which("apt-file"):
        return []
    rc, out, _ = run(["apt-file", "search", "-x", "/" + re.escape(filename) + "$"], timeout=60)
    if rc != 0:
        return []
    pkgs = []
    for line in out.splitlines():
        pkg = line.split(":", 1)[0].strip()
        if pkg and pkg not in pkgs:
            pkgs.append(pkg)
    return pkgs


_SONAME_RE = re.compile(r"^lib(?P<name>.+?)\.so(?:\.(?P<ver>[0-9][0-9.]*))?$")


def soname_package_guesses(soname: str) -> List[str]:
    """Guess Debian package names for a shared library soname using Debian policy naming.

    libssl.so.3      -> libssl3, libssl3t64
    libfoo-1.2.so.0  -> libfoo-1.2-0
    """
    m = _SONAME_RE.match(soname)
    if not m:
        return []
    name, ver = m.group("name"), (m.group("ver") or "")
    major = ver.split(".")[0] if ver else ""
    guesses: List[str] = []
    if major:
        sep = "-" if name[-1:].isdigit() else ""
        base = "lib%s%s%s" % (name, sep, major)
        if len(ver.split(".")) > 1 and ver.count(".") == 1:
            # e.g. libssl.so.1.1 -> libssl1.1
            guesses.append("lib%s%s%s" % (name, sep, ver))
        guesses += [base, base + "t64"]
    else:
        guesses.append("lib%s-dev" % name)
    return [g.lower().replace("_", "-") for g in guesses]


# Well-known sonames whose package names do not follow the default pattern.
KNOWN_SONAMES: Dict[str, List[str]] = {
    "libfuse.so.2": ["libfuse2t64", "libfuse2"],
    "libfuse3.so.3": ["libfuse3-4", "libfuse3-3"],
    "libGL.so.1": ["libgl1"],
    "libEGL.so.1": ["libegl1"],
    "libGLX.so.0": ["libglx0"],
    "libvulkan.so.1": ["libvulkan1"],
    "libstdc++.so.6": ["libstdc++6"],
    "libgcc_s.so.1": ["libgcc-s1"],
    "libpython2.7.so.1.0": ["libpython2.7"],
    "libncurses.so.5": ["libncurses5"],
    "libtinfo.so.5": ["libtinfo5"],
    "libncursesw.so.6": ["libncursesw6"],
    "libgtk-3.so.0": ["libgtk-3-0t64", "libgtk-3-0"],
    "libgtk-x11-2.0.so.0": ["libgtk2.0-0t64", "libgtk2.0-0"],
    "libgdk-x11-2.0.so.0": ["libgtk2.0-0t64", "libgtk2.0-0"],
    "libnss3.so": ["libnss3"],
    "libasound.so.2": ["libasound2t64", "libasound2"],
    "libatk-1.0.so.0": ["libatk1.0-0t64", "libatk1.0-0"],
    "libQt5Core.so.5": ["libqt5core5t64", "libqt5core5a"],
    "libQt5Gui.so.5": ["libqt5gui5t64", "libqt5gui5"],
    "libQt5Widgets.so.5": ["libqt5widgets5t64", "libqt5widgets5"],
    "libQt6Core.so.6": ["libqt6core6t64", "libqt6core6"],
    "libX11.so.6": ["libx11-6"],
    "libXext.so.6": ["libxext6"],
    "libXrender.so.1": ["libxrender1"],
    "libxcb.so.1": ["libxcb1"],
    "libz.so.1": ["zlib1g"],
    "libpng12.so.0": ["libpng12-0"],
    "libpng16.so.16": ["libpng16-16t64", "libpng16-16"],
    "libcrypto.so.1.1": ["libssl1.1"],
    "libssl.so.1.1": ["libssl1.1"],
    "libcrypto.so.3": ["libssl3t64", "libssl3"],
    "libssl.so.3": ["libssl3t64", "libssl3"],
    "libcurl.so.4": ["libcurl4t64", "libcurl4"],
    "libcurl-gnutls.so.4": ["libcurl3t64-gnutls", "libcurl3-gnutls"],
    "libjpeg.so.8": ["libjpeg8"],
    "libjpeg.so.62": ["libjpeg62-turbo"],
    "libsqlite3.so.0": ["libsqlite3-0"],
    "libpcap.so.0.8": ["libpcap0.8t64", "libpcap0.8"],
    "libusb-1.0.so.0": ["libusb-1.0-0"],
    "libgmp.so.10": ["libgmp10"],
    "libffi.so.6": ["libffi6"],
    "libffi.so.7": ["libffi7"],
    "libffi.so.8": ["libffi8"],
    "libicuuc.so.72": ["libicu72"],
    "libicui18n.so.72": ["libicu72"],
    "libwebkit2gtk-4.0.so.37": ["libwebkit2gtk-4.0-37"],
    "libwebkit2gtk-4.1.so.0": ["libwebkit2gtk-4.1-0"],
    "libappindicator3.so.1": ["libayatana-appindicator3-1"],
    "libgconf-2.so.4": ["libgconf-2-4"],
    "libnsl.so.1": ["libnsl2"],
    "libcrypt.so.1": ["libcrypt1"],
}


def packages_for_soname(soname: str, use_apt_file: bool = True) -> List[str]:
    """Return candidate packages (that apt actually knows) providing ``soname``."""
    guesses = list(KNOWN_SONAMES.get(soname, [])) + soname_package_guesses(soname)
    guesses = list(dict.fromkeys(guesses))
    cand = candidates(guesses) if guesses else {}
    found = [g for g in guesses if cand.get(g)]
    if not found and use_apt_file:
        found = apt_file_search(soname)[:5]
    return found


def apt_check_simulated(pkgs: List[str]) -> Tuple[bool, str]:
    rc, out, err = run(["apt-get", "-s", "-q", "install", *pkgs], timeout=120)
    return rc == 0, (out + err)
