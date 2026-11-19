"""Small helpers for running system commands safely (no shell, with timeouts)."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

# Some tools live in sbin, which is not on a regular user's PATH on Debian.
_EXTRA_PATHS = ("/usr/sbin", "/sbin", "/usr/local/sbin")


def which(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_PATHS:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def run(cmd: Sequence[str], timeout: float = 30, env: Optional[dict] = None) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr).

    Returns 127 if the program does not exist and 124 on timeout, mirroring the shell.
    """
    exe = which(cmd[0]) if not os.path.isabs(cmd[0]) else cmd[0]
    if not exe:
        return 127, "", "%s: not found" % cmd[0]
    full_env = dict(os.environ)
    full_env.setdefault("LC_ALL", "C")
    full_env["LANG"] = "C"
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            [exe, *cmd[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=full_env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 126, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def read_first_line(path: str, default: str = "") -> str:
    return read_text(path, default).strip().splitlines()[0] if read_text(path, "").strip() else default


@lru_cache(maxsize=1)
def dpkg_arch() -> str:
    rc, out, _ = run(["dpkg", "--print-architecture"], timeout=5)
    if rc == 0 and out.strip():
        return out.strip()
    m = platform.machine()
    return {"x86_64": "amd64", "aarch64": "arm64", "i686": "i386", "armv7l": "armhf"}.get(m, m)


@lru_cache(maxsize=1)
def foreign_archs() -> Tuple[str, ...]:
    rc, out, _ = run(["dpkg", "--print-foreign-architectures"], timeout=5)
    return tuple(out.split()) if rc == 0 else ()


def kernel_release() -> str:
    return platform.release()


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def human_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return str(n)


def shell_join(cmd: List[str]) -> str:
    import shlex

    return " ".join(shlex.quote(c) for c in cmd)
