from pathlib import Path

import pytest

from desk_assistant.config import load_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config.example.yaml"


def test_example_config_loads_and_generates_a_topic(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_STATE_DIR", str(tmp_path))
    config = load_config(EXAMPLE)
    assert config.webhook.url == ""
    assert config.webhook.key == ""
    assert config.buttons[0].id == "briefing"
    assert config.buttons[0].key == "1"
    assert config.buttons[0].gpio == 27
    assert config.ptt.gpio == 17
    assert config.ptt.key == "space"
    assert config.ui.fullscreen is True
    assert config.reply_timeout_seconds == 180
    assert config.ntfy.topic.startswith("desk-")
    assert len(config.ntfy.topic) > 20
    saved = (tmp_path / "topic").read_text(encoding="utf-8").strip()
    assert saved == config.ntfy.topic

    again = load_config(EXAMPLE)
    assert again.ntfy.topic == config.ntfy.topic


def test_env_overrides_file_and_does_not_require_a_new_topic(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DESK_WEBHOOK_URL", "https://bot.example/hook")
    monkeypatch.setenv("DESK_WEBHOOK_KEY", "env-key")
    monkeypatch.setenv("DESK_NTFY_TOPIC", "desk-from-env")
    monkeypatch.setenv("DESK_NTFY_SERVER", "https://ntfy.example")
    config = load_config(EXAMPLE)
    assert config.webhook.url == "https://bot.example/hook"
    assert config.webhook.key == "env-key"
    assert config.ntfy.topic == "desk-from-env"
    assert config.ntfy.server == "https://ntfy.example"
    assert not (tmp_path / "topic").exists()


def test_duplicate_gpio_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "ptt: {gpio: 17, key: space}\n"
        "buttons:\n"
        "  - {id: a, label: A, text: hi, gpio: 17, key: '1'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unique"):
        load_config(path, generate_topic=False)


def test_secrets_are_gitignored():
    lines = []
    for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    for entry in ("config.yaml", ".env", "state/"):
        assert entry in lines
    assert "config.example.yaml" not in lines
