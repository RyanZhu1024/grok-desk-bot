"""Unit tests for the webhook client. No network: httpx.MockTransport only."""

import json

import httpx
import pytest

from desk_assistant.models import WebhookError
from desk_assistant.webhook import WebhookClient


def _client(handler, url="https://assistant.example/hook", key="super-secret"):
    return WebhookClient(url, key, transport=httpx.MockTransport(handler))


def test_post_body_and_bearer_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["type"] = request.headers["Content-Type"]
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "runUuid": "run-123"})

    result = _client(handler).send("  hello   there ", "button", "desk-topic")
    assert result.ok
    assert result.run_uuid == "run-123"
    assert result.error is None
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer super-secret"
    assert seen["type"].startswith("application/json")
    assert seen["body"] == {
        "text": "hello there",
        "source": "button",
        "reply_topic": "desk-topic",
    }
    assert "super-secret" not in result.user_message


def test_haro_target_is_optional_and_omitted_for_the_desk():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "runUuid": "run-h"})

    client = _client(handler)
    desk = client.send("hello", "button", "desk-topic")
    assert desk.ok
    assert "target" not in seen["body"]
    haro = client.send("Rune what time is it", "haro", "haro-topic", target="Rune")
    assert haro.ok
    assert seen["body"] == {
        "text": "Rune what time is it",
        "source": "haro",
        "reply_topic": "haro-topic",
        "target": "rune",
    }


@pytest.mark.parametrize("source", ["voice", "button", "touch"])
def test_source_is_forwarded(source):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["source"] = json.loads(request.content)["source"]
        return httpx.Response(200, json={"success": True, "runUuid": "abc"})

    result = _client(handler).send("ping", source, "topic")
    assert result.ok
    assert seen["source"] == source


def test_success_without_run_uuid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True})

    result = _client(handler).send("ping", "touch", "topic")
    assert result.ok
    assert result.run_uuid is None


def test_routine_disabled_is_http_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "disabled"})

    result = _client(handler).send("ping", "button", "topic")
    assert not result.ok
    assert result.error is WebhookError.DISABLED
    assert "turned off" in result.user_message
    assert "super-secret" not in result.user_message
    assert "super-secret" not in result.detail


def test_bad_key_is_http_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "nope"})

    result = _client(handler).send("ping", "button", "topic")
    assert not result.ok
    assert result.error is WebhookError.UNAUTHORIZED
    assert "key was rejected" in result.user_message
    assert "super-secret" not in result.user_message


def test_other_http_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    result = _client(handler).send("ping", "button", "topic")
    assert result.error is WebhookError.HTTP
    assert "503" in result.user_message


def test_success_false_uses_server_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "routine is paused"})

    result = _client(handler).send("ping", "button", "topic")
    assert result.error is WebhookError.REJECTED
    assert result.user_message == "routine is paused"


def test_unreadable_success_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    result = _client(handler).send("ping", "button", "topic")
    assert result.error is WebhookError.REJECTED
    assert "unreadable" in result.user_message


def test_network_error_is_distinct_from_a_bad_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = _client(handler).send("ping", "voice", "topic")
    assert result.error is WebhookError.NETWORK
    assert "Couldn't reach" in result.user_message
    assert "super-secret" not in result.user_message


def test_timeout_is_a_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = _client(handler).send("ping", "voice", "topic")
    assert result.error is WebhookError.NETWORK


def test_missing_config_does_not_send():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"success": True})

    result = WebhookClient("", "", transport=httpx.MockTransport(handler)).send("ping", "button", "topic")
    assert called is False
    assert result.error is WebhookError.CONFIG
    assert "DESK_WEBHOOK_URL" in result.user_message


def test_empty_text_is_rejected_locally():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"success": True})

    result = _client(handler).send("   ", "button", "topic")
    assert called is False
    assert result.error is WebhookError.REJECTED


def test_bad_source_is_a_programmer_error():
    client = _client(lambda request: httpx.Response(200, json={"success": True}))
    with pytest.raises(ValueError):
        client.send("ping", "email", "topic")
