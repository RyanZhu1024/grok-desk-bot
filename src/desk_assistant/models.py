"""Shared values for the webhook, the listener, and the status screen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class Source(str, Enum):
    VOICE = "voice"
    BUTTON = "button"
    TOUCH = "touch"
    HARO = "haro"


VALID_SOURCES = {item.value for item in Source}


class WebhookError(str, Enum):
    DISABLED = "disabled"
    UNAUTHORIZED = "unauthorized"
    REJECTED = "rejected"
    HTTP = "http"
    NETWORK = "network"
    CONFIG = "config"


@dataclass(frozen=True)
class WebhookResult:
    ok: bool
    run_uuid: str | None = None
    error: WebhookError | None = None
    detail: str = ""

    @property
    def user_message(self) -> str:
        if self.ok:
            return "Request accepted."
        if self.error is WebhookError.DISABLED:
            return "The assistant routine is turned off. Enable it, then try again."
        if self.error is WebhookError.UNAUTHORIZED:
            return "The webhook key was rejected. Check the key in your config."
        if self.error is WebhookError.NETWORK:
            return "Couldn't reach the assistant. Check the network and the webhook URL."
        if self.error is WebhookError.CONFIG:
            return self.detail or "Webhook URL and key are not set."
        if self.detail:
            return self.detail
        return "The assistant did not accept the request."


@dataclass(frozen=True)
class NtfyMessage:
    """A message event from an ntfy JSON or SSE stream."""

    id: str
    topic: str
    message: str
    title: str = ""
    time: float = 0.0

    @property
    def text(self) -> str:
        body = self.message.strip()
        if body:
            return body
        return self.title.strip()


@dataclass(frozen=True)
class ButtonView:
    id: str
    label: str
    key: str


@dataclass(frozen=True)
class Snapshot:
    """A copy of what the screen should show. Safe to draw without further locking."""

    state: State
    substatus: str
    last_request: str
    last_source: str
    last_reply: str
    notification: str
    error: str
    notice: str
    buttons: tuple[ButtonView, ...]
    recording_seconds: float
    ntfy_connected: bool
    test_mode: bool
    test_note: str
    topic: str
    run_uuid: str
    active_control: str
    footer: str
