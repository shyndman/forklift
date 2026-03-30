from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import override

ENTRYPOINT_SCRIPT = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/opencode/entrypoint.sh"


class OpenCodeEntrypointTests(unittest.TestCase):
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path = Path(".")
    sandbox_bin: Path = Path(".")
    workspace: Path = Path(".")
    harness_state: Path = Path(".")
    opencode_state: Path = Path(".")
    opencode_log_dir: Path = Path(".")
    server_real_pid_file: Path = Path(".")

    @override
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.sandbox_bin = self.root / "bin"
        self.workspace = self.root / "workspace"
        self.harness_state = self.root / "harness-state"
        self.opencode_state = self.root / "run/opencode"
        self.opencode_log_dir = self.root / "home/forklift/.local/share/opencode/log"
        self.server_real_pid_file = self.root / "server-real.pid"

        self.sandbox_bin.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.harness_state.mkdir(parents=True, exist_ok=True)
        self.opencode_state.mkdir(parents=True, exist_ok=True)
        self.opencode_log_dir.mkdir(parents=True, exist_ok=True)

    @override
    def tearDown(self) -> None:
        self._kill_fake_server_if_running()
        if self._temp_dir is not None:
            self._temp_dir.cleanup()

    def test_harness_failure_exits_non_zero(self) -> None:
        self._write_executable(
            self.sandbox_bin / "chown",
            "#!/usr/bin/env bash\nexit 0\n",
        )
        self._write_executable(
            self.sandbox_bin / "runuser",
            """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--" ]]; then
    shift
    exec "$@"
  fi
  shift
done
printf 'missing -- separator\n' >&2
exit 2
""",
        )

        harness_script = self.root / "opt/forklift/harness/run.sh"
        self._write_executable(
            harness_script,
            f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s' "${{OPENCODE_MODEL:-}}" >{shlex.quote(str(self.harness_state / 'forwarded-model.txt'))}
exit 23
""",
        )

        start_server_script = self.root / "opt/opencode/start_server.sh"
        self._write_executable(
            start_server_script,
            f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p {shlex.quote(str(self.harness_state))} {shlex.quote(str(self.opencode_state))}
sleep 60 &
server_pid=$!
printf '%s' "$server_pid" >{shlex.quote(str(self.opencode_state / 'server.pid'))}
printf '%s' "$server_pid" >{shlex.quote(str(self.server_real_pid_file))}
touch {shlex.quote(str(self.opencode_state / 'server.ready'))}
exit 0
""",
        )

        entrypoint_copy = self.root / "entrypoint.sh"
        _ = entrypoint_copy.write_text(self._sandboxed_entrypoint(), encoding="utf-8")
        entrypoint_copy.chmod(0o700)

        env = {
            **os.environ,
            "PATH": f"{self.sandbox_bin}:{os.environ['PATH']}",
            "OPENCODE_MODEL": "test-model",
        }
        result = subprocess.run(
            [str(entrypoint_copy)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 23, msg=result.stderr)
        self.assertEqual(
            (self.harness_state / "forwarded-model.txt").read_text(encoding="utf-8"),
            "test-model",
        )

    def _sandboxed_entrypoint(self) -> str:
        return (
            ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")
            .replace("/opt/opencode/start_server.sh", str(self.root / "opt/opencode/start_server.sh"))
            .replace("/opt/forklift/harness/run.sh", str(self.root / "opt/forklift/harness/run.sh"))
            .replace("/home/forklift/.local/share/opencode/log", str(self.opencode_log_dir))
            .replace("/harness-state", str(self.harness_state))
            .replace("/run/opencode", str(self.opencode_state))
            .replace("/workspace", str(self.workspace))
        )

    def _kill_fake_server_if_running(self) -> None:
        if not self.server_real_pid_file.exists():
            return
        try:
            pid = int(self.server_real_pid_file.read_text(encoding="utf-8"))
        except ValueError:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def _write_executable(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(content, encoding="utf-8")
        path.chmod(0o700)
