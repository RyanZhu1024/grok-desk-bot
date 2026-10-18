"""Read a PiSugar 3 battery level, if the power manager is running.

The PiSugar server answers `get battery` on its unix socket and on TCP port
8423. A missing server is not a failure: Haro simply skips the low-battery
warning.
"""

from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger(__name__)


def parse_battery_level(text: str | None) -> float | None:
    """Pull a percent out of a PiSugar reply such as ``battery: 42``."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("battery:"):
            continue
        raw = stripped.split(":", 1)[1].strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        if value < 0 or value > 100:
            return None
        return value
    return None


def _query(connect, timeout: float) -> str | None:
    try:
        with socket.socket(*connect[0]) as sock:
            sock.settimeout(timeout)
            sock.connect(connect[1])
            sock.sendall(b"get battery\n")
            chunks: list[bytes] = []
            while True:
                try:
                    data = sock.recv(256)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data or len(b"".join(chunks)) > 512:
                    break
        return b"".join(chunks).decode("utf-8", errors="replace")
    except OSError as exc:
        logger.debug("PiSugar query failed: %s", exc)
        return None


def read_pisugar_battery(
    socket_path: str = "/tmp/pisugar-server.sock",
    host: str = "127.0.0.1",
    port: int = 8423,
    *,
    timeout: float = 1.0,
) -> float | None:
    text = None
    if socket_path and os.path.exists(socket_path):
        text = _query(((socket.AF_UNIX, socket.SOCK_STREAM), socket_path), timeout)
    if parse_battery_level(text) is None:
        text = _query(((socket.AF_INET, socket.SOCK_STREAM), (host, port)), timeout)
    return parse_battery_level(text)


class PiSugarBattery:
    def __init__(self, socket_path: str, host: str, port: int) -> None:
        self.socket_path = socket_path
        self.host = host
        self.port = port

    def read(self) -> float | None:
        return read_pisugar_battery(self.socket_path, self.host, self.port)
