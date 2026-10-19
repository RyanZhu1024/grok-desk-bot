"""Haro voice loop: target, acknowledgement, eyes, and battery. No Pi hardware."""

import time

from desk_assistant.audio import MockRecorder, MockSTT, MockTTS, player_argv
from desk_assistant.battery import parse_battery_level
from desk_assistant.config import load_config
from desk_assistant.eyes import ConsoleEyes
from desk_assistant.haro import Haro, detect_target, is_acknowledgement
from desk_assistant.haro_runtime import run_haro_test
from desk_assistant.models import NtfyMessage, State, WebhookResult
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Webhook:
    def __init__(self, result=None) -> None:
        self.calls = []
        self.result = result or WebhookResult(ok=True, run_uuid="run-1")

    def send(self, text, source, reply_topic, target=None):
        self.calls.append(
            {"text": text, "source": source, "reply_topic": reply_topic, "target": target}
        )
        return self.result

    def close(self) -> None:
        return None


class _Listener:
    def __init__(self) -> None:
        self.on_message = None
        self.connected = True

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def push(self, text: str) -> None:
        self.on_message(NtfyMessage(id="1", topic="haro", message=text))


class _Levels:
    def __init__(self, levels) -> None:
        self.levels = list(levels)

    def read(self):
        if not self.levels:
            return None
        return self.levels.pop(0)


def _haro(transcript="Rune what time is it", battery=None, **kwargs):
    eyes = ConsoleEyes()
    webhook = _Webhook()
    listener = _Listener()
    tts = MockTTS()
    haro = Haro(
        webhook=webhook,
        listener=listener,
        stt=MockSTT(transcript),
        tts=tts,
        recorder=MockRecorder(),
        eyes=eyes,
        topic="haro-topic",
        reply_timeout=kwargs.get("reply_timeout", 5),
        min_record_seconds=0.05,
        max_record_seconds=2,
        battery=battery,
        battery_low_percent=20,
        battery_interval_seconds=kwargs.get("battery_interval", 60),
        synchronous=True,
    )
    haro.start()
    return haro, webhook, listener, tts, eyes


def _say(haro: Haro) -> None:
    haro.ptt_down()
    haro.tick()
    time.sleep(0.08)
    haro.ptt_up()
    haro.tick()


def test_leading_name_sets_the_target_and_keeps_the_full_text():
    assert detect_target("Rune what time is it") == "rune"
    assert detect_target("  ATHELAS, how are you") == "athelas"
    assert detect_target("hello there") == "ollivander"
    assert detect_target("runes are ready") == "ollivander"
    assert detect_target("Hey Rune") == "ollivander"
    assert is_acknowledgement("Asking Rune…")
    assert is_acknowledgement("asking athelas...")
    assert not is_acknowledgement("It will rain.")
    assert not is_acknowledgement("Askingmore")


def test_rune_request_sends_source_haro_and_the_full_transcript():
    haro, webhook, _listener, _tts, eyes = _haro()
    try:
        _say(haro)
        assert webhook.calls == [
            {
                "text": "Rune what time is it",
                "source": "haro",
                "reply_topic": "haro-topic",
                "target": "rune",
            }
        ]
        assert haro.state is State.THINKING
        assert eyes.mood == "pulsing"
    finally:
        haro.shutdown()


def test_ollivander_is_the_default_target():
    haro, webhook, _listener, _tts, _eyes = _haro("what is the weather")
    try:
        _say(haro)
        assert webhook.calls[0]["target"] == "ollivander"
        assert webhook.calls[0]["text"] == "what is the weather"
    finally:
        haro.shutdown()


def test_acknowledgement_and_reply_are_both_spoken():
    haro, _webhook, listener, tts, eyes = _haro("Athelas status")
    try:
        _say(haro)
        assert haro.last_target == "athelas"
        listener.push("Asking Athelas…")
        haro.tick()
        assert tts.spoken == ["Asking Athelas…"]
        assert haro.state is State.THINKING
        assert eyes.mood == "pulsing"
        listener.push("The lab is quiet.")
        haro.tick()
        assert tts.spoken == ["Asking Athelas…", "The lab is quiet."]
        assert haro.state is State.IDLE
        assert eyes.mood == "dim"
        assert "green" in eyes.history
        assert "bright" in eyes.history
        assert "red" not in eyes.history
    finally:
        haro.shutdown()


def test_webhook_failure_turns_the_eyes_red_and_does_not_speak():
    haro, webhook, _listener, tts, eyes = _haro()
    webhook.result = WebhookResult(ok=False, error=None, detail="nope")
    from desk_assistant.models import WebhookError

    webhook.result = WebhookResult(ok=False, error=WebhookError.UNAUTHORIZED)
    try:
        _say(haro)
        assert haro.state is State.ERROR
        assert "key was rejected" in haro.error
        assert tts.spoken == []
        assert eyes.mood == "red"
    finally:
        haro.shutdown()


def test_low_battery_is_spoken_once():
    battery = _Levels([15, 15, 80])
    haro, _webhook, _listener, tts, _eyes = _haro(battery=battery, battery_interval=0)
    haro._battery_checked = 0
    try:
        haro.tick()
        haro.tick()
        assert tts.spoken == ["Battery is low."]
        assert haro.state is State.IDLE
        haro._battery_checked = 0
        haro.tick()
        haro.tick()
        assert tts.spoken == ["Battery is low."]
        haro._battery_warned = False
        haro._battery_checked = 0
        haro.tick()
        haro.tick()
        assert tts.spoken == ["Battery is low."]
    finally:
        haro.shutdown()


def test_missing_battery_does_not_interrupt_idle():
    haro, _webhook, _listener, tts, eyes = _haro(battery=_Levels([None]), battery_interval=0)
    haro._battery_checked = 0
    try:
        haro.tick()
        assert tts.spoken == []
        assert haro.state is State.IDLE
        assert eyes.mood == "dim"
    finally:
        haro.shutdown()


def test_battery_reply_parser():
    assert parse_battery_level("battery: 42\n") == 42
    assert parse_battery_level("battery: 18.5") == 18.5
    assert parse_battery_level("model: PiSugar 3\nbattery: 7") == 7
    assert parse_battery_level("nope") is None
    assert parse_battery_level("battery: lots") is None
    assert parse_battery_level(None) is None


def test_playback_device_is_passed_to_aplay():
    device = "plughw:CARD=seeed2micvoicec,DEV=0"
    assert player_argv("aplay", "out.wav", device) == ["aplay", "-q", "-D", device, "out.wav"]
    assert player_argv("aplay", "out.wav") == ["aplay", "-q", "out.wav"]


def test_haro_example_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_STATE_DIR", str(tmp_path))
    config = load_config(ROOT / "config.haro.example.yaml")
    assert config.haro.button_gpio == 17
    assert config.haro.led_count == 3
    assert config.haro.spi_device == 1
    assert config.haro.battery is True
    assert config.haro.battery_low_percent == 20
    assert config.stt.engine == "vosk"
    assert config.stt.vosk_model.endswith("vosk-model-small-en-us-0.15")
    assert config.tts.engine == "espeak"
    assert config.audio.device == "plughw:CARD=seeed2micvoicec,DEV=0"
    assert config.audio.output_device == config.audio.device


def test_check_mentions_haro_and_skips_the_screen(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "stt: {engine: mock}\ntts: {engine: mock}\nharo: {battery: false}\nntfy: {topic: haro-check}\n",
        encoding="utf-8",
    )
    from desk_assistant.__main__ import main

    code = main(["--variant", "haro", "--check", "--config", str(config_path)])
    captured = capsys.readouterr().out
    assert code == 0
    assert "Variant: haro" in captured
    assert "Battery: disabled" in captured
    assert "BCM 17" in captured
    assert "pygame" not in captured.lower()
    assert "800" not in captured


def test_scripted_laptop_test_mode():
    assert run_haro_test(script=True) == 0
