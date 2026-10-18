"""State machine for one desk.

Button presses, push-to-talk, and typed text all end up as a webhook POST.
The screen stays on "thinking" until the next ntfy message arrives, or until
the reply timeout. Later messages are notifications, not a second reply.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from desk_assistant.audio import Recorder, SpeechToText, TextToSpeech
from desk_assistant.config import ButtonSettings
from desk_assistant.models import VALID_SOURCES, ButtonView, NtfyMessage, Snapshot, State, WebhookResult
from desk_assistant.ntfy import NtfyListener
from desk_assistant.webhook import WebhookClient

logger = logging.getLogger(__name__)


@dataclass
class _Cmd:
    kind: str
    button_id: str = ""
    source: str = ""
    text: str = ""


@dataclass
class _Inbound:
    message: NtfyMessage


@dataclass
class _WebhookDone:
    generation: int
    result: WebhookResult


@dataclass
class _Transcript:
    generation: int
    text: str
    error: str = ""


@dataclass
class _SpeakDone:
    generation: int
    error: str = ""


class Assistant:
    def __init__(
        self,
        *,
        webhook: WebhookClient,
        listener: NtfyListener,
        stt: SpeechToText,
        tts: TextToSpeech,
        recorder: Recorder,
        buttons: list[ButtonSettings],
        reply_timeout: float,
        speak_notifications: bool = False,
        min_record_seconds: float = 0.25,
        max_record_seconds: float = 30.0,
        synchronous: bool = False,
        test_mode: bool = False,
        test_note: str = "",
        topic: str = "",
        footer: str = "",
    ) -> None:
        self.webhook = webhook
        self.listener = listener
        self.stt = stt
        self.tts = tts
        self.recorder = recorder
        self.buttons = list(buttons)
        self.reply_timeout = reply_timeout
        self.speak_notifications = speak_notifications
        self.min_record_seconds = min_record_seconds
        self.max_record_seconds = max_record_seconds
        self.synchronous = synchronous
        self.test_mode = test_mode
        self.test_note = test_note
        self.topic = topic
        self.footer = footer

        self.state = State.IDLE
        self.substatus = "Ready"
        self.last_request = ""
        self.last_source = ""
        self.last_reply = ""
        self.notification = ""
        self.error = ""
        self.notice = ""
        self.notice_at = 0.0
        self.run_uuid = ""
        self.active_control = ""

        self.request_log: list[tuple[str, str]] = []
        self.reply_log: list[str] = []
        self.notification_log: list[str] = []

        self._events: queue.Queue = queue.Queue()
        self._commands: queue.Queue = queue.Queue()
        self._generation = 0
        self._speak_generation = 0
        self._pending_reply = False
        self._accept_late_reply = False
        self._reply_deadline: float | None = None
        self._listen_started = 0.0
    def start(self) -> None:
        self.listener.on_message = self.enqueue_ntfy
        self.listener.start()

    def shutdown(self) -> None:
        self.listener.stop()
        try:
            self.tts.stop()
        except Exception:
            logger.debug("tts stop failed", exc_info=True)
        if self.state is State.LISTENING:
            try:
                self.recorder.stop()
            except Exception:
                logger.debug("recorder stop failed", exc_info=True)
        self.webhook.close()

    def enqueue_ntfy(self, message: NtfyMessage) -> None:
        self._events.put(_Inbound(message))

    def enqueue_button(self, button_id: str, source: str = "button") -> None:
        self._commands.put(_Cmd("button", button_id=button_id, source=source))

    def enqueue_ptt_down(self) -> None:
        self._commands.put(_Cmd("ptt_down"))

    def enqueue_ptt_up(self) -> None:
        self._commands.put(_Cmd("ptt_up"))

    def enqueue_text(self, text: str, source: str = "touch") -> None:
        self._commands.put(_Cmd("text", source=source, text=text))

    def press_button(self, button_id: str, source: str = "button") -> None:
        """Main-thread entry used by the screen, the keyboard, and tests."""
        if source not in VALID_SOURCES:
            raise ValueError(source)
        if self.state in {State.LISTENING, State.TRANSCRIBING, State.THINKING}:
            self._set_notice("Still working on the last request.")
            return
        button = self._button(button_id)
        if button is None:
            self._enter_error(f"Unknown button {button_id}.")
            return
        if self.state is State.SPEAKING:
            self._interrupt_speech()
        self.active_control = button.id
        self.submit(button.text, source)

    def submit_text(self, text: str, source: str = "touch") -> None:
        if self.state in {State.LISTENING, State.TRANSCRIBING, State.THINKING}:
            self._set_notice("Still working on the last request.")
            return
        if self.state is State.SPEAKING:
            self._interrupt_speech()
        self.active_control = ""
        self.submit(text, source)

    def ptt_down(self) -> None:
        if self.state in {State.LISTENING, State.TRANSCRIBING, State.THINKING}:
            return
        if self.state is State.SPEAKING:
            self._interrupt_speech()
        self._generation += 1
        self._pending_reply = False
        self._accept_late_reply = False
        self._reply_deadline = None
        try:
            self.recorder.start()
        except Exception as exc:
            logger.warning("Microphone failed to start: %s", exc)
            self._enter_error(f"Microphone problem. {exc}")
            return
        self.state = State.LISTENING
        self.error = ""
        self.notice = ""
        self.active_control = "ptt"
        self._listen_started = time.monotonic()
        self.substatus = "Listening"

    def ptt_up(self) -> None:
        if self.state is not State.LISTENING:
            return
        elapsed = time.monotonic() - self._listen_started
        try:
            wav = self.recorder.stop()
        except Exception as exc:
            logger.warning("Microphone failed to stop: %s", exc)
            self._enter_error(f"Microphone problem. {exc}")
            return
        if elapsed < self.min_record_seconds:
            self.state = State.IDLE
            self.substatus = "Hold the talk button a little longer."
            self.active_control = ""
            return
        self.state = State.TRANSCRIBING
        self.substatus = "Transcribing"
        generation = self._generation
        self._run(self._transcribe, generation, wav)

    def submit(self, text: str, source: str) -> None:
        if source not in VALID_SOURCES:
            raise ValueError(source)
        cleaned = " ".join((text or "").split())
        if not cleaned:
            self._enter_error("Nothing to send.")
            return
        self._generation += 1
        generation = self._generation
        self.last_request = cleaned
        self.last_source = source
        self.last_reply = ""
        self.error = ""
        self.notice = ""
        self.run_uuid = ""
        self._pending_reply = True
        self._accept_late_reply = False
        self._reply_deadline = time.monotonic() + self.reply_timeout
        self.state = State.THINKING
        self.substatus = "Sending"
        self.request_log.append((source, cleaned))
        self._run(self._post, generation, cleaned, source)

    def tick(self) -> None:
        """Drain input and network events. Call this from the UI thread."""
        for _ in range(12):
            progressed = self._drain()
            before = (self.state, self.notice, self._pending_reply)
            self._check_timers()
            if not progressed and (self.state, self.notice, self._pending_reply) == before:
                break

    def snapshot(self) -> Snapshot:
        recording = 0.0
        if self.state is State.LISTENING:
            recording = max(0.0, time.monotonic() - self._listen_started)
        substatus = self.substatus
        if self.state is State.LISTENING:
            shown = f"{recording:.1f}s" if recording < 10 else f"{recording:.0f}s"
            substatus = f"Release to send · {shown}"
        return Snapshot(
            state=self.state,
            substatus=substatus,
            last_request=self.last_request,
            last_source=self.last_source,
            last_reply=self.last_reply,
            notification=self.notification,
            error=self.error,
            notice=self.notice,
            buttons=tuple(ButtonView(b.id, b.label, b.key) for b in self.buttons),
            recording_seconds=recording,
            ntfy_connected=bool(self.listener.connected),
            test_mode=self.test_mode,
            test_note=self.test_note,
            topic=self.topic,
            run_uuid=self.run_uuid,
            active_control=self.active_control,
            footer=self.footer,
        )

    def _drain(self) -> bool:
        progressed = False
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            progressed = True
            self._apply_command(command)
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            progressed = True
            self._apply_event(event)
        return progressed

    def _apply_command(self, command: _Cmd) -> None:
        if command.kind == "button":
            self.press_button(command.button_id, command.source or "button")
        elif command.kind == "ptt_down":
            self.ptt_down()
        elif command.kind == "ptt_up":
            self.ptt_up()
        elif command.kind == "text":
            self.submit_text(command.text, command.source or "touch")

    def _apply_event(self, event: object) -> None:
        if isinstance(event, _WebhookDone):
            self._on_webhook(event)
        elif isinstance(event, _Inbound):
            self._on_inbound(event.message)
        elif isinstance(event, _Transcript):
            self._on_transcript(event)
        elif isinstance(event, _SpeakDone):
            self._on_speak_done(event)

    def _check_timers(self) -> None:
        now = time.monotonic()
        if self.notice and now - self.notice_at > 3:
            self.notice = ""
        if self.state is State.LISTENING and now - self._listen_started >= self.max_record_seconds:
            self.ptt_up()
        if (
            self.state is State.THINKING
            and self._pending_reply
            and self._reply_deadline is not None
            and now >= self._reply_deadline
        ):
            self._pending_reply = False
            self._reply_deadline = None
            self._accept_late_reply = True
            self._enter_error(self._timeout_message())

    def _on_webhook(self, event: _WebhookDone) -> None:
        if event.generation != self._generation:
            return
        result = event.result
        if not result.ok:
            self._pending_reply = False
            self._reply_deadline = None
            self._accept_late_reply = False
            if self.state is State.SPEAKING and self.last_reply:
                return
            self._enter_error(result.user_message)
            return
        self.run_uuid = result.run_uuid or ""
        if self.state is State.THINKING:
            short = self.run_uuid[:8]
            self.substatus = "Waiting for a reply" + (f" · {short}" if short else "")

    def _on_inbound(self, message: NtfyMessage) -> None:
        text = message.text
        if not text:
            return
        waiting = self._pending_reply and self.state is State.THINKING
        late = self._accept_late_reply and self.state is State.ERROR
        if waiting or late:
            self._pending_reply = False
            self._accept_late_reply = False
            self._reply_deadline = None
            self.last_reply = text
            self.reply_log.append(text)
            self.error = ""
            logger.info("Reply arrived (%d chars)", len(text))
            self._start_speech(text)
            return
        logger.info("Notification arrived (%d chars)", len(text))
        self.notification = text
        self.notification_log.append(text)
        if self.speak_notifications and self.state is State.IDLE:
            self._start_speech(text)

    def _on_transcript(self, event: _Transcript) -> None:
        if event.generation != self._generation:
            return
        if self.state is not State.TRANSCRIBING:
            return
        if event.error:
            self._enter_error(event.error)
            return
        if not event.text.strip():
            self._enter_error("Didn't catch that. Try again.")
            return
        self.active_control = "ptt"
        self.submit(event.text, "voice")

    def _on_speak_done(self, event: _SpeakDone) -> None:
        if event.generation != self._speak_generation:
            return
        if event.error:
            self._enter_error(f"Couldn't speak the reply. {event.error}")
            return
        if self.state is State.SPEAKING:
            self.state = State.IDLE
            self.substatus = "Ready"
            self.active_control = ""

    def _post(self, generation: int, text: str, source: str) -> None:
        result = self.webhook.send(text, source, self.topic)
        self._events.put(_WebhookDone(generation, result))

    def _transcribe(self, generation: int, wav: Path) -> None:
        try:
            text = self.stt.transcribe(wav)
            self._events.put(_Transcript(generation, text))
        except Exception as exc:
            logger.warning("Transcription failed: %s", exc)
            self._events.put(_Transcript(generation, "", str(exc)))
        finally:
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass

    def _start_speech(self, text: str) -> None:
        self._interrupt_speech()
        self._speak_generation += 1
        generation = self._speak_generation
        self.state = State.SPEAKING
        self.substatus = "Speaking"
        self._run(self._speak, generation, text)

    def _speak(self, generation: int, text: str) -> None:
        try:
            self.tts.speak(text)
        except Exception as exc:
            logger.warning("Speech failed: %s", exc)
            self._events.put(_SpeakDone(generation, str(exc)))
            return
        self._events.put(_SpeakDone(generation, ""))

    def _interrupt_speech(self) -> None:
        self._speak_generation += 1
        try:
            self.tts.stop()
        except Exception:
            logger.debug("interrupt speech failed", exc_info=True)

    def _run(self, target, *args) -> None:
        if self.synchronous:
            target(*args)
            return
        threading.Thread(target=target, args=args, daemon=True).start()

    def _enter_error(self, message: str) -> None:
        self.state = State.ERROR
        self.error = message
        self.substatus = "Needs attention"
        self.active_control = ""
        logger.info("Desk error: %s", message)

    def _set_notice(self, message: str) -> None:
        self.notice = message
        self.notice_at = time.monotonic()

    def _button(self, button_id: str) -> ButtonSettings | None:
        for button in self.buttons:
            if button.id == button_id:
                return button
        return None

    def _timeout_message(self) -> str:
        seconds = int(self.reply_timeout)
        if seconds >= 60 and seconds % 60 == 0:
            minutes = seconds // 60
            unit = "minute" if minutes == 1 else "minutes"
            return f"No reply arrived within {minutes} {unit}. Try again."
        return f"No reply arrived within {seconds} seconds. Try again."
