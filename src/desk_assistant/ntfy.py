"""Subscribe to an ntfy topic and yield message events.

The stream is newline-delimited JSON (`/topic/json`). SSE `data:` lines are
accepted too, so either ntfy subscription style works. Keepalives, open
events, and blank lines are ignored.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass

import httpx

from desk_assistant.models import NtfyMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NtfyEvent:
    event: str
    id: str
    topic: str
    message: str
    title: str
    time: float


def parse_ntfy_line(line: str) -> NtfyEvent | None:
    """Parse one JSON-stream or SSE line. Returns None when the line is not an event."""
    text = (line or "").strip()
    if not text or text.startswith(":"):
        return None
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text or text == "[DONE]":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "event" not in data:
        return None
    try:
        timestamp = float(data.get("time") or 0)
    except (TypeError, ValueError):
        timestamp = 0.0
    return NtfyEvent(
        event=str(data.get("event") or ""),
        id=str(data.get("id") or ""),
        topic=str(data.get("topic") or ""),
        message=str(data.get("message") or "") if data.get("message") is not None else "",
        title=str(data.get("title") or "") if data.get("title") is not None else "",
        time=timestamp,
    )


class NtfyListener:
    """Background subscriber. `on_message` is called from the listener thread."""

    def __init__(
        self,
        server: str,
        topic: str,
        on_message,
        *,
        read_timeout: float = 50.0,
    ) -> None:
        self.server = server.rstrip("/")
        self.topic = topic.strip()
        self.on_message = on_message
        self.read_timeout = read_timeout
        self.connected = False
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ntfy-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.debug("closing ntfy client", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        since = "now"
        backoff = 0.4
        while not self._stop.is_set():
            url = f"{self.server}/{self.topic}/json"
            client = httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=self.read_timeout, write=10.0, pool=10.0)
            )
            with self._lock:
                self._client = client
            try:
                with client.stream(
                    "GET",
                    url,
                    params={"since": since},
                    headers={"Accept": "application/x-ndjson"},
                ) as response:
                    response.raise_for_status()
                    self.connected = True
                    self.last_error = ""
                    backoff = 0.4
                    logger.info("Listening on %s/%s", self.server, self.topic)
                    for line in response.iter_lines():
                        if self._stop.is_set():
                            break
                        event = parse_ntfy_line(line)
                        if event is None:
                            continue
                        if event.event == "message" and event.id:
                            # Resume after this message. Keepalives are not a cursor:
                            # the server may not keep them in history.
                            since = event.id
                        if event.event != "message":
                            continue
                        message = NtfyMessage(
                            id=event.id,
                            topic=event.topic or self.topic,
                            message=event.message,
                            title=event.title,
                            time=event.time,
                        )
                        if not message.text:
                            continue
                        try:
                            self.on_message(message)
                        except Exception:
                            logger.exception("ntfy callback failed")
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.connected = False
                self.last_error = exc.__class__.__name__
                logger.warning("ntfy stream interrupted (%s); retrying", exc.__class__.__name__)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 15.0)
            finally:
                self.connected = False
                with self._lock:
                    if self._client is client:
                        self._client = None
                try:
                    client.close()
                except Exception:
                    logger.debug("ntfy client close failed", exc_info=True)
