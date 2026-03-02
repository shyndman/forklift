from __future__ import annotations

import signal
import sys
import time
from pathlib import Path
from typing import Callable, TextIO

from .clientlog_models import FollowRenderState, ParsedEvent, as_str
from .clientlog_parser import ClientLogParser
from .clientlog_renderer import TranscriptRenderer, paint
from .run_state import TERMINAL_RUN_STATUSES


def resolve_run_dir(run_id: str, *, runs_root: Path) -> Path:
    """Resolve and validate the run directory backing a `clientlog` invocation."""

    run_dir = (runs_root / run_id).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"clientlog error: run directory '{run_id}' not found at {run_dir}.")
    return run_dir


def render_initial_transcript(
    handle: TextIO,
    *,
    parser: ClientLogParser,
    renderer: TranscriptRenderer,
    follow: bool,
) -> tuple[list[ParsedEvent], FollowRenderState | None]:
    """Render the initial transcript snapshot and optionally initialize follow-state."""

    initial_chunk = handle.read()
    history_events = parser.feed(initial_chunk)
    if not follow:
        history_events.extend(parser.flush())

    snapshot = renderer.render_snapshot(history_events)
    if snapshot:
        print(snapshot, end="")

    if not follow:
        return history_events, None
    return history_events, renderer.initialize_follow_state(history_events)


def follow_stream(
    handle: TextIO,
    *,
    parser: ClientLogParser,
    renderer: TranscriptRenderer,
    follow_state: FollowRenderState,
    run_state_file: Path,
    load_required_run_state_fn: Callable[[Path], dict[str, object]],
) -> None:
    """Tail transcript output until interrupted or terminal run-state is observed."""

    interrupted = False
    idle_polls_after_terminal = 0

    def signal_handler(_signum: int, _frame: object | None) -> None:
        nonlocal interrupted
        interrupted = True

    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous_handlers:
        _ = signal.signal(sig, signal_handler)

    try:
        while not interrupted:
            chunk = handle.read()
            if chunk:
                new_events = parser.feed(chunk)
                rendered = renderer.render_follow_events(new_events, follow_state)
                if rendered:
                    print(rendered, end="", flush=True)
                idle_polls_after_terminal = 0
                continue

            if is_terminal_run_state(
                run_state_file,
                load_required_run_state_fn=load_required_run_state_fn,
            ):
                idle_polls_after_terminal += 1
                if idle_polls_after_terminal >= 3:
                    print(
                        paint(
                            "follow mode: run reached terminal status; exiting.",
                            "subtle",
                        ),
                        file=sys.stderr,
                    )
                    return
            else:
                idle_polls_after_terminal = 0

            time.sleep(0.25)
    finally:
        for sig, previous in previous_handlers.items():
            _ = signal.signal(sig, previous)

    print(paint("Interrupted, exiting follow mode.", "subtle"), file=sys.stderr)
    raise SystemExit(130)


def is_terminal_run_state(
    run_state_file: Path,
    *,
    load_required_run_state_fn: Callable[[Path], dict[str, object]],
) -> bool:
    """Return whether run-state has reached a terminal status."""

    state = load_required_run_state_fn(run_state_file)
    status = as_str(state.get("status"))
    return bool(status and status in TERMINAL_RUN_STATUSES)
