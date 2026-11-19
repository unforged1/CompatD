"""Runs fix/install actions, elevating with pkexec when needed, streaming output."""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
from typing import Callable, List, Optional

from .models import Action
from .sysutil import is_root, shell_join, which

LineFn = Callable[[str], None]
DoneFn = Callable[[int], None]


def build_argv(action: Action, dry_run: bool = False) -> List[str]:
    """Return the argv that executes the whole action as a single (elevated) shell job."""
    cmds = action.commands
    if dry_run:
        sim = []
        for c in cmds:
            if c[:2] == ["apt-get", "install"]:
                sim.append(["apt-get", "-s", *c[1:]])
            elif c[:2] == ["apt-get", "update"]:
                continue
            else:
                sim.append(["echo", "[dry-run] would run:", shell_join(c)])
        cmds = sim or [["echo", "[dry-run] nothing to do"]]
    script = "set -e\nexport DEBIAN_FRONTEND=noninteractive\n" + "\n".join(
        "printf '%%s\\n' %s\n%s" % (shlex.quote(">>> " + shell_join(c)), shell_join(c)) for c in cmds
    )
    argv = ["/bin/sh", "-c", script]
    if action.needs_root and not dry_run and not is_root():
        pk = which("pkexec")
        if pk:
            argv = [pk, *argv]
        elif which("sudo"):
            argv = ["sudo", "-A", *argv] if os.environ.get("SUDO_ASKPASS") else ["sudo", "-n", *argv]
    return argv


class Runner:
    """Executes one action in a background thread."""

    def __init__(self, action: Action, on_line: LineFn, on_done: DoneFn, dry_run: bool = False) -> None:
        self.action = action
        self.on_line = on_line
        self.on_done = on_done
        self.dry_run = dry_run
        self.proc: Optional[subprocess.Popen] = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        argv = build_argv(self.action, self.dry_run)
        self.on_line("$ %s%s" % ("[dry-run] " if self.dry_run else "", self.action.label))
        for c in self.action.commands:
            self.on_line("  " + shell_join(c))
        env = dict(os.environ, LC_ALL="C", DEBIAN_FRONTEND="noninteractive")
        try:
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
        except OSError as exc:
            self.on_line("error: %s" % exc)
            self.on_done(126)
            return
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            self.on_line(raw.decode("utf-8", "replace").rstrip("\n"))
        rc = self.proc.wait()
        if rc == 126 and argv and argv[0].endswith("pkexec"):
            self.on_line("Authorization was cancelled or denied.")
        elif rc == 127 and argv and argv[0].endswith("pkexec"):
            self.on_line("Authorization failed (no polkit agent running?).")
        self.on_line("[exit code %d]" % rc)
        self.on_done(rc)

    def cancel(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
