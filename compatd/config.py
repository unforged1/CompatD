"""User settings, stored as JSON in $XDG_CONFIG_HOME/compatd/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "compatd", "config.json")


@dataclass
class Config:
    theme: str = "auto"  # auto | kali | debian | mx | lmde | other
    dark_mode: str = "theme"  # theme | dark | light
    dry_run: bool = False
    show_optional: bool = True
    show_installed: bool = True
    start_fullscreen: bool = False
    follow_symlinks: bool = False
    skip_hidden: bool = True
    max_files: int = 200000
    max_file_mb: int = 256
    check_elf: bool = True
    check_scripts: bool = True
    check_python: bool = True
    check_launchers: bool = True
    check_packages: bool = True
    check_foreign: bool = True
    report_broken_links: bool = True
    use_apt_file: bool = False
    last_directory: str = ""

    @classmethod
    def load(cls) -> "Config":
        try:
            with open(config_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        known = {f.name: f for f in fields(cls)}
        cfg = cls()
        for k, v in data.items() if isinstance(data, dict) else []:
            if k in known and isinstance(v, type(getattr(cfg, k))):
                setattr(cfg, k, v)
        return cfg

    def save(self) -> None:
        path = config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)

    def scan_options(self):
        from .scanner import ScanOptions

        return ScanOptions(
            follow_symlinks=self.follow_symlinks,
            skip_hidden=self.skip_hidden,
            max_files=self.max_files,
            max_file_mb=self.max_file_mb,
            check_elf=self.check_elf,
            check_scripts=self.check_scripts,
            check_python=self.check_python,
            check_launchers=self.check_launchers,
            check_packages=self.check_packages,
            check_foreign=self.check_foreign,
            report_broken_links=self.report_broken_links,
            use_apt_file=self.use_apt_file,
        )
