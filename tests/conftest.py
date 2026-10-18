import pytest


@pytest.fixture(autouse=True)
def _isolated_desk_env(monkeypatch):
    for name in (
        "DESK_CONFIG",
        "DESK_WEBHOOK_URL",
        "DESK_WEBHOOK_KEY",
        "DESK_NTFY_SERVER",
        "DESK_NTFY_TOPIC",
        "DESK_REPLY_TIMEOUT",
        "DESK_FULLSCREEN",
        "DESK_STATE_DIR",
        "DESK_INPUT",
    ):
        monkeypatch.delenv(name, raising=False)
