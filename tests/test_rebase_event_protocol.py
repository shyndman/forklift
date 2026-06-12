"""Guards the rebase-event protocol shared across the host/container boundary.

`src/forklift/container_runner.py` (host) consumes the structured rebase events
that `docker/.../forklift_harness/rebase_state.py` (container) produces. The two
modules cannot import each other at runtime — the container image ships only the
harness package, not `forklift` — so the protocol version and event vocabulary
are hand-duplicated. These tests fail loudly when the two copies drift, instead
of letting the host silently drop every event at runtime.
"""

from __future__ import annotations

import unittest

from forklift import container_runner
from forklift_harness import rebase_state


class RebaseEventProtocolTests(unittest.TestCase):
    def test_event_version_matches_across_boundary(self) -> None:
        self.assertEqual(
            rebase_state.REBASE_EVENT_VERSION,
            container_runner.REBASE_EVENT_VERSION,
        )

    def test_emitted_event_names_are_known_to_the_host(self) -> None:
        emitted = {
            "progress",
            "conflict",
            "continue",
            "skip",
            "auto_skip",
            "complete",
            "abort",
            "reset",
        }
        self.assertEqual(emitted, set(container_runner.KNOWN_REBASE_EVENTS))


if __name__ == "__main__":
    _ = unittest.main()
