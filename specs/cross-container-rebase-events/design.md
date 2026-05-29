## Context

Forklift already owns the most important paused-rebase transitions inside the harness: it starts the initial `git rebase`, mediates `git rebase --continue|--skip|--abort`, and records machine-readable skip/conflict artifacts in `harness-state`. What it still lacks is a live, structured path back to the host while the container is running. Today `ContainerRunner.run()` launches the container with `subprocess.Popen(..., stdout=PIPE, stderr=PIPE)` and then waits on `process.communicate()`, so the host only emits top-level `Container stdout` / `Container stderr` logs after the container exits.

That transport is the wrong place for this feature. The discussion locked in three constraints:
- live rebase progress MUST NOT rely on polling a file from the host;
- structured host/harness communication MUST NOT piggyback on stdout;
- the harness sender SHOULD use Python, which is already present in the image, rather than adding `netcat` or another runtime dependency.

Relevant implementation touchpoints today:
- `src/forklift/run_manager.py` prepares `workspace/`, `harness-state/`, and `opencode-logs/`, returning `RunPaths` with no dedicated control mount.
- `src/forklift/container_runner.py` builds `docker run` mounts for `workspace`, `harness-state`, and `opencode-logs`, but has no side channel for structured container-to-host events.
- `docker/kitchen-sink/harness/run.sh` already exports rebase-related paths and sources `includes/rebase.sh` before starting the initial rebase.
- `docker/kitchen-sink/harness/includes/rebase.sh` already knows when the rebase pauses, when `--continue` is invoked, when explicit `--skip` happens, and when abort is allowed.
- Git exposes rebase ordinal/total through backend-specific state files: `.git/rebase-merge/msgnum` + `.git/rebase-merge/end` for the merge backend, and `.git/rebase-apply/next` + `.git/rebase-apply/last` for the apply backend.

Stakeholders:
- operators who need immediate, top-level visibility into where a run is blocked;
- the harness, which already has the authoritative rebase state and should remain the source of truth;
- maintainers who still need existing artifacts (`opencode-client.log`, `harness-status.txt`, `STUCK.md`, skip metadata) to remain stable.

## Goals / Non-Goals

**Goals:**
- Add a dedicated, structured cross-container event channel for live rebase progress and conflict reporting.
- Surface visually front-loaded top-level logs such as `Rebase 5/31` and `Conflict 5/31` while preserving structured `step` and `total` fields.
- Keep stdout/stderr as human-oriented logs and keep `opencode-client.log` as the deep transcript artifact.
- Reuse the harness’s existing paused-rebase authority instead of re-deriving state from host-side Git inspection.
- Make event delivery best-effort and additive: missing events should reduce visibility, not break a run.

**Non-Goals:**
- Streaming every successful cleanly-applied commit directly from Git’s own progress output.
- Replacing `harness-status.txt`, `opencode-client.log`, `DONE.md`, `STUCK.md`, or skipped-commit metadata.
- Building a general-purpose RPC channel between host and container.
- Refactoring the existing stdout/stderr transport into a live streaming terminal view.
- Introducing any new image dependency beyond the Python already shipped in the kitchen-sink image.
- Expanding host support beyond the current Linux + local Docker daemon operating model that Forklift already assumes.

## Verified dependency inventory

No new Python package dependency or image package dependency is required for this feature.

Verified dependencies and runtime surfaces the implementation is allowed to use:
- **Python standard library (repo runtime `>=3.13`)**: implementation uses only built-in modules already available in the image and host interpreter: `socket`, `threading`, `json`, `os`, `pathlib`, and existing `subprocess`/`time` usage. The socket API was verified against the official Python socket documentation for 3.14.5; the specific `AF_UNIX`/stream APIs used here are stable and available on Python 3.13.
- **structlog (`structlog~=25.5` in `pyproject.toml`)**: verified against the official structlog 25.5.0 documentation and source. Forklift already uses `structlog.get_logger(__name__)` and the stdlib integration via `structlog.stdlib.BoundLogger`; this feature reuses that existing API surface and does not require any logging-library upgrade or reconfiguration.
- **Docker CLI / bind mounts**: verified against the current official `docker run` and bind-mount documentation. Forklift already depends on Docker and already launches containers with bind mounts and explicit `-e KEY=VALUE` forwarding from the `extra_env` mapping passed into `ContainerRunner.run(...)`; this feature adds one more bind-mounted directory and one more environment variable, but does not require a new Docker-side component.
- **Git inside the kitchen-sink image**: the harness continues using the existing `git` binary and the existing paused-rebase metadata files under `.git/rebase-merge` and `.git/rebase-apply`. No new Git extension or external helper is introduced.

Dependency review result:
- Do **not** add `netcat`, `socat`, a Python socket helper package, or a background sidecar process.
- Do **not** add a JSON schema or IPC framework dependency; the payloads are small enough to validate with built-in Python and shell logic.

## Decisions

1. **Use a host-created Unix domain socket in a dedicated control mount**
   - Decision: `RunDirectoryManager.prepare()` will create a sibling control directory under the run directory, e.g. `run_dir/control/`, and `RunPaths` will grow a `control_dir: Path` field. `ContainerRunner` will create a Unix domain socket inside that directory before launch, bind-mount the directory into the container at `/forklift-control`, and export `FORKLIFT_REBASE_EVENTS_SOCK=/forklift-control/rebase-events.sock`.
   - The host MUST validate the filesystem socket pathname before `bind()`. Linux pathname UNIX sockets are limited by `sockaddr_un.sun_path`, which is 108 bytes including the terminating null byte, so the encoded pathname for `run_dir/control/rebase-events.sock` MUST be `<= 107` bytes. If it is longer, Forklift MUST fail early with an actionable error telling the operator to shorten `XDG_STATE_HOME` or the repository/run path.
   - Why: this gives the harness a purpose-built structured channel without abusing stdout or requiring the host to poll a file. Mounting the directory rather than the socket path directly keeps socket lifecycle normal on both sides.
   - Alternatives considered:
     - Poll a JSON file in `harness-state`: rejected per discussion and because it adds latency, race windows, and needless file churn.
     - Parse structured markers from container stdout: rejected because stdout should remain a human log surface and accidental output should not corrupt the protocol.
     - Reuse `run-state.json`: rejected because the container cannot see that file and the use case needs live streaming, not host-side lifecycle snapshots.

2. **Keep the socket protocol tiny: one newline-delimited JSON event per send**
   - Decision: the harness will open a fresh socket connection per event, send exactly one UTF-8 JSON object plus a trailing newline, then close the connection. The host listener accepts many short-lived connections and parses newline-delimited payloads independently.
   - Event payload shape:
     ```json
     {"v":1,"event":"conflict","step":5,"total":31,"sha":"abc123def456","subject":"Rename auth middleware","files":["src/auth.py","tests/test_auth.py"]}
     ```
   - Required keys for all payloads: `v`, `event`, `step`, `total`.
   - Optional keys when known: `sha`, `subject`, `files`.
   - Supported event values in v1: `progress`, `conflict`, `continue`, `skip`, `auto_skip`, `complete`, `abort`.
   - Important current-state note: `auto_skip` is **not** an existing emitted concept in the codebase today. `rebase.sh` currently has only an implicit mechanically-empty-commit auto-skip branch inside `handle_rebase_continue()`; this feature introduces the first dedicated `auto_skip` event for that path.
   - Why: a one-event-per-connection sender is trivial to implement from shell via Python and avoids long-lived connection management inside the harness. NDJSON remains easy to debug and tolerant of incremental host reads.
   - Alternatives considered:
     - A persistent bidirectional session: rejected because the harness only needs fire-and-forget notifications and shell-managed connection reuse is brittle.
     - Binary framing: rejected because the payloads are small and JSON is simpler to inspect in tests and diagnostics.
     - Multiple events bundled into one connection: rejected because it complicates sender state without reducing meaningful overhead.

3. **Derive event content from harness-owned rebase state, not host inference**
   - Decision: `docker/kitchen-sink/harness/includes/rebase.sh` is the paused-rebase interception layer and will add helpers that read the active rebase backend files, current `REBASE_HEAD`, and conflicted files to build a normalized progress snapshot before emitting events. The event hooks belong in `start_initial_rebase()`, `handle_rebase_continue()`, `handle_rebase_skip()`, `handle_rebase_abort()`, the implicit auto-skip branch inside `handle_rebase_continue()`, and the paused-command classification path that already normalizes `git rebase --continue|--skip|--abort`.
   - Snapshot derivation rules:
     - merge backend: read `.git/rebase-merge/msgnum` and `.git/rebase-merge/end`
     - apply backend: read `.git/rebase-apply/next` and `.git/rebase-apply/last`
     - current commit SHA: `git -C "$WORKSPACE_DIR" rev-parse REBASE_HEAD`
     - current subject: `git -C "$WORKSPACE_DIR" show -s --format=%s REBASE_HEAD`
     - conflicted files: `git -C "$WORKSPACE_DIR" diff --name-only --diff-filter=U`
   - Emit points:
     - when the initial rebase pauses on conflicts: `progress` then `conflict`
     - immediately before allowing a real `git rebase --continue`: `continue`
     - when `--continue` lands on another paused conflict: `progress` then `conflict`
     - when the agent explicitly skips: `skip`
     - when the existing implicit mechanically-empty-commit branch inside `handle_rebase_continue()` fires after failed `git rebase --continue`: capture the pre-skip `REBASE_HEAD` snapshot, emit the new `auto_skip` event for that commit, run the real `git rebase --skip`, then emit the resulting paused/conflict or completion event for the next state
     - when the rebase finishes cleanly: `complete`
     - when abort is allowed and executed after `STUCK.md`: `abort`
   - Why: the harness is already the component deciding what the rebase is doing. Re-deriving the same facts from the host would be slower, less accurate, and harder to test.
   - Alternatives considered:
     - Host-side Git inspection of the mounted workspace: rejected because it duplicates logic and races with in-container state changes.
     - Parsing Git’s stderr text like `Rebasing (5/31)`: rejected because it is backend- and locale-sensitive and does not solve explicit conflict/skip semantics cleanly.

4. **Treat socket transport as additive and non-fatal**
   - Decision: event emission failures in the harness MUST NOT fail the rebase. The Python sender returns success on best effort, but any connection failure, timeout, or encoding problem is reduced to a local transcript/stdout note. On the host side, malformed JSON, unknown versions, or unknown event types are logged as warnings and ignored.
   - Why: progress visibility is valuable, but the rebase itself is the product-critical operation. A broken progress channel must never strand a valid run.
   - Mixed-version behavior:
     - new host + old image: host sees no events and falls back to current behavior;
     - old host + new image: harness sees no socket env var and silently skips event emission;
     - partial event loss: host logs whatever it receives and still relies on existing completion/status artifacts for final outcome.
   - Alternatives considered:
     - Fail the run when events cannot be delivered: rejected because it turns an observability feature into a correctness dependency.
     - Retry indefinitely from the harness: rejected because it risks blocking the rebase on a best-effort side channel.

5. **Render top-level logs with front-loaded ordinals plus stable structured fields**
   - Decision: the host listener will map socket events into immediate structlog calls with visually front-loaded event text while retaining machine-meaningful fields.
   - Expected top-level format:
     ```text
     INFO  Rebase 5/31        step=5 total=31 sha=abc123def456 subject="Rename auth middleware"
     WARN  Conflict 5/31      step=5 total=31 sha=abc123def456 subject="Rename auth middleware" conflict_files=2 files="src/auth.py, tests/test_auth.py"
     INFO  Continue 5/31      step=5 total=31 sha=abc123def456 subject="Rename auth middleware"
     INFO  Skip 6/31          step=6 total=31 sha=def456abc789 subject="Handle null upstream config"
     INFO  Rebase complete    step=31 total=31
     ```
   - Mapping rules:
     - `progress` → `logger.info("Rebase {step}/{total}", ...)`
     - `conflict` → `logger.warning("Conflict {step}/{total}", ..., conflict_files=len(files), files=", ".join(files))`
     - `continue` / `skip` / `auto_skip` / `abort` → `logger.info(...)`
     - `complete` → `logger.info("Rebase complete", step=..., total=...)`
   - Why: this preserves the user-facing readability goal without sacrificing structured fields for filtering or future automation.
   - Alternatives considered:
     - put the ordinal only in structured fields: rejected because it buries the most important context in the long tail of the log line;
     - put the ordinal only in the message string: rejected because later tooling would lose `step`/`total` without parsing text.

6. **Implement the host listener inside `ContainerRunner.run()` with a dedicated background thread**
   - Decision: `ContainerRunner.run()` will remain responsible for container lifecycle, but it will spin up a dedicated Unix-socket listener thread before `subprocess.Popen()`. That thread will accept connections with a short timeout, parse events, and invoke a small callback that logs them immediately. The existing `process.communicate()` stdout/stderr collection remains unchanged.
   - Why: this is the smallest host change that adds live event handling without rewriting the existing container stdout/stderr transport. The socket listener is orthogonal to `communicate()`.
   - Implementation shape:
     - create listener socket;
     - `chmod` the socket path permissively enough for container UID/GID 1000 to connect;
     - start listener thread and stop event;
     - launch container;
     - wait on `communicate()` as today;
     - stop listener, close socket, remove stale socket path.
   - The listener SHOULD log from the background thread directly through the existing `structlog.stdlib.BoundLogger` rather than introduce an extra queue, because the current logger API already proxies to the thread-safe standard-library logging surface.
   - Alternatives considered:
     - selectors/event loop in the main thread: rejected because `communicate()` already owns that flow and the extra complexity buys little.
     - a separate long-lived daemon process: rejected because the event channel is per-run state and belongs to the container lifecycle.

7. **Keep control-path ownership separate from `harness-state` artifacts**
   - Decision: the socket lives in `run_dir/control/`, not under `harness-state/`. `RunDirectoryManager._ensure_permissions(...)` currently aligns ownership only for `workspace`, `harness-state`, and `opencode-logs`; this feature must extend that same uid/gid-1000 ownership setup to the new control directory so the container’s non-root `forklift` user can traverse the mount. The host listener will also remove any stale socket path before binding.
   - Why: `harness-state` is for durable files the operator may inspect after a run. The socket is ephemeral runtime control plumbing and should not be confused with saved artifacts.
   - Alternatives considered:
     - put the socket under `harness-state`: rejected because it mixes an ephemeral IPC endpoint into an artifact directory and increases the chance of accidental cleanup/inspection confusion.
     - mount a host temp directory outside the run tree: rejected because the channel should stay co-located with other per-run state for auditability and cleanup.

## Implementation API reference

This section is the normative API guide for implementation. No additional dependency/API lookup should be necessary.

### Current codebase APIs to extend

#### `src/forklift/run_manager.py`

Current definitions:

```python
@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    workspace: Path
    harness_state: Path
    opencode_logs: Path
    run_id: str

class RunDirectoryManager:
    def prepare(
        self,
        source_repo: Path,
        main_branch: str = "main",
        selected_upstream_sha: str | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> RunPaths: ...

    def _ensure_permissions(self, *paths: Path) -> None: ...
```

Current behavior to preserve while extending:
- `prepare()` computes `run_dir/<project>_<timestamp>/workspace`, `harness-state`, and `opencode-logs`, creates those directories, clones into `workspace`, writes metadata, initializes `run-state.json`, seeds upstream, then calls `_ensure_permissions(workspace, harness_state, opencode_logs)`.
- `_ensure_permissions(*paths)` recursively `chown`s each provided path to `CONTAINER_UID=1000` / `CONTAINER_GID=1000`.
- This feature adds `control_dir: Path` to `RunPaths`, creates `run_dir/control`, and extends the same `_ensure_permissions(...)` call to include it.

#### `src/forklift/container_runner.py`

Current definitions:

```python
@dataclass(frozen=True)
class ContainerRunResult:
    exit_code: int
    timed_out: bool
    stdout: str
    stderr: str
    container_name: str

class ContainerRunner:
    def run(
        self,
        workspace: Path,
        harness_state: Path,
        opencode_logs: Path,
        run_state_file: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> ContainerRunResult: ...

    def _build_command(
        self,
        container_name: str,
        workspace: Path,
        harness_state: Path,
        opencode_logs: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> list[str]: ...
```

Current behavior to preserve while extending:
- `run()` creates the container name, builds the Docker command, logs launch metadata, starts `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True)`, records run-state transitions, computes `client_log_path = harness_state / "opencode-client.log"`, and blocks on `process.communicate(timeout=...)`.
- On timeout it tries `_force_stop(container_name)`, then `process.kill()`, then collects remaining stdout/stderr with `process.communicate()`.
- `_build_command()` currently emits exactly three bind mounts and only forwards the explicit `extra_env` mapping as sorted `-e KEY=VALUE` pairs:

```python
[DOCKER_BIN, "run", "--rm", "--name", container_name,
 "-v", f"{workspace}:/workspace",
 "-v", f"{harness_state}:/harness-state",
 "-v", f"{opencode_logs}:{OPENCODE_LOG_DIR}",
 *self.extra_run_args,
 *env_flags,
 self.image,
 HARNESS_ENTRYPOINT]
```

- `run_state_file` is host-managed only; it is not part of the container mount list.
- This feature extends `run()`/`_build_command()` with a control-directory mount and one new environment variable derived from the listener socket path.

#### `src/forklift/cli.py`

Current definitions/patterns:

```python
logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))
...
container_runner = ContainerRunner(timeout_seconds=timeout_seconds)
container_result = container_runner.run(
    run_paths.workspace,
    run_paths.harness_state,
    run_paths.opencode_logs,
    run_paths.run_dir / "run-state.json",
    self._build_container_env(...),
)
```

Current behavior to preserve while extending:
- `cli.py` configures structlog with `structlog.stdlib.LoggerFactory()` and `structlog.stdlib.BoundLogger`.
- `Container stdout` / `Container stderr` are logged only after `container_runner.run(...)` returns.
- This feature adds new live top-level rebase/conflict logs without removing the existing post-run stdout/stderr logs.

#### `docker/kitchen-sink/harness/run.sh`

Current exported runtime paths/variables:

```bash
WORKSPACE_DIR=${WORKSPACE_DIR:-/workspace}
HARNESS_STATE_DIR=${HARNESS_STATE_DIR:-/harness-state}
CLIENT_LOG=${OPENCODE_CLIENT_LOG:-$HARNESS_STATE_DIR/opencode-client.log}
HARNESS_STATUS_FILE=${HARNESS_STATUS_FILE:-$HARNESS_STATE_DIR/harness-status.txt}
REBASE_CONTINUE_CHECK_FILE=${REBASE_CONTINUE_CHECK_FILE:-$HARNESS_STATE_DIR/rebase-continue-check.sh}
REBASE_SKIPPED_COMMITS_FILE=${REBASE_SKIPPED_COMMITS_FILE:-$HARNESS_STATE_DIR/rebase-skipped-commits.json}
REBASE_CONFLICTING_COMMITS_FILE=${REBASE_CONFLICTING_COMMITS_FILE:-$HARNESS_STATE_DIR/rebase-conflicting-commits.json}
```

Current behavior to preserve while extending:
- The script sources `includes/common.sh`, `includes/rebase.sh`, and the other helpers during startup.
- It creates `harness-state`, truncates `opencode-client.log` and `setup.log`, and writes `harness-status.txt` via `write_harness_status`.
- It always invokes `start_initial_rebase()` before any agent launch.
- If `INITIAL_REBASE_RESULT=completed`, it writes `completed` to `harness-status.txt` and exits before launching the agent.
- After a successful agent run, it writes `completed` again.
- This feature adds a new optional environment variable: `FORKLIFT_REBASE_EVENTS_SOCK=/forklift-control/rebase-events.sock`.

#### `docker/kitchen-sink/harness/includes/common.sh`

Current helper APIs:

```bash
emit_phase_message() { phase="$1"; stream="$2"; message="$3"; ... }
write_harness_status() { status="$1"; phase="${2:-${HARNESS_PHASE:-unknown}}"; message="${3:-}"; ... }
fail_harness() { message="$1"; write_harness_status "failed" ...; ... }
```

Current behavior to preserve while extending:
- `emit_phase_message phase stream message` prints `[phase] message` to stdout/stderr and mirrors it into `opencode-client.log`.
- `write_harness_status status [phase] [message]` writes `status=...`, `phase=...`, and `message=...` lines to `harness-status.txt`.
- Socket-send failures should reuse this existing logging style instead of inventing a second local reporting mechanism.

#### `docker/kitchen-sink/harness/includes/rebase.sh`

Current helper APIs:

```bash
start_initial_rebase() { ... }
record_current_conflicting_commit() { ... }
handle_rebase_continue() { ... }
handle_rebase_skip() { ... }
handle_rebase_abort() { ... }
classify_paused_rebase_command() { ... }
rebase_in_progress() { ... }
capture_status_snapshot() { ... }
```

Current behavior to preserve while extending:
- `record_current_conflicting_commit()` already derives `REBASE_HEAD` with `git rev-parse REBASE_HEAD` and the subject with `git show -s --format=%s REBASE_HEAD`, then appends to `rebase-conflicting-commits.json` if new.
- `handle_rebase_skip()` already derives `REBASE_HEAD`, records the explicit skip in `rebase-skipped-commits.json`, then invokes the real `git rebase --skip`.
- `handle_rebase_continue()` already intercepts `git rebase --continue`, runs the optional continue-check flow, invokes the real continue, and contains the existing implicit auto-skip branch when the continue fails but the workspace status snapshot is empty.
- `handle_rebase_abort()` already gates `git rebase --abort` on non-empty `STUCK.md` content.
- `classify_paused_rebase_command()` already normalizes the allowed paused commands to `continue`, `skip`, or `abort`.
- This feature adds event emission at those existing seams; it does not move paused-rebase orchestration out of `rebase.sh`.

### Python stdlib socket APIs

Host listener and harness sender both use the Python `socket` module with pathname UNIX sockets:

```python
import socket

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
```

Verified semantics to rely on:
- `socket.AF_UNIX` + `socket.SOCK_STREAM` creates a stream-oriented Unix domain socket.
- For filesystem-backed UNIX sockets, the address passed to `bind()` / `connect()` is a Python `str` pathname. Linux abstract-namespace sockets are explicitly out of scope.
- `bind(path)` creates the filesystem socket node at `path`. If a stale socket file already exists, the host MUST remove it before `bind()`.
- `listen(backlog)` puts the bound socket into listening mode.
- `accept()` returns `(conn, addr)` for the next client connection.
- `settimeout(seconds)` converts blocking `accept()` / `recv()` / `connect()` calls into timeout exceptions after the configured interval; use a short timeout in the listener loop so shutdown can observe `threading.Event`.
- `recv(bufsize)` returns `bytes`; `b""` means the peer closed the connection.
- `sendall(data)` sends the entire payload before returning or raises on failure.
- `close()` releases the socket object; for the server socket, the filesystem socket path should be unlinked during teardown after close.
- Exceptions to handle explicitly: `OSError`/`TimeoutError` from socket operations. Sender failures are best-effort and MUST NOT fail the rebase; host listener failures become warnings unless they prevent initial socket bind.

Path-length rule:
- Linux pathname UNIX sockets are limited by `sockaddr_un.sun_path`, which is 108 bytes including the null terminator.
- Therefore the encoded filesystem path MUST be at most 107 bytes.
- Implementation check: `len(os.fsencode(str(socket_path))) <= 107` before `bind()`.

### Python stdlib threading APIs

The host listener uses one background thread plus one stop signal:

```python
import threading

stop_event = threading.Event()
thread = threading.Thread(target=listen_for_events, name="forklift-rebase-events", daemon=True)
thread.start()
...
stop_event.set()
thread.join(timeout=...)
```

Verified semantics to rely on:
- `threading.Event()` provides `set()` and `is_set()` for cross-thread shutdown signaling.
- A short socket timeout plus `stop_event.is_set()` polling is sufficient; no additional synchronization primitive is required.
- The thread may be daemonized for process cleanup robustness, but `ContainerRunner.run()` SHOULD still close the listening socket, set the stop event, and `join()` the thread during normal teardown.

### Python stdlib JSON APIs

Event payloads use newline-delimited UTF-8 JSON objects.

Sender contract:

```python
payload = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
client.sendall(payload)
```

Receiver contract:

```python
event = json.loads(line)
```

Validation rules:
- Parsed payload MUST be a JSON object / Python `dict`.
- Required keys: `v` (int), `event` (str), `step` (int), `total` (int).
- Optional keys: `sha` (non-empty str), `subject` (non-empty str), `files` (list[str]).
- Unknown extra keys MAY be ignored.
- Unsupported `v` values and unknown `event` strings MUST produce a warning and the payload MUST be ignored.

### Docker CLI APIs already in use

Forklift already builds `docker run` commands in `ContainerRunner._build_command()`. This feature reuses the existing command-building style.

Verified semantics to rely on:
- `-v <host-path>:<container-path>` creates a bind mount and is read-write by default.
- Bind mounts obscure pre-existing container contents at the destination path, so `/forklift-control` MUST remain a dedicated empty mount point.
- `-e KEY=VALUE` forwards a single environment variable into the container.
- Docker docs prefer `--mount type=bind,...` for new code, but changing all existing Forklift mounts from `-v` to `--mount` is unnecessary scope. The implementation MAY keep using `-v` for consistency because `RunDirectoryManager` creates the source directory up front.

Required additions to the Docker command:
- a fourth bind mount for the control directory: host `run_dir/control` → container `/forklift-control`
- one new environment variable: `FORKLIFT_REBASE_EVENTS_SOCK=/forklift-control/rebase-events.sock`

### structlog APIs already in use

Forklift already uses structlog’s standard-library integration. The implementation should keep the current pattern:

```python
import structlog
from structlog.stdlib import BoundLogger

logger: BoundLogger = cast(BoundLogger, structlog.get_logger(__name__))
```

Verified semantics to rely on:
- `structlog.get_logger(*args, **initial_values)` returns a lazy proxy that creates the configured bound logger on first use.
- `structlog.stdlib.BoundLogger.info(event, **kw)` processes the event and proxies to `logging.Logger.info`.
- `structlog.stdlib.BoundLogger.warning(event, **kw)` processes the event and proxies to `logging.Logger.warning`.
- `structlog.stdlib.BoundLogger.exception(event, **kw)` defaults `exc_info=True` and proxies to `logging.Logger.exception`.
- No `structlog.configure(...)` changes are required for this feature; it only emits additional log lines through the existing configuration.

Expected logging calls:

```python
logger.info("Rebase 5/31", step=5, total=31, sha="abc123", subject="Rename auth middleware")
logger.warning(
    "Conflict 5/31",
    step=5,
    total=31,
    sha="abc123",
    subject="Rename auth middleware",
    conflict_files=2,
    files="src/auth.py, tests/test_auth.py",
)
```

### Harness sender behavior

The shell helper in `docker/kitchen-sink/harness/includes/rebase.sh` should delegate socket writes to a tiny inline Python program. The sender API contract is:
- read `FORKLIFT_REBASE_EVENTS_SOCK` from the environment; if it is empty or unset, return success without sending;
- create `socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)`;
- `settimeout(1)` before `connect()` and `sendall()`;
- `connect(sock_path)`;
- `sendall(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")`;
- `close()` in all cases;
- catch `OSError`, `TimeoutError`, and JSON-encoding errors locally, log a best-effort note, and return success to the shell caller.

### Host-side event parsing contract

`ContainerRunner` should parse complete newline-delimited frames only. The first implementation should stay intentionally small:
- each accepted connection is read until EOF;
- split buffered bytes on `b"\n"`;
- ignore empty lines;
- decode UTF-8 strictly;
- `json.loads(...)` each line;
- validate the minimal v1 schema;
- log a warning and drop the payload on decode/type/version/event errors;
- log the accepted payload immediately through the existing logger.

No ack/reply path, backpressure protocol, or reconnect handshake exists in v1.

## Risks / Trade-offs

- [Socket permission mismatch between host user and container UID 1000] → Create a dedicated control directory per run, align directory ownership with the existing run artifact ownership flow, and set the bound socket mode explicitly after bind.
- [UNIX socket pathname exceeds Linux `sun_path` limit] → Validate `len(os.fsencode(str(socket_path))) <= 107` before bind and fail early with an actionable error instead of surfacing a low-level bind failure.
- [Dropped or malformed events produce misleadingly sparse progress logs] → Treat the channel as best effort, log host-side parse failures, and keep all existing completion/status artifacts authoritative.
- [Git backend differences expose incomplete ordinal data] → Normalize both `rebase-merge` and `rebase-apply` backends in one helper and test both shapes directly.
- [Log spam from repeated conflict retries] → Emit events only on meaningful transitions and allow duplicate paused-commit emissions to be deduplicated by the harness when the same `REBASE_HEAD` remains active.
- [Future demand for per-clean-commit progress beyond paused transitions] → Document that v1 covers harness-owned transitions only; deeper per-commit streaming would require separate design work around Git progress parsing or explicit stepwise orchestration.
- [Docker Desktop / non-Linux host semantics for pathname sockets on bind mounts may differ from current Linux behavior] → Keep v1 explicitly aligned with the existing Linux host assumption and defer cross-platform host support until it is separately specified and tested.

## Migration Plan

1. Extend `RunPaths` and run-directory preparation to create the control directory alongside `workspace`, `harness-state`, and `opencode-logs`.
2. Update `ContainerRunner` to create the socket listener, mount the control directory, export `FORKLIFT_REBASE_EVENTS_SOCK`, and log parsed events immediately.
3. Update harness startup/rebase helpers to no-op cleanly when the socket env var is absent, then emit v1 events when present.
4. Rebuild `forklift/kitchen-sink:latest` after the harness changes so the image and host code stay in sync.
5. Validate mixed-version safety by proving both “host without sender” and “sender without host socket” degrade to existing behavior.

Rollback strategy:
- Revert the host-side socket mount/listener code and rebuild the image without the sender helper.
- Because the channel is additive and optional, rollback simply removes live progress logs; it does not require any data migration or cleanup beyond deleting stale socket files in active run directories.

## Open Questions

None. The transport choice (bind-mounted Unix domain socket), sender implementation (Python), and requirement to avoid both polling and stdout-as-protocol are locked by discussion.
