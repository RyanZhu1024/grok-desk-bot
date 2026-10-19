"""Device selection for the PowerConf speakerphone, without real hardware."""

from desk_assistant.audio import (
    parse_alsa_list,
    parse_pactl_short,
    player_argv,
    resolve_input_device,
    resolve_output_device,
    select_named_device,
)
from desk_assistant.config import Config, load_config
from desk_assistant.runtime import format_check

ARECORD_L = """\
**** List of CAPTURE Hardware Devices ****
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 2: PowerConf [Anker PowerConf S330], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

PACTL_SINKS = """\
58\talsa_output.platform-hdmi.stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tSUSPENDED
62\talsa_output.usb-Anker_PowerConf_S330-00.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tIDLE
"""

PACTL_SOURCES = """\
63\talsa_output.usb-Anker_PowerConf_S330-00.analog-stereo.monitor\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tIDLE
64\talsa_input.usb-Anker_PowerConf_S330-00.analog-stereo\tmodule-alsa-card.c\ts16le 2ch 48000Hz\tIDLE
"""

POWERCONF = "plughw:CARD=PowerConf,DEV=0"
SINK = "alsa_output.usb-Anker_PowerConf_S330-00.analog-stereo"


def test_alsa_list_keeps_the_card_name():
    devices = parse_alsa_list(ARECORD_L)
    assert devices[0][0] == "plughw:CARD=vc4hdmi0,DEV=0"
    assert devices[1] == (POWERCONF, f"Anker PowerConf S330 ({POWERCONF})")


def test_pactl_short_skips_blank_lines_and_can_drop_monitors():
    sinks = parse_pactl_short(PACTL_SINKS)
    assert [name for name, _desc in sinks] == [
        "alsa_output.platform-hdmi.stereo",
        SINK,
    ]
    sources = parse_pactl_short(PACTL_SOURCES, skip_monitors=True)
    assert [name for name, _desc in sources] == [
        "alsa_input.usb-Anker_PowerConf_S330-00.analog-stereo"
    ]


def test_substring_match_is_case_insensitive_and_skips_monitors():
    candidates = parse_pactl_short(PACTL_SOURCES)
    chosen = select_named_device("powerconf", candidates)
    assert chosen is not None
    assert chosen[0] == "alsa_input.usb-Anker_PowerConf_S330-00.analog-stereo"
    assert not chosen[0].endswith(".monitor")


def test_substring_match_falls_back_when_nothing_matches():
    candidates = parse_alsa_list(ARECORD_L)
    assert select_named_device("missing", candidates) is None
    assert select_named_device("", candidates) is None
    assert select_named_device("PowerConf", []) is None


def test_input_match_selects_powerconf_and_falls_back():
    config = Config()
    config.audio.input_match = "PowerConf"
    matched = resolve_input_device(config, candidates=parse_alsa_list(ARECORD_L))
    assert matched.source == "matched"
    assert matched.device == POWERCONF
    assert "PowerConf" in matched.report()

    missed = resolve_input_device(config, candidates=parse_alsa_list("card 0: Headphones [Built-in], device 0: X [Y]\n"))
    assert missed.source == "default"
    assert missed.device == ""
    assert "no capture device matched 'PowerConf'" in missed.report()


def test_explicit_arecord_device_wins_over_the_match():
    config = Config()
    config.audio.device = "plughw:1,0"
    config.audio.input_match = "PowerConf"
    chosen = resolve_input_device(config, candidates=parse_alsa_list(ARECORD_L))
    assert chosen.source == "configured"
    assert chosen.device == "plughw:1,0"


def test_empty_match_uses_the_default_device():
    config = Config()
    config.audio.input_match = ""
    chosen = resolve_input_device(config, candidates=parse_alsa_list(ARECORD_L))
    assert chosen.source == "default"
    assert chosen.device == ""
    assert "empty" in chosen.detail


def test_output_match_uses_the_paplay_sink_name():
    config = Config()
    config.audio.output_match = "PowerConf"
    chosen = resolve_output_device(config, "paplay", candidates=parse_pactl_short(PACTL_SINKS))
    assert chosen.source == "matched"
    assert chosen.device == SINK

    missed = resolve_output_device(config, "paplay", candidates=[("alsa_output.hdmi", "alsa_output.hdmi")])
    assert missed.source == "default"
    assert missed.device == ""

    other_player = resolve_output_device(config, "ffplay", candidates=parse_pactl_short(PACTL_SINKS))
    assert other_player.device == ""
    assert other_player.source == "default"


def test_player_argv_passes_the_selected_device():
    assert player_argv("paplay", "a.wav", SINK) == ["paplay", "--device", SINK, "a.wav"]
    assert player_argv("aplay", "a.wav", POWERCONF) == ["aplay", "-q", "-D", POWERCONF, "a.wav"]
    assert player_argv("paplay", "a.wav", "") == ["paplay", "a.wav"]
    assert player_argv("aplay", "a.wav") == ["aplay", "-q", "a.wav"]


def test_check_reports_the_devices_it_found(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "desk_assistant.audio.probe_capture_devices",
        lambda: (parse_alsa_list(ARECORD_L), "arecord -l"),
    )
    monkeypatch.setattr(
        "desk_assistant.audio.probe_playback_devices",
        lambda player: (parse_pactl_short(PACTL_SINKS), "pactl list short sinks"),
    )
    monkeypatch.setattr("desk_assistant.audio.detect_player", lambda: "paplay")
    config = load_config(generate_topic=False, persist_topic=False)
    config.ntfy.topic = "desk-check"
    config.state_dir = tmp_path
    config.audio.input_match = "PowerConf"
    config.audio.output_match = "PowerConf"
    text = format_check(config)
    assert f"Audio input: {POWERCONF}  (matched 'PowerConf';" in text
    assert f"Audio output: {SINK}  (matched 'PowerConf';" in text
    assert "Screen: 1280x720" in text
    assert "inbox: BCM 23" in text
