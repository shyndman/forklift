"""structlog configuration for the in-container harness.

Configures structlog once so every record is serialized to one NDJSON line and
sent to the host over the log socket (``FORKLIFT_LOG_SOCK``), where the
orchestrator renders it natively under the run correlator. The orchestrator
calls :func:`configure_logging` first thing in ``main()``.
"""

from __future__ import annotations

import os
import socket
import sys
from typing import final

import structlog

# Wall-clock bound on connecting/sending one record to the host log socket.
LOG_SOCKET_TIMEOUT_SECONDS = 1


@final
class SocketLogger:
    """structlog logger writing each rendered NDJSON record to the log socket.

    Models :class:`structlog.PrintLogger`: every level method funnels to
    :meth:`_emit`, which opens the unix socket, sends one newline-terminated
    line, and closes it (connect-send-close per record, matching the harness's
    other socket writers). When the socket is unset or unreachable the line
    falls back to stderr so records are never silently dropped.
    """

    def __init__(self, sock_path: str | None) -> None:
        self._sock_path: str | None = sock_path

    def _emit(self, message: str) -> None:
        line = message + "\n"
        if self._sock_path:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(LOG_SOCKET_TIMEOUT_SECONDS)
            try:
                client.connect(self._sock_path)
                _ = client.sendall(line.encode("utf-8"))
                return
            except OSError:
                pass
            finally:
                client.close()
        _ = sys.stderr.write(line)
        _ = sys.stderr.flush()

    log = debug = info = warning = warn = error = err = critical = fatal = exception = (
        msg
    ) = _emit


class SocketLoggerFactory:
    """structlog ``logger_factory`` producing a shared :class:`SocketLogger`."""

    def __init__(self, sock_path: str | None) -> None:
        self._logger: SocketLogger = SocketLogger(sock_path)

    def __call__(self, *args: object) -> SocketLogger:
        return self._logger


def configure_logging() -> None:
    """Emit every structlog record as one NDJSON line to the host log socket."""

    sock_path = os.environ.get("FORKLIFT_LOG_SOCK") or None
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(default=str),
        ],
        logger_factory=SocketLoggerFactory(sock_path),
        cache_logger_on_first_use=True,
    )
