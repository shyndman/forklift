from __future__ import annotations


class ForkliftError(Exception):
    """Base class for Forklift run-lifecycle failures. Carries no exit code."""


class SetupError(ForkliftError):
    """Pre-container setup failed: operator identity, remotes, fetch, target resolution, env load."""


class HarnessIncompleteError(ForkliftError):
    """Container exited cleanly but the harness did not report successful completion."""


class PublishError(ForkliftError):
    """Authorship rewrite or local publication failed."""


class RebaseStuckError(ForkliftError):
    """The harness rebase report recorded a stuck outcome."""


class UpstreamNotMergedError(ForkliftError):
    """Selected upstream target is not an ancestor of the target branch after the run."""


class ContainerTimeoutError(ForkliftError):
    """The container exceeded its timeout budget."""


class ContainerExitError(ForkliftError):
    """The container exited with a non-zero status code."""

    def __init__(self, container_exit_code: int) -> None:
        super().__init__(f"container exited with code {container_exit_code}")
        self.container_exit_code: int = container_exit_code
