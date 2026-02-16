from __future__ import annotations

from .cli import Forklift

__all__ = ["Forklift", "main"]


def main() -> None:
    _ = Forklift.parse().start()
