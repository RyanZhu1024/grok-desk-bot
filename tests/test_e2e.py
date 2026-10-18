"""End-to-end desk behavior against the in-process mock webhook and ntfy server."""

import time

import pytest

from desk_assistant.app import Assistant
from desk_assistant.audio import MockRecorder, MockSTT, MockTTS
from desk_assistant.config import default_buttons
from desk_assistant.mock_server import MockAssistantServer
from desk_assistant.models import State
from desk_assistant.ntfy import NtfyListener
from desk_assistant.runtime import run_test_mode
from desk_assistant.webhook import WebhookClient


def _pump(app: Assistant, predicate, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.tick()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out in state {app.state} error={app.error!r}")


@pytest.fixture
def desk():
    server = MockAssistantServer(reply_delay=0.05, reply_fn=lambda text, source: f"echo:{source}:{text}")
    server.start()
    topic = "desk-unit"
    listener = NtfyListener(server.base_url, topic, on_message=lambda _message: None)
    tts = MockTTS()
    app = Assistant(
        webhook=WebhookClient(f"{server.base_url}/webhook", "test-key"),
        listener=listener,
        stt=MockSTT("What is the weather today?"),
        tts=tts,
        recorder=MockRecorder(),
        buttons=default_buttons(),
        reply_timeout=5,
        synchronous=True,
        topic=topic,
        min_record_seconds=0.05,
        max_record_seconds=1.5,
    )
    app.start()
    server.wait_for_subscribers(topic, 1)
    try:
        yield server, app, tts
    finally:
        app.shutdown()
        server.stop()


def test_button_request_is_accepted_and_the_reply_is_spoken(desk):
    server, app, tts = desk
    app.press_button("briefing", source="button")
    _pump(app, lambda: app.state is State.IDLE and app.reply_log)
    assert server.requests[0] == {
        "text": "Give me my morning briefing.",
        "source": "button",
        "reply_topic": "desk-unit",
    }
    assert app.last_reply == "echo:button:Give me my morning briefing."
    assert tts.spoken == [app.last_reply]
    assert app.run_uuid


def test_touch_and_voice_sources(desk):
    server, app, tts = desk
    app.submit_text("dim the lamp", source="touch")
    _pump(app, lambda: app.state is State.IDLE and app.reply_log)
    app.ptt_down()
    time.sleep(0.08)
    app.ptt_up()
    _pump(app, lambda: any(source == "voice" for source, _text in app.request_log) and app.state is State.IDLE)
    sources = [call["source"] for call in server.requests]
    assert sources == ["touch", "voice"]
    assert server.requests[1]["text"] == "What is the weather today?"
    assert tts.spoken[-1].startswith("echo:voice:")


def test_routine_disabled_and_bad_key(desk):
    server, app, tts = desk
    server.status_override = 400
    app.press_button("calendar", source="button")
    _pump(app, lambda: app.state is State.ERROR)
    assert "turned off" in app.error
    assert tts.spoken == []

    server.status_override = None
    app.webhook.key = "wrong-key"
    app.press_button("inbox", source="button")
    _pump(app, lambda: "key was rejected" in app.error)
    assert app.state is State.ERROR
    assert tts.spoken == []


def test_notifications_are_shown_and_not_spoken(desk):
    server, app, tts = desk
    app.press_button("briefing")
    _pump(app, lambda: app.state is State.IDLE and app.reply_log)
    reply = app.last_reply
    server.publish("desk-unit", "Package delivered")
    _pump(app, lambda: app.notification == "Package delivered")
    assert app.last_reply == reply
    assert app.notification_log == ["Package delivered"]
    assert tts.spoken == [reply]
    assert app.state is State.IDLE


def test_timeout_then_a_late_reply_still_lands(desk):
    server, app, tts = desk
    server.silent = True
    app.reply_timeout = 0.35
    app.press_button("briefing")
    time.sleep(0.5)
    app.tick()
    assert app.state is State.ERROR
    assert "No reply arrived" in app.error
    server.publish("desk-unit", "Sorry I was slow.")
    _pump(app, lambda: app.last_reply == "Sorry I was slow.")
    assert tts.spoken == ["Sorry I was slow."]
    assert app.state is State.IDLE


def test_a_button_is_ignored_while_thinking(desk):
    server, app, _tts = desk
    server.reply_delay = 1.2
    app.press_button("briefing")
    app.press_button("calendar")
    assert "Still working" in app.notice
    assert len(server.requests) == 1
    assert server.requests[0]["text"].startswith("Give me my morning briefing")


def test_empty_transcript_does_not_call_the_webhook(desk):
    server, app, _tts = desk
    app.stt.transcript = "   "
    app.ptt_down()
    time.sleep(0.08)
    app.ptt_up()
    app.tick()
    assert app.state is State.ERROR
    assert "Didn't catch" in app.error
    assert server.requests == []


def test_scripted_laptop_test_mode(tmp_path):
    code = run_test_mode(script=True, screenshot_dir=str(tmp_path), reply_delay=0.2)
    assert code == 0
    for name in (
        "01-idle.png",
        "02-thinking.png",
        "03-speaking.png",
        "04-reply.png",
        "05-notification.png",
        "06-listening.png",
        "07-voice.png",
        "08-routine-off.png",
        "09-bad-key.png",
    ):
        shot = tmp_path / name
        assert shot.is_file(), name
        assert shot.stat().st_size > 1000, name
