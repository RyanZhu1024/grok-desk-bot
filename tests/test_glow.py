"""Glowing shell eyes: moods, animation frames, and the ntfy tag."""

import struct
import time
import wave

from desk_assistant.audio import _SubprocessTTS, wav_amplitude_envelope
from desk_assistant.config import load_config
from desk_assistant.eyes import ConsoleEyes, ConsoleGlow, HaroLights, apa102_frame, ws281x_channel
from desk_assistant.glow import (
    THINKING_PERIOD,
    eye_frame,
    split_reply,
)
from desk_assistant.models import State, WebhookError, WebhookResult
from test_haro import _Levels, _haro


def _lights():
    return HaroLights(ConsoleEyes(), ConsoleGlow())


def _sum(pixel):
    return pixel[0] + pixel[1] + pixel[2]


def test_mood_tag_is_stripped_before_speech():
    mood, spoken = split_reply("[mood:happy]\nThe lab is quiet.")
    assert mood == "happy"
    assert spoken == "The lab is quiet."
    mood, spoken = split_reply("[mood:CALM] The lab is quiet.")
    assert mood == "calm"
    assert spoken == "The lab is quiet."
    mood, spoken = split_reply("[mood:furious]\nHello there")
    assert mood is None
    assert spoken == "Hello there"
    mood, spoken = split_reply("The lab is quiet.")
    assert mood is None
    assert spoken == "The lab is quiet."
    assert split_reply("   ") == (None, "")


def test_moods_tint_the_same_animation():
    happy = eye_frame("listening", "happy", 0)[0]
    calm = eye_frame("listening", "calm", 0)[0]
    unsure = eye_frame("listening", "unsure", 0)[0]
    excited = eye_frame("listening", "excited", 0)[0]
    sad = eye_frame("listening", "sad", 0)[0]
    assert happy[1] > happy[0] and happy[1] > happy[2]
    assert calm[1] > calm[0] and abs(calm[1] - calm[2]) < 20
    assert unsure[0] > unsure[1] > unsure[2]
    assert _sum(excited) > _sum(happy)
    assert sad[2] > sad[1] > sad[0]
    assert _sum(sad) < _sum(happy)
    assert eye_frame("listening", "nope", 0) == eye_frame("listening", "happy", 0)


def test_each_glow_state_has_its_pattern():
    idle_low = eye_frame("idle", "happy", 0)
    idle_high = eye_frame("idle", "happy", 4.8 / 2)
    listening = eye_frame("listening", "happy", 0)
    assert idle_low[0] == idle_low[1]
    assert _sum(idle_low[0]) < _sum(idle_high[0]) < _sum(listening[0])

    left, right = eye_frame("thinking", "happy", THINKING_PERIOD / 4)
    assert _sum(left) > _sum(right)
    left, right = eye_frame("thinking", "calm", THINKING_PERIOD * 3 / 4)
    assert _sum(right) > _sum(left)

    loud = eye_frame("speaking", "happy", 0.4, amplitude=1)[0]
    quiet = eye_frame("speaking", "happy", 0.4, amplitude=0)[0]
    assert _sum(loud) > _sum(quiet) * 2
    first = eye_frame("speaking", "happy", 0.0)[0]
    later = eye_frame("speaking", "happy", 0.2)[0]
    assert first != later

    assert _sum(eye_frame("reply-arrived", "excited", 0.05)[0]) > 100
    assert eye_frame("reply-arrived", "excited", 0.15)[0] == (0, 0, 0)
    assert _sum(eye_frame("reply-arrived", "excited", 0.26)[0]) > 100
    assert eye_frame("reply-arrived", "excited", 0.50)[0] == (0, 0, 0)

    error = eye_frame("error", "sad", 1.2)
    assert error[0] == error[1]
    assert error[0][0] > 150 and error[0][1] == 0 and error[0][2] == 0
    assert error == eye_frame("error", "happy", 0)

    amber = eye_frame("low-battery", "sad", 0.05)[0]
    assert amber[0] > amber[1] > amber[2]
    assert amber == eye_frame("low-battery", "happy", 0.05)[0]
    assert eye_frame("low-battery", "happy", 0.5)[0] == (0, 0, 0)


def test_reply_flash_plays_before_the_speaking_flicker():
    lights = _lights()
    lights.flash_reply()
    lights.set_glow("speaking", "happy")
    started = lights._flash_started
    lights.render(started + 0.05, amplitude=0)
    assert _sum(lights.glow.left) > 50
    lights.render(started + 0.15, amplitude=1)
    assert lights.glow.left == (0, 0, 0)
    lights.render(started + 1.0, amplitude=1)
    assert _sum(lights.glow.left) > _sum(eye_frame("speaking", "happy", 0, 0)[0])
    assert lights.glow_history[:2] == ["reply-arrived", "speaking"]


def test_reply_mood_is_spoken_without_the_tag_and_tints_the_eyes():
    lights = _lights()
    haro, _webhook, listener, tts, eyes = _haro("Athelas status", eyes=lights)
    try:
        haro.ptt_down()
        haro.tick()
        time.sleep(0.08)
        haro.ptt_up()
        haro.tick()
        listener.push("[mood:excited]\nAsking Athelas…")
        haro.tick()
        assert tts.spoken == ["Asking Athelas…"]
        assert haro.color_mood == "excited"
        assert haro.state is State.THINKING
        assert "reply-arrived" not in lights.glow_history
        listener.push("[mood:calm]\nThe lab is quiet.")
        haro.tick()
        assert tts.spoken == ["Asking Athelas…", "The lab is quiet."]
        assert haro.last_reply == "The lab is quiet."
        assert haro.color_mood == "calm"
        assert lights.color_mood == "calm"
        assert "reply-arrived" in lights.glow_history
        assert "speaking" in lights.glow_history
        assert "bright" in eyes.history
        assert eyes.mood == "dim"
    finally:
        haro.shutdown()


def test_unknown_mood_is_dropped_and_the_previous_tint_remains():
    lights = _lights()
    haro, _webhook, listener, tts, _eyes = _haro(eyes=lights)
    try:
        haro.ptt_down()
        haro.tick()
        time.sleep(0.08)
        haro.ptt_up()
        haro.tick()
        listener.push("[mood:furious]\nHello there")
        haro.tick()
        assert tts.spoken == ["Hello there"]
        assert haro.color_mood == "happy"
    finally:
        haro.shutdown()


def test_a_mood_by_itself_flashes_and_does_not_speak():
    lights = _lights()
    haro, _webhook, listener, tts, eyes = _haro(eyes=lights)
    try:
        haro.ptt_down()
        haro.tick()
        time.sleep(0.08)
        haro.ptt_up()
        haro.tick()
        listener.push("[mood:sad]")
        haro.tick()
        assert tts.spoken == []
        assert haro.color_mood == "sad"
        assert haro.state is State.IDLE
        assert "reply-arrived" in lights.glow_history
        assert eyes.mood == "dim"
    finally:
        haro.shutdown()


def test_low_battery_blinks_amber_while_the_hat_stays_dim():
    lights = _lights()
    haro, _webhook, _listener, tts, eyes = _haro(battery=_Levels([15]), battery_interval=0, eyes=lights)
    haro._battery_checked = 0
    try:
        haro.tick()
        haro.tick()
        assert tts.spoken == ["Battery is low."]
        assert haro.state.value == "idle"
        assert eyes.mood == "dim"
        assert lights.glow_state == "low-battery"
    finally:
        haro.shutdown()


def test_a_webhook_error_is_red_on_both_layers():
    lights = _lights()
    haro, webhook, _listener, _tts, eyes = _haro(eyes=lights)
    webhook.result = WebhookResult(ok=False, error=WebhookError.UNAUTHORIZED)
    try:
        haro.ptt_down()
        haro.tick()
        time.sleep(0.08)
        haro.ptt_up()
        haro.tick()
        assert eyes.mood == "red"
        assert lights.glow_state == "error"
        red, _other = eye_frame("error", "happy", 0)
        assert red[0] > 0 and red[1] == 0
    finally:
        haro.shutdown()


def test_apa102_frame_puts_the_eyes_after_the_hat():
    frame = apa102_frame([(1, 2, 3, 4), (0, 0, 0, 1)], [(10, 20, 30), (40, 50, 60)])
    assert frame[:4] == [0, 0, 0, 0]
    assert frame[4:8] == [0xE0 | 4, 3, 2, 1]
    assert frame[8:12] == [0xE0 | 1, 0, 0, 0]
    assert frame[12:16] == [0xE0 | 31, 30, 20, 10]
    assert frame[16:20] == [0xE0 | 31, 60, 50, 40]
    assert frame[-4:] == [0xFF, 0xFF, 0xFF, 0xFF]


def test_ws281x_pwm_channel_follows_the_pin():
    assert ws281x_channel(12) == 0
    assert ws281x_channel(18) == 0
    assert ws281x_channel(13) == 1
    assert ws281x_channel(19) == 1


def test_wav_envelope_tracks_a_loud_hop_after_silence(tmp_path):
    path = tmp_path / "voice.wav"
    silence = [0] * 1600
    loud = [20000] * 1600
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"".join(struct.pack("<h", sample) for sample in silence + loud))
    envelope = wav_amplitude_envelope(str(path), hop_seconds=0.05)
    assert envelope[0] < 0.2
    assert max(envelope[2:]) > 0.8
    tts = _SubprocessTTS("aplay")
    assert tts.playback_level() is None
    tts._envelope = envelope
    tts._play_started = time.monotonic()
    tts._hop = 0.05
    tts._proc = object()
    assert tts.playback_level() == envelope[0]


def test_eye_settings_are_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_STATE_DIR", str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text("haro: {eye_type: WS2812B, eye_pin: 13, eye_brightness: 2}\n", encoding="utf-8")
    config = load_config(path)
    assert config.haro.eye_type == "ws2812"
    assert config.haro.eye_pin == 13
    assert config.haro.eye_brightness == 1.0
    path.write_text("haro: {eye_type: apa102, battery: false}\n", encoding="utf-8")
    config = load_config(path)
    assert config.haro.eye_type == "apa102"
    assert config.haro.eye_count == 2


def test_check_describes_chained_apa102_eyes(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        "stt: {engine: mock}\ntts: {engine: mock}\n"
        "haro: {battery: false, eye_type: apa102}\n"
        "ntfy: {topic: haro-glow}\n",
        encoding="utf-8",
    )
    from desk_assistant.__main__ import main

    code = main(["--variant", "haro", "--check", "--config", str(path)])
    captured = capsys.readouterr().out
    assert code == 0
    assert "APA102 LEDs chained" in captured
    assert "pygame" not in captured.lower()
