from __future__ import annotations

import os
import sys
from pathlib import Path


def _inject_editable_venv_site_packages() -> None:
    """Add the repo-local `.venv` site-packages to `sys.path` when available."""

    project_root = Path(__file__).resolve().parents[2]
    venv_path = project_root / ".venv"
    if not venv_path.exists():
        return

    candidate_paths: list[Path] = []
    if os.name == "nt":
        candidate_paths.append(venv_path / "Lib" / "site-packages")
    else:
        lib_dir = venv_path / "lib"
        if lib_dir.exists():
            for entry in lib_dir.iterdir():
                if entry.is_dir() and entry.name.startswith("python"):
                    candidate_paths.append(entry / "site-packages")

    for site_packages in candidate_paths:
        if site_packages.exists():
            resolved = str(site_packages)
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
            break


_inject_editable_venv_site_packages()

from .cli import Forklift

__all__ = ["Forklift", "main"]


def main() -> None:
    _ = Forklift.parse().start()
