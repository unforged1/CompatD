# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 unforged1
"""In-memory timings and errors for test reports.

Nothing here is written to disk or sent anywhere. The data only leaves the machine when the
user opens a test report, reviews it and submits it themselves (see testreport.py).
"""
from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from typing import Dict, Iterator, List, Tuple

_lock = threading.Lock()
_timings: Dict[str, List[Tuple[float, str]]] = {}
_errors: List[str] = []
_started = time.monotonic()
MAX_ERRORS = 10


def record(name: str, seconds: float, detail: str = "") -> None:
    with _lock:
        _timings.setdefault(name, []).append((seconds, detail))


@contextmanager
def timed(name: str, detail: str = "") -> Iterator[None]:
    t0 = time.monotonic()
    try:
        yield
    finally:
        record(name, time.monotonic() - t0, detail)


def since_start() -> float:
    return time.monotonic() - _started


def error(where: str, exc: BaseException) -> None:
    """Remember an error: its type, message and the innermost CompatD frame (no local variables)."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if "compatd" in f.filename.replace("\\", "/")]
    loc = ""
    if frames:
        f = frames[-1]
        path = f.filename.replace("\\", "/")
        loc = " at %s:%d in %s()" % (path[path.rfind("compatd/"):], f.lineno, f.name)
    line = "%s: %s: %s%s" % (where, type(exc).__name__, str(exc)[:300], loc)
    with _lock:
        if len(_errors) < MAX_ERRORS:
            _errors.append(line)


def snapshot() -> Tuple[Dict[str, List[Tuple[float, str]]], List[str]]:
    with _lock:
        return {k: list(v) for k, v in _timings.items()}, list(_errors)


def reset() -> None:
    """For tests."""
    with _lock:
        _timings.clear()
        _errors.clear()
