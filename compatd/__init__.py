# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""CompatD - driver and compatibility manager for Debian-based systems."""

import os

__app_name__ = "CompatD"
__app_id__ = "io.github.compatd.CompatD"
__version__ = "0.1.0"
__copyright__ = "Copyright © 2026 unforged1"
__license__ = "GPL-3.0-or-later"

_HERE = os.path.dirname(os.path.abspath(__file__))


def data_file(name: str) -> str:
    """Path to a bundled data file: the source tree's data/ or the installed /usr/share/compatd/.

    Returns "" when the file is not found.
    """
    for base in (os.path.join(_HERE, "..", "data"), "/usr/share/compatd", "/usr/local/share/compatd"):
        path = os.path.normpath(os.path.join(base, name))
        if os.path.isfile(path):
            return path
    return ""


def license_text() -> str:
    """Full GPLv3 text: the project's LICENSE file, else Debian's common-licenses copy."""
    for path in (os.path.join(_HERE, "..", "LICENSE"), data_file("LICENSE"), "/usr/share/common-licenses/GPL-3"):
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return ""
