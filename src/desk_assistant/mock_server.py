"""Local stand-in for the assistant webhook and the ntfy server.

Test mode and the unit tests both talk to this. It accepts the same POST the
real routine accepts, then publishes a plain-text reply on the request's
`reply_topic` using the ntfy JSON stream format.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from typing import Callable
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_DROP = object()
_STOP = object()

ReplyFn = Callable[[str, str], str]


def canned_reply(text: str, source: str) -> str:
    """Short replies so the laptop demo has something believable to show and speak."""
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()
    if "briefing" in lowered:
        return (
            "Good morning. You have two meetings today: design review at 2:00, "
            "and a short sync at 4:30. No overnight alerts."
        )
    if "calendar" in lowered or "what's next" in lowered or "whats next" in lowered:
        return "Next up is design review at 2:00 PM. After that, a 30 minute sync at 4:30."
    if "inbox" in lowered or "urgent" in lowered:
        return "One urgent note: the hardware vendor confirmed the screens ship Thursday."
    return f"Got it from {source}. You said: {cleaned}"


class MockAssistantServer:
    def __init__(
        self,
        key: str = "test-key",
        *,
        reply_delay: float = 0.4,
        reply_fn: ReplyFn | None = None,
    ) -> None:
        self.key = key
        self.reply_delay = reply_delay
        self.reply_fn = reply_fn or canned_reply
        self.status_override: int | None = None
        self.silent = False
        self.requests: list[dict] = []
        self.base_url = ""
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._subscribers: dict[str, list[Queue]] = defaultdict(list)
        self._history: dict[str, list[dict]] = defaultdict(list)
        self.accepted_streams = 0

    def start(self) -> str:
        handler = _Handler
        server = _Server(("127.0.0.1", 0), handler)
        server.mock = self
        self._httpd = server
        self._thread = threading.Thread(target=server.serve_forever, name="mock-assistant", daemon=True)
        self._thread.start()
        host, port = server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        logger.info("Mock assistant listening on %s", self.base_url)
        return self.base_url

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            queues = [q for group in self._subscribers.values() for q in group]
        for q in queues:
            q.put(_STOP)
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def __enter__(self) -> "MockAssistantServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._subscribers.get(topic, []))

    def wait_for_subscribers(self, topic: str, count: int, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.subscriber_count(topic) == count:
                return
            time.sleep(0.02)
        raise TimeoutError(f"timed out waiting for {count} subscriber(s) on {topic}")

    def publish(self, topic: str, message: str, title: str = "") -> dict:
        event = {
            "id": uuid.uuid4().hex[:12],
            "time": int(time.time()),
            "event": "message",
            "topic": topic,
            "message": message,
        }
        if title:
            event["title"] = title
        with self._lock:
            history = self._history[topic]
            history.append(event)
            del history[:-50]
            queues = list(self._subscribers.get(topic, []))
        for q in queues:
            q.put(event)
        return event

    def inject(self, topic: str, event: dict) -> None:
        """Push a raw event to live subscribers without storing it in history."""
        with self._lock:
            queues = list(self._subscribers.get(topic, []))
        for q in queues:
            q.put(event)

    def drop_subscribers(self) -> None:
        with self._lock:
            queues = [q for group in self._subscribers.values() for q in group]
        for q in queues:
            q.put(_DROP)

    def subscribe(self, topic: str, since: str) -> tuple[Queue, list[dict]]:
        q: Queue = Queue()
        with self._lock:
            self.accepted_streams += 1
            replay = _history_after(self._history.get(topic, []), since)
            self._subscribers[topic].append(q)
        return q, replay

    def unsubscribe(self, topic: str, q: Queue) -> None:
        with self._lock:
            group = self._subscribers.get(topic, [])
            try:
                group.remove(q)
            except ValueError:
                pass

    def handle_webhook(self, headers, raw: bytes) -> tuple[int, dict]:
        auth = headers.get("Authorization", "")
        if auth != f"Bearer {self.key}" or self.status_override == 401:
            return 401, {"error": "unauthorized"}
        if self.status_override == 400:
            return 400, {"error": "routine disabled"}
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return 400, {"error": "invalid json"}
        if not isinstance(data, dict):
            return 400, {"error": "invalid json"}
        self.requests.append(data)
        run_uuid = str(uuid.uuid4())
        if not self.silent:
            threading.Thread(
                target=self._reply_later,
                args=(dict(data),),
                name="mock-reply",
                daemon=True,
            ).start()
        return 200, {"success": True, "runUuid": run_uuid}

    def _reply_later(self, data: dict) -> None:
        delay = self.reply_delay
        if delay > 0:
            if self._stop.wait(delay):
                return
        if self._stop.is_set() or self.silent:
            return
        topic = str(data.get("reply_topic") or "")
        if not topic:
            return
        text = str(data.get("text") or "")
        source = str(data.get("source") or "")
        self.publish(topic, self.reply_fn(text, source))


def _history_after(history: list[dict], since: str) -> list[dict]:
    if not since or since == "now":
        return []
    for index, event in enumerate(history):
        if event.get("id") == since:
            return list(history[index + 1 :])
    return []


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    mock: MockAssistantServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("mock http " + fmt, *args)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        app: MockAssistantServer = self.server.mock  # type: ignore[attr-defined]
        if path == "/webhook":
            status, body = app.handle_webhook(self.headers, raw)
            payload = json.dumps(body).encode("utf-8")
            self._send(status, payload, "application/json")
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 1:
            message, title = _publish_body(raw, self.headers.get("Content-Type", ""))
            app.publish(parts[0], message, title)
            self._send(200, json.dumps({"success": True}).encode("utf-8"), "application/json")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2 or parts[1] != "json":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        topic = parts[0]
        since = parse_qs(parsed.query).get("since", ["now"])[0]
        app: MockAssistantServer = self.server.mock  # type: ignore[attr-defined]
        q, replay = app.subscribe(topic, since)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self._write_event(
                {
                    "id": uuid.uuid4().hex[:12],
                    "time": int(time.time()),
                    "event": "open",
                    "topic": topic,
                }
            )
            for event in replay:
                self._write_event(event)
            while not app._stop.is_set():
                try:
                    item = q.get(timeout=0.5)
                except Empty:
                    continue
                if item is _DROP or item is _STOP:
                    break
                self._write_event(item)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            app.unsubscribe(topic, q)

    def _write_event(self, event: dict) -> None:
        self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _publish_body(raw: bytes, content_type: str) -> tuple[str, str]:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type:
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            return text.strip(), ""
        if isinstance(data, dict):
            return str(data.get("message") or ""), str(data.get("title") or "")
    return text.strip(), ""
