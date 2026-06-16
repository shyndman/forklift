"""Host-side tests for the in-container Forklift harness package.

These replace the bash-shelled rebase-mediation cases that previously lived in
``test_harness_setup.py``. They exercise the Python port directly: command
classification, note sanitization, rebase introspection, host-event parity, the
mediator's continue-check gating and note-required fail-closed behavior, and the
orchestrator's conflict-mode kill-and-relaunch vs rebase-mode reply-and-continue
driven by a scripted fake ``opencode`` over a multi-conflict repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from structlog.testing import capture_logs

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
) -> HarnessConfig:
    harness_state.mkdir(parents=True, exist_ok=True)
    return HarnessConfig(
        workspace_dir=workspace,
        harness_state_dir=harness_state,
        real_git_bin=REAL_GIT,
        main_branch="main",
        upstream_ref="upstream/main",
        continue_check_file=harness_state / "rebase-continue-check.sh",
        agent_lifetime=agent_lifetime,
        conflict_index_snapshot=harness_state / "conflict-index",
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


def test_classify_rejects_config_on_passthrough() -> None:
    cmd = classify_paused_rebase_command(["-c", "user.name=x", "status"])
    assert cmd.action == "unsupported"


def test_classify_rejects_redirect_config_on_passthrough() -> None:
    cmd = classify_paused_rebase_command(["-c", "core.hooksPath=/tmp/evil", "status"])
    assert cmd.action == "unsupported"


def test_classify_rejects_fsmonitor_config_on_staging() -> None:
    cmd = classify_paused_rebase_command(["-c", "core.fsmonitor=/tmp/evil", "add", "."])
    assert cmd.action == "unsupported"


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


def test_classify_reset_conflict() -> None:
    cmd = classify_paused_rebase_command(["reset-conflict"])
    assert cmd.action == "reset"


def test_classify_reset_conflict_rejects_config_override() -> None:
    cmd = classify_paused_rebase_command(["-c", "core.pager=cat", "reset-conflict"])
    assert cmd.action == "unsupported"


def test_classify_reset_conflict_rejects_extra_recognized_token() -> None:
    cmd = classify_paused_rebase_command(["reset-conflict", "rebase"])
    assert cmd.action == "unsupported"


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


def test_auto_skip_clean_empty_stop_announces_next_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 2)
    _start_rebase(workspace)

    # Resolve the first conflict to the upstream content so the staged tree is clean,
    # turning this stop into a mechanically-empty one.
    _ = (workspace / "a0.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "a0.txt")

    config = _config(workspace, tmp_path / "state")
    state = RebaseState(config)
    assert state.is_clean_empty_stop() is True

    with capture_logs() as logs:
        _ = state.auto_skip_clean_empty_stop()

    # The skip advances to the next conflict; git rebase --skip exits non-zero here.
    assert state.rebase_in_progress() is True
    progress = state.read_progress()
    assert progress is not None
    assert progress.step == 2
    assert progress.total == 2

    # The next conflict must be announced to the host, not silently skipped.
    conflicts = [e for e in logs if e.get("event") == "conflict 2/2"]
    assert len(conflicts) == 1
    assert conflicts[0]["log_level"] == "warning"
    assert conflicts[0]["files"] == ["a1.txt"]

    # The conflict-index snapshot for the new step must be captured for reset-conflict.
    assert config.conflict_index_snapshot.exists()


def test_auto_skip_clean_empty_stop_completion_emits_once(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)

    _ = (workspace / "a0.txt").write_text("upstream\n", encoding="utf-8")
    _ = _git(workspace, "add", "a0.txt")

    state = RebaseState(_config(workspace, tmp_path / "state"))
    assert state.is_clean_empty_stop() is True

    with capture_logs() as logs:
        _ = state.auto_skip_clean_empty_stop()

    # Skipping the only commit finishes the rebase; completion must fire exactly once.
    assert state.rebase_in_progress() is False
    completes = [e for e in logs if e.get("event") == "rebase complete"]
    assert len(completes) == 1


def test_emit_conflict_record_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    _make_conflicting_repo(workspace, 1)
    _start_rebase(workspace)

    state = RebaseState(_config(workspace, tmp_path / "state"))
    with capture_logs() as logs:
        state.emit_event_from_snapshot("conflict")

    conflicts = [e for e in logs if e.get("event") == "conflict 1/1"]
    assert len(conflicts) == 1
    record = conflicts[0]
    assert record["log_level"] == "warning"
    assert record["step"] == 1
    assert record["total"] == 1
    assert record["files"] == ["a0.txt"]


def test_snapshot_conflict_index_discards_stale_on_unidentifiable_capture(
    tmp_path: Path,
) -> None:
    # No rebase in progress, so the step identity is unavailable.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    config = _config(workspace, tmp_path / "state")
    state = RebaseState(config)

    # Seed a stale snapshot + identity sidecar from an earlier conflict.
    config.conflict_index_snapshot.parent.mkdir(parents=True, exist_ok=True)
    _ = config.conflict_index_snapshot.write_bytes(b"stale-index")
    meta = config.conflict_index_snapshot.parent / (
        config.conflict_index_snapshot.name + ".meta"
    )
    _ = meta.write_text("3/5:cafebabe", encoding="utf-8")

    state.snapshot_conflict_index()

    # An unidentifiable capture must wipe the stale snapshot so a later reset cannot
    # restore another step's index.
    assert not config.conflict_index_snapshot.exists()
    assert not meta.exists()


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
