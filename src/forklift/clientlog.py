from __future__ import annotations

from pathlib import Path
from typing import TextIO, override

from clypi import Command, Positional, arg

from .clientlog_command import (
    follow_stream,
    is_terminal_run_state,
    render_initial_transcript,
    resolve_run_dir,
)
from .clientlog_models import FollowRenderState, FollowStepState, ParsedEvent
from .clientlog_parser import ClientLogParser
from .clientlog_renderer import TranscriptRenderer
from .run_manager import DEFAULT_RUNS_ROOT
from .run_state import RunStateError, read_run_state, run_state_path


class Clientlog(Command):
    """Render and optionally follow the OpenCode client transcript for a run."""

    run_id: Positional[str]
    follow: bool = arg(False, short="f", help="Continue streaming appended events")

    @override
    async def run(self) -> None:
        run_dir = self._resolve_run_dir(self.run_id)
        client_log_path = run_dir / "harness-state" / "opencode-client.log"
        run_state_file = run_state_path(run_dir)
        _ = self._load_required_run_state(run_state_file)

        if not client_log_path.exists():
            raise SystemExit(
                f"clientlog error: missing transcript file at {client_log_path}."
            )

        parser = ClientLogParser()
        renderer = TranscriptRenderer()
        with client_log_path.open("r", encoding="utf-8") as handle:
            _history_events, follow_state = render_initial_transcript(
                handle,
                parser=parser,
                renderer=renderer,
                follow=self.follow,
            )
            if self.follow and follow_state is not None:
                self._follow_stream(
                    handle,
                    parser,
                    renderer,
                    follow_state,
                    run_state_file,
                )

    def _resolve_run_dir(self, run_id: str) -> Path:
        """Resolve the run directory rooted under `DEFAULT_RUNS_ROOT`."""

        return resolve_run_dir(run_id, runs_root=DEFAULT_RUNS_ROOT)

    def _load_required_run_state(self, state_path: Path) -> dict[str, object]:
        """Load run-state metadata and fail fast when missing/unreadable."""

        if not state_path.exists():
            raise SystemExit(
                f"clientlog error: required run-state metadata is missing at {state_path}."
            )
        try:
            return read_run_state(state_path)
        except RunStateError as exc:
            raise SystemExit(
                f"clientlog error: required run-state metadata at {state_path} is unreadable: {exc}"
            ) from exc

    def _follow_stream(
        self,
        handle: TextIO,
        parser: ClientLogParser,
        renderer: TranscriptRenderer,
        follow_state: FollowRenderState,
        run_state_file: Path,
    ) -> None:
        """Follow transcript updates until interrupted or run-state becomes terminal."""

        follow_stream(
            handle,
            parser=parser,
            renderer=renderer,
            follow_state=follow_state,
            run_state_file=run_state_file,
            load_required_run_state_fn=self._load_required_run_state,
        )

    def _is_terminal_run_state(self, run_state_file: Path) -> bool:
        """Return whether run-state has transitioned to a terminal lifecycle status."""

        return is_terminal_run_state(
            run_state_file,
            load_required_run_state_fn=self._load_required_run_state,
        )


__all__ = [
    "ClientLogParser",
    "Clientlog",
    "FollowRenderState",
    "FollowStepState",
    "ParsedEvent",
    "TranscriptRenderer",
]
