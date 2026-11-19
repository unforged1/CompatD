"""Export scan findings as Markdown or JSON."""
from __future__ import annotations

import json
from typing import List

from . import __version__
from .models import SEVERITY_RANK, Finding
from .scanner import describe_action


def write_report(path: str, findings: List[Finding], scope: str, distro_name: str) -> None:
    ordered = sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), f.category))
    if path.endswith(".json"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"tool": "CompatD", "version": __version__, "scope": scope, "system": distro_name, "findings": [f.to_dict() for f in ordered]}, fh, indent=2)
        return
    lines = ["# CompatD report", "", "- Version: %s" % __version__, "- System: %s" % distro_name, "- Scope: %s" % scope, ""]
    for sev in ("critical", "warning", "info"):
        group = [f for f in ordered if f.severity == sev]
        if not group:
            continue
        lines += ["## %s (%d)" % (sev.capitalize(), len(group)), ""]
        for f in group:
            lines.append("### %s" % f.title)
            lines.append("- **Category:** %s" % f.category)
            lines.append("- **Target:** `%s`" % f.target)
            if f.detail:
                lines.append("- **Details:** %s" % f.detail.replace("\n", "; "))
            if f.replacement:
                lines.append("- **Replacement:** %s" % f.replacement)
            if f.action:
                lines += ["", "```sh", describe_action(f.action), "```"]
            lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
