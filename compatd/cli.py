"""Command-line entry point. Without a sub-command the GUI is started."""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __app_name__, __version__


def _print_findings(findings, as_json: bool) -> None:
    if as_json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        return
    from .models import SEVERITY_RANK
    from .scanner import describe_action

    for f in sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), f.category, f.target)):
        print("[%s] %s: %s" % (f.severity.upper(), f.category, f.title))
        print("    target: %s" % f.target)
        if f.detail:
            for line in f.detail.splitlines():
                print("    %s" % line)
        if f.replacement:
            print("    -> %s" % f.replacement)
        if f.action:
            print("    fix: %s" % describe_action(f.action).replace("\n", "\n         "))
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ("critical", "warning", "info")}
    print("\n%d critical, %d warning, %d info" % (counts["critical"], counts["warning"], counts["info"]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="compatd", description="%s %s - driver & compatibility manager" % (__app_name__, __version__))
    parser.add_argument("--version", action="version", version="%s %s" % (__app_name__, __version__))
    parser.add_argument("--theme", choices=["auto", "kali", "debian", "mx", "lmde", "other"], help="override the GUI theme")
    parser.add_argument("--fullscreen", action="store_true", help="start the GUI in fullscreen")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="start the graphical interface (default)")
    p = sub.add_parser("info", help="show system information")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("drivers", help="list devices and driver recommendations")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("system", help="check this device for compatibility faults")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("scan", help="scan a directory for compatibility faults")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.add_argument("--hidden", action="store_true", help="include hidden files")
    p.add_argument("--apt-file", action="store_true", help="use apt-file to look up missing libraries")
    args = parser.parse_args(argv)

    if args.cmd in (None, "gui"):
        try:
            from .gui.app import run_gui
        except (ImportError, ValueError) as exc:
            print("GTK 3 (python3-gi, gir1.2-gtk-3.0) is required for the GUI: %s" % exc, file=sys.stderr)
            print("Use 'compatd --help' for the command-line interface.", file=sys.stderr)
            return 1
        return run_gui(theme=args.theme, fullscreen=args.fullscreen)

    from . import distro as distro_mod

    dist = distro_mod.detect()

    if args.cmd == "info":
        from . import hardware

        data = {"distro": dist.pretty_name, "family": dist.family, "theme": dist.label, **hardware.summary()}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for k, v in data.items():
                print("%-12s %s" % (k + ":", v))
        return 0

    if args.cmd == "drivers":
        from . import drivers, hardware

        devices = hardware.all_devices()
        recs = drivers.recommend(devices, dist)
        if args.json:
            print(json.dumps({"devices": [d.to_dict() for d in devices], "recommendations": [r.to_dict() for r in recs]}, indent=2))
            return 0
        print("Devices:")
        for d in devices:
            if hardware.is_important(d) or d.driver:
                print("  %-4s %-14s %-28s %-40s driver: %s" % (d.bus, d.slot, d.class_name[:28], d.label[:40], d.driver or "NONE"))
        print("\nRecommendations:")
        for r in recs:
            print("  [%s]%s %s" % (r.status, " (optional)" if r.optional else "", r.title))
            print("      packages: %s" % (", ".join(r.packages) or "-"))
            if r.missing:
                print("      to install: %s" % ", ".join(r.missing))
            for n in r.notes:
                print("      note: %s" % n)
        cmds = drivers.install_commands([r for r in recs if r.recommended])
        if cmds:
            from .sysutil import shell_join

            print("\nTo install the recommended drivers run (as root):")
            for c in cmds:
                print("  " + shell_join(c))
        return 0

    if args.cmd == "system":
        from . import hardware, syscheck

        findings = syscheck.run_all(dist, hardware.all_devices())
        _print_findings(findings, args.json)
        return 0

    if args.cmd == "scan":
        from .scanner import ScanOptions, Scanner

        opts = ScanOptions(skip_hidden=not args.hidden, use_apt_file=args.apt_file)
        sc = Scanner(opts)
        findings = sc.scan(args.path)
        _print_findings(findings, args.json)
        if not args.json:
            print("%d files scanned" % sc.files_scanned)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
