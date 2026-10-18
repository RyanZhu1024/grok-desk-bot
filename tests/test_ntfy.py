"""Unit tests for ntfy parsing and the live JSON-stream listener."""

import time

from desk_assistant.mock_server import MockAssistantServer
from desk_assistant.ntfy import NtfyListener, parse_ntfy_line


def test_parse_message_and_ignores_non_events():
    event = parse_ntfy_line(
        '{"id":"abc","time":10,"event":"message","topic":"desk","message":"Hello","title":"Hi"}'
    )
    assert event is not None
    assert event.event == "message"
    assert event.id == "abc"
    assert event.message == "Hello"
    assert event.title == "Hi"

    keepalive = parse_ntfy_line('{"id":"k","time":11,"event":"keepalive","topic":"desk"}')
    assert keepalive is not None
    assert keepalive.event == "keepalive"

    opened = parse_ntfy_line('{"id":"o","event":"open","topic":"desk"}')
    assert opened is not None
    assert opened.event == "open"

    sse = parse_ntfy_line('data: {"id":"s","time":1,"event":"message","topic":"desk","message":"via sse"}')
    assert sse is not None
    assert sse.message == "via sse"

    assert parse_ntfy_line("") is None
    assert parse_ntfy_line("   ") is None
    assert parse_ntfy_line(":keepalive") is None
    assert parse_ntfy_line("this is not json") is None
    assert parse_ntfy_line('{"message":"no event field"}') is None
    assert parse_ntfy_line("data: [DONE]") is None


def test_listener_receives_messages_and_skips_other_events():
    server = MockAssistantServer()
    server.start()
    received = []
    listener = NtfyListener(server.base_url, "topic-a", on_message=received.append)
    try:
        server.publish("topic-a", "old message that should not replay")
        listener.start()
        server.wait_for_subscribers("topic-a", 1)
        server.inject("topic-a", {"id": "k1", "time": 1, "event": "keepalive", "topic": "topic-a"})
        server.publish("topic-a", "one")
        server.publish("topic-a", "two", title="Ignored title")
        server.publish("topic-a", "", title="Title only")
        _wait(lambda: len(received) >= 3)
        assert [item.text for item in received] == ["one", "two", "Title only"]
        assert "old message" not in " ".join(item.text for item in received)
    finally:
        listener.stop()
        server.stop()


def test_listener_resumes_after_the_stream_drops():
    server = MockAssistantServer()
    server.start()
    received = []
    listener = NtfyListener(server.base_url, "topic-b", on_message=received.append)
    try:
        listener.start()
        server.wait_for_subscribers("topic-b", 1)
        server.publish("topic-b", "first")
        _wait(lambda: [item.text for item in received] == ["first"])
        first_streams = server.accepted_streams

        server.drop_subscribers()
        _wait(lambda: server.accepted_streams > first_streams, timeout=4)
        server.publish("topic-b", "second")
        _wait(lambda: [item.text for item in received] == ["first", "second"])
    finally:
        listener.stop()
        server.stop()


def _wait(predicate, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for ntfy")
