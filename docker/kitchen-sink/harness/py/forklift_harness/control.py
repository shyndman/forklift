"""Intra-container control-socket protocol shared by the mediator and orchestrator.

The orchestrator binds a UNIX stream socket at `FORKLIFT_REBASE_CONTROL_SOCK`
(default `/run/forklift/rebase-control.sock`). After the git mediator performs a
real rebase transition it connects, sends a single newline-delimited JSON
`TransitionReport`, and blocks waiting for a `Directive` reply. This socket never
leaves the container and is distinct from the host-owned events socket.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import cast

CONTROL_PROTOCOL_VERSION = 1
PROCEED = "proceed"


@dataclass(frozen=True)
class TransitionReport:
    """A completed rebase transition reported by the mediator to the orchestrator."""

    action: str  # continue | skip | abort
    sha: str
    subject: str
    files: tuple[str, ...]
    note: str
    advanced: bool
    completed: bool

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "v": CONTROL_PROTOCOL_VERSION,
            "action": self.action,
            "sha": self.sha,
            "subject": self.subject,
            "files": list(self.files),
            "note": self.note,
            "advanced": self.advanced,
            "completed": self.completed,
        }
        return json.dumps(payload)

    @classmethod
    def from_json(cls, raw: str) -> TransitionReport:
        parsed = cast(object, json.loads(raw))
        if not isinstance(parsed, dict):
            raise ValueError("control report payload must be a JSON object")
        data = cast(dict[str, object], parsed)
        files_raw = data.get("files", [])
        files = (
            tuple(
                str(item)
                for item in cast(list[object], files_raw)
                if isinstance(item, str)
            )
            if isinstance(files_raw, list)
            else ()
        )
        return cls(
            action=str(data.get("action", "")),
            sha=str(data.get("sha", "")),
            subject=str(data.get("subject", "")),
            files=files,
            note=str(data.get("note", "")),
            advanced=bool(data.get("advanced", False)),
            completed=bool(data.get("completed", False)),
        )


@dataclass(frozen=True)
class Directive:
    """An orchestrator reply telling the mediator how to return to the agent."""

    directive: str  # proceed

    def to_json(self) -> str:
        return json.dumps({"v": CONTROL_PROTOCOL_VERSION, "directive": self.directive})

    @classmethod
    def from_json(cls, raw: str) -> Directive:
        parsed = cast(object, json.loads(raw))
        if not isinstance(parsed, dict):
            raise ValueError("control directive payload must be a JSON object")
        data = cast(dict[str, object], parsed)
        return cls(directive=str(data.get("directive", "")))


def _recv_line(conn: socket.socket, *, timeout: float | None) -> str | None:
    """Read a single newline-delimited line from a connected stream socket."""

    conn.settimeout(timeout)
    chunks: list[bytes] = []
    while True:
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            return None
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    text = b"".join(chunks).decode("utf-8")
    line, _, _ = text.partition("\n")
    if not line.strip():
        return None
    return line


def send_report_and_wait(
    sock_path: str,
    report: TransitionReport,
    *,
    timeout: float,
) -> Directive | None:
    """Send a transition report and block for the orchestrator directive.

    Returns the parsed directive, or None when the orchestrator never replies
    within `timeout` (fail-closed) or the connection is severed by a kill.
    """

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        client.connect(sock_path)
        _ = client.sendall((report.to_json() + "\n").encode("utf-8"))
        line = _recv_line(client, timeout=timeout)
        if line is None:
            return None
        return Directive.from_json(line)
    except OSError:
        return None
    finally:
        client.close()


class ControlListener:
    """Orchestrator-side listener that accepts one mediator transition at a time."""

    def __init__(self, sock_path: str) -> None:
        self.sock_path: str = sock_path
        self._listener: socket.socket | None = None

    def __enter__(self) -> ControlListener:
        # Remove any stale socket from a prior run before binding.
        try:
            os.makedirs(os.path.dirname(self.sock_path), exist_ok=True)
        except OSError:
            pass
        self._unlink_socket()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.sock_path)
        listener.listen()
        listener.settimeout(0.25)
        self._listener = listener
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self._unlink_socket()

    def _unlink_socket(self) -> None:
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

    def accept(self) -> socket.socket | None:
        """Accept the next mediator connection, or None on the poll timeout."""

        if self._listener is None:
            raise RuntimeError("ControlListener used outside its context manager")
        try:
            accepted = cast(tuple[socket.socket, object], self._listener.accept())
        except socket.timeout:
            return None
        return accepted[0]

    def recv_report(
        self, conn: socket.socket, *, timeout: float
    ) -> TransitionReport | None:
        """Read a transition report from an accepted connection."""

        line = _recv_line(conn, timeout=timeout)
        if line is None:
            return None
        return TransitionReport.from_json(line)

    def reply(self, conn: socket.socket, directive: Directive) -> None:
        """Send a directive back to a waiting mediator."""

        try:
            _ = conn.sendall((directive.to_json() + "\n").encode("utf-8"))
        except OSError:
            pass
