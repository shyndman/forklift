"""Host-side tests for the in-container Forklift harness package.

These replace the bash-shelled rebase-mediation cases that previously lived in
``test_harness_setup.py``. They exercise the Python port directly: command
classification, note sanitization, rebase introspection, host-event parity, the
mediator's continue-check gating and note-required fail-closed behavior, and the
orchestrator's conflict-mode kill-and-relaunch vs rebase-mode reply-and-continue
driven by a scripted fake ``opencode`` over a multi-conflict repository.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import cast

import pytest

from forklift_harness.mediate import main as mediate_main
from forklift_harness.orchestrate import main as orchestrate_main
from forklift_harness.rebase_state import (
    HarnessConfig,
    RebaseState,
    classify_paused_rebase_command,
)

REAL_GIT = shutil.which("git") or "/usr/bin/git"
WRAPPER_BIN_DIR = (
    Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/includes/bin"
)
HARNESS_PY_DIR = Path(__file__).resolve().parents[1] / "docker/kitchen-sink/harness/py"


# --------------------------------------------------------------------------- #
# git fixtures
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    return subprocess.run(
        [REAL_GIT, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_conflicting_repo(workspace: Path, n: int) -> None:
    """Build a repo whose `main` conflicts with `upstream/main` on each of n commits."""

    workspace.mkdir(parents=True, exist_ok=True)
    _ = _git(workspace, "init", "-b", "main")
    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text("base\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD").stdout.strip()

    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "upstream")
    upstream = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    _ = _git(workspace, "update-ref", "refs/remotes/upstream/main", upstream)

    _ = _git(workspace, "reset", "--hard", base)
    for i in range(n):
        _ = (workspace / f"a{i}.txt").write_text(f"main-{i}\n", encoding="utf-8")
        _ = _git(workspace, "add", "-A")
        _ = _git(workspace, "commit", "-m", f"main {i}")


def _make_clean_repo(workspace: Path) -> None:
    """Build a repo whose `main` rebases cleanly onto `upstream/main`."""

    workspace.mkdir(parents=True, exist_ok=True)
    _ = _git(workspace, "init", "-b", "main")
    _ = (workspace / "a.txt").write_text("base\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD").stdout.strip()

    _ = (workspace / "a.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "upstream")
    upstream = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    _ = _git(workspace, "update-ref", "refs/remotes/upstream/main", upstream)

    _ = _git(workspace, "reset", "--hard", base)
    _ = (workspace / "b.txt").write_text("fork\n", encoding="utf-8")
    _ = _git(workspace, "add", "-A")
    _ = _git(workspace, "commit", "-m", "fork b")


def _start_rebase(workspace: Path) -> None:
    """Run the initial rebase so the workspace is paused on the first conflict."""

    proc = subprocess.run(
        [REAL_GIT, "rebase", "upstream/main"],
        cwd=str(workspace),
        env={
            **os.environ,
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0  # paused on conflict


def _config(
    workspace: Path,
    harness_state: Path,
    *,
    agent_lifetime: str = "conflict",
    events_sock: str | None = None,
    control_sock: str | None = None,
) -> HarnessConfig:
    harness_state.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=harness_state,
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=harness_state / "rebase-continue-check.sh",
        client_log=harness_state / "opencode-client.log",
        events_sock=events_sock,
        control_sock=control_sock or str(harness_state / "control.sock"),
        agent_lifetime=agent_lifetime,
    )


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #


def test_classify_continue_with_resolution_note() -> None:
    cmd = classify_paused_rebase_command(
        ["rebase", "--continue", "--resolution-note", "fixed it"]
    )
    assert cmd.action == "continue"
    assert cmd.resolution_note == "fixed it"


def test_classify_continue_with_inline_note() -> None:
    cmd = classify_paused_rebase_command(
        ["rebase", "--continue", "--resolution-note=inline note"]
    )
    assert cmd.action == "continue"
    assert cmd.resolution_note == "inline note"


def test_classify_skip_with_note() -> None:
    cmd = classify_paused_rebase_command(
        ["rebase", "--skip", "--resolution-note", "drop empty"]
    )
    assert cmd.action == "skip"
    assert cmd.resolution_note == "drop empty"


def test_classify_abort_with_reason() -> None:
    cmd = classify_paused_rebase_command(
        ["rebase", "--abort", "--reason", "needs a human"]
    )
    assert cmd.action == "abort"
    assert cmd.reason == "needs a human"


def test_classify_continue_without_note_leaves_note_none() -> None:
    cmd = classify_paused_rebase_command(["rebase", "--continue"])
    assert cmd.action == "continue"
    assert cmd.resolution_note is None


def test_classify_passthrough_read_only_command() -> None:
    cmd = classify_paused_rebase_command(["status"])
    assert cmd.action == "passthrough"


def test_classify_passthrough_with_benign_config() -> None:
    cmd = classify_paused_rebase_command(["-c", "user.name=x", "status"])
    assert cmd.action == "passthrough"


def test_classify_rejects_dangerous_config_on_rebase() -> None:
    cmd = classify_paused_rebase_command(
        ["-c", "core.pager=less", "rebase", "--continue", "--resolution-note", "x"]
    )
    assert cmd.action == "unsupported"


def test_classify_rejects_alias_config() -> None:
    cmd = classify_paused_rebase_command(
        ["-c", "alias.x=!sh", "rebase", "--continue", "--resolution-note", "x"]
    )
    assert cmd.action == "unsupported"


def test_classify_rejects_config_env_redirect() -> None:
    cmd = classify_paused_rebase_command(
        ["--config-env", "FOO", "rebase", "--continue"]
    )
    assert cmd.action == "unsupported"


def test_classify_rejects_exec_path() -> None:
    cmd = classify_paused_rebase_command(["--exec-path=/tmp", "rebase", "--abort"])
    assert cmd.action == "unsupported"


def test_classify_rejects_unknown_command() -> None:
    cmd = classify_paused_rebase_command(["rm", "-rf", "."])
    assert cmd.action == "unsupported"


def test_classify_sanitizes_control_characters_and_whitespace() -> None:
    cmd = classify_paused_rebase_command(
        ["rebase", "--continue", "--resolution-note", "  a\nb\x01c  "]
    )
    assert cmd.resolution_note == "abc"


# --------------------------------------------------------------------------- #
# RebaseState introspection + events
# --------------------------------------------------------------------------- #


def test_rebase_state_reads_progress_and_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 2)
    _start_rebase(workspace)
    state = RebaseState(_config(workspace, tmp_path / "state"))

    assert state.rebase_in_progress() is True
    progress = state.read_progress()
    assert progress is not None
    assert progress.total == 2
    assert progress.step == 1
    assert "a0.txt" in progress.files
    identity = state.rebase_head_identity()
    assert identity is not None
    assert identity[1] == "main 0"


def test_count_rebase_commits_before_rebase(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 2)
    state = RebaseState(_config(workspace, tmp_path / "state"))
    # On `main` before rebasing, both fork commits are ahead of upstream/main.
    assert state.count_rebase_commits() == 2


def test_rebase_state_clean_empty_stop_detection(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    state = RebaseState(_config(workspace, tmp_path / "state"))
    # A real conflict has a dirty (unmerged) tree, so it is not a clean empty stop.
    assert state.is_clean_empty_stop() is False


def test_emit_event_payload_parity(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)

    sock_path = tmp_path / "events.sock"
    received: list[dict[str, object]] = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    listener.listen()
    listener.settimeout(2)

    def serve() -> None:
        conn = listener.accept()[0]
        with conn:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                received.append(cast(dict[str, object], json.loads(line)))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    state = RebaseState(
        _config(workspace, tmp_path / "state", events_sock=str(sock_path))
    )
    state.emit_event_from_snapshot("conflict")
    thread.join(timeout=2)
    listener.close()

    assert len(received) == 1
    payload = received[0]
    assert payload["v"] == 1
    assert payload["event"] == "conflict"
    assert payload["step"] == 1
    assert payload["total"] == 1
    assert payload["files"] == ["a0.txt"]


def test_emit_event_ignores_missing_socket(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    state = RebaseState(
        _config(workspace, tmp_path / "state", events_sock=str(tmp_path / "nope.sock"))
    )
    # Should not raise even though the socket does not exist.
    state.emit_event_from_snapshot("conflict")


# --------------------------------------------------------------------------- #
# mediator continue-check gating + note-required (no orchestrator socket)
# --------------------------------------------------------------------------- #


def _mediate_env(config: HarnessConfig) -> dict[str, str]:
    return {
        "WORKSPACE_DIR": str(config.workspace_dir),
        "HARNESS_STATE_DIR": str(config.harness_state_dir),
        "REAL_GIT_BIN": config.real_git_bin,
        "FORKLIFT_MAIN_BRANCH": config.main_branch,
        "UPSTREAM_REF": config.upstream_ref,
        "REBASE_CONTINUE_CHECK_FILE": str(config.continue_check_file),
        "CLIENT_LOG": str(config.client_log),
        "FORKLIFT_REBASE_CONTROL_SOCK": config.control_sock,
        "FORKLIFT_AGENT_LIFETIME": config.agent_lifetime,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }


def _run_mediate(
    config: HarnessConfig, args: list[str], monkeypatch: pytest.MonkeyPatch
) -> int:
    for key, value in _mediate_env(config).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FORKLIFT_REBASE_EVENTS_SOCK", raising=False)
    return mediate_main(args)


def test_mediate_continue_requires_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    config = _config(workspace, tmp_path / "state")
    code = _run_mediate(config, ["rebase", "--continue"], monkeypatch)
    assert code == 1


def test_mediate_abort_requires_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    config = _config(workspace, tmp_path / "state")
    code = _run_mediate(config, ["rebase", "--abort"], monkeypatch)
    assert code == 1
    # Rebase must still be in progress because the abort was rejected.
    assert (workspace / ".git" / "rebase-merge").is_dir()


def test_mediate_continue_blocks_on_failing_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    # Resolve the conflict so the check is the only gate.
    _start_rebase(workspace)
    _ = _git(workspace, "checkout", "--theirs", "a0.txt")
    _ = _git(workspace, "add", "a0.txt")
    config = _config(workspace, tmp_path / "state")
    _ = config.continue_check_file.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 7\n", encoding="utf-8"
    )
    code = _run_mediate(
        config, ["rebase", "--continue", "--resolution-note", "x"], monkeypatch
    )
    assert code == 1
    # The failing check must not have advanced the rebase.
    assert (workspace / ".git" / "rebase-merge").is_dir()


def test_mediate_continue_blocks_on_tracked_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)
    _ = _git(workspace, "checkout", "--theirs", "a0.txt")
    _ = _git(workspace, "add", "a0.txt")
    config = _config(workspace, tmp_path / "state")
    _ = config.continue_check_file.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho mutated >> a0.txt\n",
        encoding="utf-8",
    )
    code = _run_mediate(
        config, ["rebase", "--continue", "--resolution-note", "x"], monkeypatch
    )
    assert code == 1
    assert (workspace / ".git" / "rebase-merge").is_dir()


def test_continue_check_command_text_strips_writer_preamble(
    tmp_path: Path,
) -> None:
    """The bash writer's preamble must round-trip out via the Python reader.

    Guards the cross-language coupling: the marker the shell appends and the
    marker the Python reader strips have to stay in sync, so drive the real
    writer and assert only the fork's own commands come back.
    """

    config = _config(tmp_path / "ws", tmp_path / "state")
    rebase_sh = WRAPPER_BIN_DIR.parent / "rebase.sh"
    body = "echo first-check\nexit 0"
    result = subprocess.run(
        ["bash", "-c", f'source "{rebase_sh}"; write_rebase_continue_check_file'],
        env={
            **os.environ,
            "FORK_REBASE_CONTINUE_CHECK": body,
            "REBASE_CONTINUE_CHECK_FILE": str(config.continue_check_file),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0

    state = RebaseState(config)
    assert state.continue_check_command_text() == body


def test_continue_check_command_text_strips_extra_marked_preamble_lines(
    tmp_path: Path,
) -> None:
    """Adding a marked preamble line must not silently truncate fork commands."""

    config = _config(tmp_path / "ws", tmp_path / "state")
    _ = config.continue_check_file.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail  # forklift:continue-check-preamble\ncd /workspace  # forklift:continue-check-preamble\necho body-line\n",
        encoding="utf-8",
    )
    state = RebaseState(config)
    assert state.continue_check_command_text() == "echo body-line"


# --------------------------------------------------------------------------- #
# orchestrator integration with a scripted fake opencode
# --------------------------------------------------------------------------- #


_FAKE_OPENCODE = """#!/usr/bin/env bash
set -uo pipefail
echo launch >> "{launch_log}"
cd "$WORKSPACE_DIR"
while [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; do
  files=$(git diff --name-only --diff-filter=U)
  [ -z "$files" ] && break
  for f in $files; do
    git checkout --theirs "$f" >/dev/null 2>&1 || true
    git add "$f"
  done
  # `git rebase --continue` exits nonzero when it pauses on the NEXT conflict;
  # that is expected, so drive off the rebase state, not the exit code.
  git rebase --continue --resolution-note "resolved by fake" || true
done
exit 0
"""

_FAKE_OPENCODE_ABORT = """#!/usr/bin/env bash
set -uo pipefail
echo launch >> "{launch_log}"
cd "$WORKSPACE_DIR"
git rebase --abort --reason "cannot resolve, human needed" || exit 0
exit 0
"""


def _orchestrator_env(config: HarnessConfig, fake_opencode: Path) -> dict[str, str]:
    path = f"{WRAPPER_BIN_DIR}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    return {
        "WORKSPACE_DIR": str(config.workspace_dir),
        "HARNESS_STATE_DIR": str(config.harness_state_dir),
        "REAL_GIT_BIN": config.real_git_bin,
        "FORKLIFT_MAIN_BRANCH": "main",
        "UPSTREAM_REF": "upstream/main",
        "REBASE_CONTINUE_CHECK_FILE": str(config.continue_check_file),
        "CLIENT_LOG": str(config.client_log),
        "FORKLIFT_REBASE_CONTROL_SOCK": config.control_sock,
        "FORKLIFT_AGENT_LIFETIME": config.agent_lifetime,
        "INSTRUCTIONS_FILE": str(config.harness_state_dir / "instructions.txt"),
        "FORK_CONTEXT_FILE": str(config.harness_state_dir / "fork-context.md"),
        "OPENCODE_BIN": str(fake_opencode),
        "OPENCODE_VARIANT": "test",
        "OPENCODE_AGENT": "test",
        "OPENCODE_TIMEOUT": "60",
        "OPENCODE_SERVER_PORT": "4096",
        "PYTHONPATH": str(HARNESS_PY_DIR),
        "PATH": path,
    }


def _write_fake(tmp_path: Path, template: str, launch_log: Path) -> Path:
    fake = tmp_path / "fake-opencode.sh"
    _ = fake.write_text(template.format(launch_log=launch_log), encoding="utf-8")
    fake.chmod(0o755)
    return fake


def _prep_instructions(harness_state: Path) -> None:
    harness_state.mkdir(parents=True, exist_ok=True)
    _ = (harness_state / "instructions.txt").write_text(
        "Resolve the conflict.\n", encoding="utf-8"
    )
    _ = (harness_state / "fork-context.md").write_text(
        "No FORK context.\n", encoding="utf-8"
    )


def _run_orchestrator(
    config: HarnessConfig,
    fake_opencode: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    _prep_instructions(config.harness_state_dir)
    Path(WRAPPER_BIN_DIR / "git").chmod(0o755)
    for key, value in _orchestrator_env(config, fake_opencode).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FORKLIFT_REBASE_EVENTS_SOCK", raising=False)
    return orchestrate_main()


def _load_report(harness_state: Path) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((harness_state / "rebase-report.json").read_text(encoding="utf-8")),
    )


def test_orchestrator_conflict_mode_relaunches_per_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    harness_state = tmp_path / "state"
    _make_conflicting_repo(workspace, 3)
    launch_log = tmp_path / "launches.txt"
    fake = _write_fake(tmp_path, _FAKE_OPENCODE, launch_log)
    config = _config(workspace, harness_state, agent_lifetime="conflict")

    code = _run_orchestrator(config, fake, monkeypatch)

    assert code == 0
    assert not (workspace / ".git" / "rebase-merge").is_dir()
    launches = launch_log.read_text(encoding="utf-8").count("launch")
    assert launches == 3
    report = _load_report(harness_state)
    assert report["outcome"] == "completed"
    resolutions = report["resolutions"]
    assert isinstance(resolutions, list)
    resolutions = cast(list[object], resolutions)
    assert len(resolutions) == 3


def test_orchestrator_rebase_mode_single_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    harness_state = tmp_path / "state"
    _make_conflicting_repo(workspace, 3)
    launch_log = tmp_path / "launches.txt"
    fake = _write_fake(tmp_path, _FAKE_OPENCODE, launch_log)
    config = _config(workspace, harness_state, agent_lifetime="rebase")

    code = _run_orchestrator(config, fake, monkeypatch)

    assert code == 0
    assert not (workspace / ".git" / "rebase-merge").is_dir()
    launches = launch_log.read_text(encoding="utf-8").count("launch")
    assert launches == 1
    report = _load_report(harness_state)
    assert report["outcome"] == "completed"
    resolutions = report["resolutions"]
    assert isinstance(resolutions, list)
    resolutions = cast(list[object], resolutions)
    assert len(resolutions) == 3


def test_orchestrator_clean_rebase_completes_without_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    harness_state = tmp_path / "state"
    _make_clean_repo(workspace)
    launch_log = tmp_path / "launches.txt"
    fake = _write_fake(tmp_path, _FAKE_OPENCODE, launch_log)
    config = _config(workspace, harness_state, agent_lifetime="conflict")

    code = _run_orchestrator(config, fake, monkeypatch)

    assert code == 0
    assert not launch_log.exists()
    report = _load_report(harness_state)
    assert report["outcome"] == "completed"


def test_orchestrator_stuck_path_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    harness_state = tmp_path / "state"
    _make_conflicting_repo(workspace, 2)
    launch_log = tmp_path / "launches.txt"
    fake = _write_fake(tmp_path, _FAKE_OPENCODE_ABORT, launch_log)
    config = _config(workspace, harness_state, agent_lifetime="conflict")

    code = _run_orchestrator(config, fake, monkeypatch)

    assert code == 0  # harness ran fine; host owns the exit-4 decision
    report = _load_report(harness_state)
    assert report["outcome"] == "stuck"
    assert report["stuck"] is not None
    status = (harness_state / "harness-status.txt").read_text(encoding="utf-8")
    assert "status=completed" in status
