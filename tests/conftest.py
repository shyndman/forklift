from __future__ import annotations

import sys
from pathlib import Path

# Make the in-container harness Python package importable for host-side unit tests.
_HARNESS_PY = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/py"
if str(_HARNESS_PY) not in sys.path:
    sys.path.insert(0, str(_HARNESS_PY))
