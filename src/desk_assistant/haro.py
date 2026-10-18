"""Screenless hold-to-talk loop for the Haro build.

The desk treats the first ntfy message as the whole reply. Haro does not:
a short "Asking Rune…" acknowledgement can arrive first, and both that and
the later reply are spoken. The LEDs follow the same states, without a screen.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from pathlib import Path

from desk_assistant.audio import Recorder, SpeechToText, TextToSpeech
from desk_assistant.eyes import (
    MOOD_BRIGHT,
    MOOD_DIM,
    MOOD_GREEN,
    MOOD_PULSING,
    MOOD_RED,
)
from desk_assistant.models import NtfyMessage, State, WebhookResult
from desk_assistant.ntfy import NtfyListener
from desk_assistant.webhook import WebhookClient

logger = logging.getLogger(__name__)

_LEADING_TARGET = re.compile(r"^(rune|athelas)\b", re.IGNORECASE)

_MOOD = {
    State.IDLE: MOOD_DIM,
    State.LISTENING: MOOD_GREEN,
    State.TRANSCRIBING: MOOD_GREEN,
    State.THINKING: MOOD_PULSING,
    State.SPEAKING: MOOD_BRIGHT,
    State.ERROR: MOOD_RED,
}


def detect_target(text: str) -> str:
    """Map a leading Rune or Athelas to a target. Anything else is ollivander.

    The spoken words stay in the text. Only the target field changes.
    """
    match = _LEADING_TARGET.match((text or "").lstrip())
    if match is None:
        return "ollivander"
    return match.group(1).lower()


def is_acknowledgement(text: str) -> bool:
    """True for a short status such as ``Asking Rune…``, not for a real answer."""
    lowered = (text or "").strip().lower()
    if not lowered.startswith("asking"):
        return False
    rest = lowered[len("asking") :]
    return rest == "" or not rest[0].isalnum()


class _Event:
    def __init__(self, kind: str, **payload) -> None:
        self.kind = kind
        self.payload = payload


class Haro:
    def __init__(
        self,
        *,
        webhook: WebhookClient,
        listener: NtfyListener,
        stt: SpeechToText,
        tts: TextToSpeech,
        recorder: Recorder,
        eyes,
        topic: str,
        reply_timeout: float = 180.0,
        min_record_seconds: float = 0.25,
        max_record_seconds: float = 30.0,
        battery=None,
        battery_low_percent: float = 20.0,
        battery_interval_seconds: float = 60.0,
        synchronous: bool = False,
    ) -> None:
        self.webhook = webhook
        self.listener = listener
        self.stt = stt
        self.tts = tts
        self.recorder = recorder
        self.eyes = eyes
        self.topic = topic
        self.reply_timeout = reply_timeout
        self.min_record_seconds = min_record_seconds
        self.max_record_seconds = max_record_seconds
        self.battery = battery
        self.battery_low_percent = battery_low_percent
        self.battery_interval = battery_interval_seconds
        self.synchronous = synchronous

        self.state = State.IDLE
        self.substatus = "Ready"
        self.error = ""
        self.last_request = ""
        self.last_target = ""
        self.last_reply = ""
        self.battery_percent: float | None = None
        self.request_log: list[tuple[str, str]] = []
        self.reply_log: list[str] = []
        self.ack_log: list[str] = []

        self._commands: queue.Queue = queue.Queue()
        self._events: queue.Queue = queue.Queue()
        self._generation = 0
        self._speak_generation = 0
        self._listen_started = 0.0
        self._awaiting_final = False
        self._reply_deadline: float | None = None
        self._speaking = False
        self._speech: list[tuple[str, State]] = []
        self._after_speech = State.IDLE
        self._battery_checked = time.monotonic()
        self._battery_warned = False
        self._apply_eyes()

    def start(self) -> None:
        self.listener.on_message = self._enqueue_ntfy
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
        self.eyes.close()

    def ptt_down(self) -> None:
        self._commands.put("down")

    def ptt_up(self) -> None:
        self._commands.put("up")

    def tick(self) -> None:
        for _ in range(12):
            if not self._drain():
                break
        self._check_timers()
        self._maybe_battery()
        if self.state is State.THINKING:
            self.eyes.pulse(time.monotonic())

    def _enqueue_ntfy(self, message: NtfyMessage) -> None:
        self._events.put(_Event("ntfy", message=message))

    def _drain(self) -> bool:
        progressed = False
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                break
            progressed = True
            if command == "down":
                self._hold()
            else:
                self._release()
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            progressed = True
            self._apply(event)
        return progressed

    def _hold(self) -> None:
        if self.state in {State.LISTENING, State.TRANSCRIBING, State.THINKING}:
            return
        if self._speaking or self.state is State.SPEAKING:
            self._interrupt_speech()
        self._generation += 1
        self._awaiting_final = False
        self._reply_deadline = None
        self._speech.clear()
        try:
            self.recorder.start()
        except Exception as exc:
            logger.warning("Microphone failed to start: %s", exc)
            self._enter_error(f"Microphone problem. {exc}")
            return
        self.error = ""
        self._listen_started = time.monotonic()
        self.state = State.LISTENING
        self.substatus = "Listening"
        self._apply_eyes()

    def _release(self) -> None:
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
            self.substatus = "Hold the button a little longer."
            self._apply_eyes()
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self.state = State.TRANSCRIBING
        self.substatus = "Transcribing"
        self._apply_eyes()
        self._run(self._transcribe, self._generation, wav)

    def _apply(self, event: _Event) -> None:
        if event.kind == "transcript":
            self._on_transcript(event.payload["generation"], event.payload["text"], event.payload["error"])
        elif event.kind == "webhook":
            self._on_webhook(event.payload["generation"], event.payload["result"])
        elif event.kind == "ntfy":
            self._on_ntfy(event.payload["message"])
        elif event.kind == "spoken":
            self._on_spoken(event.payload["generation"], event.payload["error"])

    def _on_transcript(self, generation: int, text: str, error: str) -> None:
        if generation != self._generation or self.state is not State.TRANSCRIBING:
            return
        if error:
            self._enter_error(error)
            return
        cleaned = " ".join((text or "").split())
        if not cleaned:
            self._enter_error("Didn't catch that. Try again.")
            return
        target = detect_target(cleaned)
        self.last_request = cleaned
        self.last_target = target
        self.last_reply = ""
        self._generation += 1
        generation = self._generation
        self._awaiting_final = True
        self._reply_deadline = time.monotonic() + self.reply_timeout
        self.state = State.THINKING
        self.substatus = f"Asking {target}"
        self.request_log.append((target, cleaned))
        self._apply_eyes()
        self._run(self._post, generation, cleaned, target)

    def _post(self, generation: int, text: str, target: str) -> None:
        result = self.webhook.send(text, "haro", self.topic, target=target)
        self._events.put(_Event("webhook", generation=generation, result=result))

    def _on_webhook(self, generation: int, result: WebhookResult) -> None:
        if generation != self._generation:
            return
        if not result.ok:
            self._awaiting_final = False
            self._reply_deadline = None
            self._enter_error(result.user_message)
            return
        if self.state is State.THINKING:
            self.substatus = "Waiting for a reply"

    def _on_ntfy(self, message: NtfyMessage) -> None:
        text = message.text
        if not text or not self._awaiting_final:
            return
        if is_acknowledgement(text):
            self.ack_log.append(text)
            logger.info("Acknowledgement arrived (%d chars)", len(text))
            self._enqueue_speech(text, State.THINKING)
            return
        self._awaiting_final = False
        self._reply_deadline = None
        self.last_reply = text
        self.reply_log.append(text)
        logger.info("Reply arrived (%d chars)", len(text))
        self._enqueue_speech(text, State.IDLE)

    def _enqueue_speech(self, text: str, after: State) -> None:
        self._speech.append((text, after))
        self._pump_speech()

    def _pump_speech(self) -> None:
        if self._speaking or not self._speech:
            return
        text, after = self._speech.pop(0)
        self._after_speech = after
        self._speaking = True
        self._speak_generation += 1
        generation = self._speak_generation
        self.state = State.SPEAKING
        self.substatus = "Speaking"
        self._apply_eyes()
        self._run(self._speak, generation, text)

    def _speak(self, generation: int, text: str) -> None:
        error = ""
        try:
            self.tts.speak(text)
        except Exception as exc:
            logger.warning("Speech failed: %s", exc)
            error = str(exc)
        self._events.put(_Event("spoken", generation=generation, error=error))

    def _on_spoken(self, generation: int, error: str) -> None:
        if generation != self._speak_generation:
            return
        self._speaking = False
        if error:
            self._speech.clear()
            self._enter_error(f"Couldn't speak. {error}")
            return
        self.state = self._after_speech
        if self.state is State.IDLE:
            self.substatus = "Ready"
        elif self.state is State.THINKING and self._awaiting_final:
            self.substatus = "Waiting for a reply"
        self._apply_eyes()
        self._pump_speech()

    def _transcribe(self, generation: int, wav: Path) -> None:
        text = ""
        error = ""
        try:
            text = self.stt.transcribe(wav)
        except Exception as exc:
            logger.warning("Transcription failed: %s", exc)
            error = str(exc)
        finally:
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass
        self._events.put(_Event("transcript", generation=generation, text=text, error=error))

    def _interrupt_speech(self) -> None:
        self._speak_generation += 1
        self._speaking = False
        self._speech.clear()
        try:
            self.tts.stop()
        except Exception:
            logger.debug("interrupt speech failed", exc_info=True)

    def _check_timers(self) -> None:
        now = time.monotonic()
        if self.state is State.LISTENING and now - self._listen_started >= self.max_record_seconds:
            self._release()
        if (
            self._awaiting_final
            and self.state is State.THINKING
            and not self._speaking
            and self._reply_deadline is not None
            and now >= self._reply_deadline
        ):
            self._awaiting_final = False
            self._reply_deadline = None
            self._enter_error(self._timeout_message())

    def _maybe_battery(self) -> None:
        if self.battery is None or self.state is not State.IDLE or self._speaking:
            return
        now = time.monotonic()
        if now - self._battery_checked < self.battery_interval:
            return
        self._battery_checked = now
        try:
            level = self.battery.read()
        except Exception as exc:
            logger.debug("Battery read failed: %s", exc)
            return
        self.battery_percent = level
        if level is None:
            return
        if level <= self.battery_low_percent:
            if not self._battery_warned:
                self._battery_warned = True
                self._enqueue_speech("Battery is low.", State.IDLE)
        else:
            self._battery_warned = False

    def _enter_error(self, message: str) -> None:
        self.state = State.ERROR
        self.error = message
        self.substatus = "Needs attention"
        self._apply_eyes()
        logger.info("Haro error: %s", message)

    def _apply_eyes(self) -> None:
        self.eyes.set_mood(_MOOD[self.state])

    def _run(self, target, *args) -> None:
        if self.synchronous:
            target(*args)
            return
        threading.Thread(target=target, args=args, daemon=True).start()

    def _timeout_message(self) -> str:
        seconds = int(self.reply_timeout)
        if seconds >= 60 and seconds % 60 == 0:
            minutes = seconds // 60
            unit = "minute" if minutes == 1 else "minutes"
            return f"No reply arrived within {minutes} {unit}. Try again."
        return f"No reply arrived within {seconds} seconds. Try again."
