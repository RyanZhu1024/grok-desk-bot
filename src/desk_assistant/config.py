"""Load desk-assistant settings from a YAML file and the environment.

Environment variables win over the file. The webhook key is never written
into logs from this module. A missing ntfy topic is generated once and stored
in the state directory so the same topic survives a reboot.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TOPIC_RE_MAX = 64


@dataclass
class WebhookSettings:
    url: str = ""
    key: str = ""
    timeout_seconds: float = 20.0


@dataclass
class NtfySettings:
    server: str = "https://ntfy.sh"
    topic: str = ""


@dataclass
class ButtonSettings:
    id: str
    label: str
    text: str
    gpio: int | None = None
    key: str = ""


@dataclass
class PttSettings:
    gpio: int | None = 17
    key: str = "space"


@dataclass
class SttSettings:
    engine: str = "auto"
    vosk_model: str = ""
    whisper_bin: str = "whisper-cli"
    whisper_model: str = ""
    mock_transcript: str = "Give me a short status update."


@dataclass
class TtsSettings:
    engine: str = "auto"
    piper_bin: str = "piper"
    piper_model: str = ""
    espeak_bin: str = "espeak-ng"
    espeak_voice: str = "en-us"
    espeak_speed: int = 155
    player: str = "auto"


@dataclass
class AudioSettings:
    sample_rate: int = 16000
    max_record_seconds: float = 30.0
    min_record_seconds: float = 0.25
    device: str = ""


@dataclass
class UiSettings:
    width: int = 800
    height: int = 480
    fullscreen: bool = False
    driver: str = "auto"


@dataclass
class Config:
    webhook: WebhookSettings = field(default_factory=WebhookSettings)
    ntfy: NtfySettings = field(default_factory=NtfySettings)
    buttons: list[ButtonSettings] = field(default_factory=list)
    ptt: PttSettings = field(default_factory=PttSettings)
    stt: SttSettings = field(default_factory=SttSettings)
    tts: TtsSettings = field(default_factory=TtsSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    reply_timeout_seconds: float = 180.0
    speak_notifications: bool = False
    input_backend: str = "auto"
    state_dir: Path = field(default_factory=lambda: default_state_dir())
    config_path: Path | None = None
    topic_persisted: bool = False


def default_buttons() -> list[ButtonSettings]:
    return [
        ButtonSettings(
            id="briefing",
            label="Briefing",
            text="Give me my morning briefing.",
            gpio=27,
            key="1",
        ),
        ButtonSettings(
            id="calendar",
            label="Calendar",
            text="What's next on my calendar?",
            gpio=22,
            key="2",
        ),
        ButtonSettings(
            id="inbox",
            label="Inbox",
            text="Any urgent messages I should know about?",
            gpio=23,
            key="3",
        ),
    ]


def default_state_dir() -> Path:
    override = os.environ.get("DESK_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "desk-assistant"


def candidate_config_paths() -> list[Path]:
    env = os.environ.get("DESK_CONFIG")
    if env:
        return [Path(env).expanduser()]
    return [
        Path.cwd() / "config.yaml",
        Path.home() / ".config" / "desk-assistant" / "config.yaml",
        Path("/etc/desk-assistant/config.yaml"),
    ]


def find_config_path() -> Path | None:
    for path in candidate_config_paths():
        if path.is_file():
            return path
    return None


def load_config(
    path: str | Path | None = None,
    *,
    persist_topic: bool = True,
    generate_topic: bool = True,
) -> Config:
    config_path: Path | None
    if path:
        config_path = Path(path).expanduser()
        raw = _read_yaml(config_path)
    else:
        config_path = find_config_path()
        raw = _read_yaml(config_path) if config_path else {}

    config = Config(
        webhook=_webhook(raw.get("webhook") or {}),
        ntfy=_ntfy(raw.get("ntfy") or {}),
        buttons=_buttons(raw.get("buttons")),
        ptt=_ptt(raw.get("ptt") or {}),
        stt=_stt(raw.get("stt") or {}),
        tts=_tts(raw.get("tts") or {}),
        audio=_audio(raw.get("audio") or {}),
        ui=_ui(raw.get("ui") or {}),
        reply_timeout_seconds=_as_float(raw.get("reply_timeout_seconds"), 180.0),
        speak_notifications=_as_bool(raw.get("speak_notifications"), False),
        input_backend=_as_str(raw.get("input_backend"), "auto") or "auto",
        state_dir=default_state_dir(),
        config_path=config_path,
    )
    _apply_env(config)
    _validate(config)
    if generate_topic:
        config.ntfy.topic = resolve_topic(config, persist=persist_topic)
    return config


def resolve_topic(config: Config, *, persist: bool) -> str:
    existing = config.ntfy.topic.strip()
    if existing:
        _check_topic(existing)
        return existing
    path = config.state_dir / "topic"
    if path.is_file():
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            _check_topic(saved)
            config.topic_persisted = True
            return saved
    topic = "desk-" + secrets.token_hex(16)
    _check_topic(topic)
    if persist:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(topic + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        config.topic_persisted = True
    return topic


def redact_key(key: str) -> str:
    if not key:
        return "missing"
    return f"set ({len(key)} characters)"


def _read_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def _apply_env(config: Config) -> None:
    if os.environ.get("DESK_WEBHOOK_URL"):
        config.webhook.url = os.environ["DESK_WEBHOOK_URL"].strip()
    if os.environ.get("DESK_WEBHOOK_KEY"):
        config.webhook.key = os.environ["DESK_WEBHOOK_KEY"]
    if os.environ.get("DESK_NTFY_SERVER"):
        config.ntfy.server = os.environ["DESK_NTFY_SERVER"].strip()
    if os.environ.get("DESK_NTFY_TOPIC"):
        config.ntfy.topic = os.environ["DESK_NTFY_TOPIC"].strip()
    if os.environ.get("DESK_REPLY_TIMEOUT"):
        config.reply_timeout_seconds = float(os.environ["DESK_REPLY_TIMEOUT"])
    if os.environ.get("DESK_FULLSCREEN"):
        config.ui.fullscreen = _as_bool(os.environ["DESK_FULLSCREEN"], config.ui.fullscreen)
    if os.environ.get("DESK_STATE_DIR"):
        config.state_dir = Path(os.environ["DESK_STATE_DIR"]).expanduser()
    if os.environ.get("DESK_INPUT"):
        config.input_backend = os.environ["DESK_INPUT"].strip()


def _validate(config: Config) -> None:
    ids = [button.id for button in config.buttons]
    if len(ids) != len(set(ids)):
        raise ValueError("button ids must be unique")
    pins: list[int] = []
    if config.ptt.gpio is not None:
        pins.append(config.ptt.gpio)
    for button in config.buttons:
        if button.gpio is not None:
            pins.append(button.gpio)
    if len(pins) != len(set(pins)):
        raise ValueError("GPIO pins for the buttons and push-to-talk must be unique")
    if config.reply_timeout_seconds <= 0:
        raise ValueError("reply_timeout_seconds must be positive")


def _check_topic(topic: str) -> None:
    if len(topic) > TOPIC_RE_MAX or not topic:
        raise ValueError("ntfy topic must be 1 to 64 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(ch not in allowed for ch in topic):
        raise ValueError(
            "ntfy topic may only contain letters, numbers, '_' and '-'"
        )


def _webhook(raw: dict) -> WebhookSettings:
    return WebhookSettings(
        url=_as_str(raw.get("url")).strip(),
        key=_as_str(raw.get("key")),
        timeout_seconds=_as_float(raw.get("timeout_seconds"), 20.0),
    )


def _ntfy(raw: dict) -> NtfySettings:
    return NtfySettings(
        server=_as_str(raw.get("server"), "https://ntfy.sh").strip() or "https://ntfy.sh",
        topic=_as_str(raw.get("topic")).strip(),
    )


def _buttons(raw) -> list[ButtonSettings]:
    if raw is None:
        return default_buttons()
    if not isinstance(raw, list):
        raise ValueError("buttons must be a list")
    buttons: list[ButtonSettings] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("each button must be a mapping")
        label = _as_str(item.get("label"), f"Button {index + 1}")
        ident = _as_str(item.get("id"), label.lower().replace(" ", "-"))
        buttons.append(
            ButtonSettings(
                id=ident,
                label=label,
                text=_as_str(item.get("text")),
                gpio=_as_optional_int(item.get("gpio")),
                key=_as_str(item.get("key")),
            )
        )
    return buttons


def _ptt(raw: dict) -> PttSettings:
    gpio = raw.get("gpio", 17)
    return PttSettings(gpio=_as_optional_int(gpio), key=_as_str(raw.get("key"), "space") or "space")


def _stt(raw: dict) -> SttSettings:
    return SttSettings(
        engine=_as_str(raw.get("engine"), "auto") or "auto",
        vosk_model=_as_str(raw.get("vosk_model")),
        whisper_bin=_as_str(raw.get("whisper_bin"), "whisper-cli") or "whisper-cli",
        whisper_model=_as_str(raw.get("whisper_model")),
        mock_transcript=_as_str(raw.get("mock_transcript"), "Give me a short status update.")
        or "Give me a short status update.",
    )


def _tts(raw: dict) -> TtsSettings:
    return TtsSettings(
        engine=_as_str(raw.get("engine"), "auto") or "auto",
        piper_bin=_as_str(raw.get("piper_bin"), "piper") or "piper",
        piper_model=_as_str(raw.get("piper_model")),
        espeak_bin=_as_str(raw.get("espeak_bin"), "espeak-ng") or "espeak-ng",
        espeak_voice=_as_str(raw.get("espeak_voice"), "en-us") or "en-us",
        espeak_speed=int(_as_float(raw.get("espeak_speed"), 155)),
        player=_as_str(raw.get("player"), "auto") or "auto",
    )


def _audio(raw: dict) -> AudioSettings:
    return AudioSettings(
        sample_rate=int(_as_float(raw.get("sample_rate"), 16000)),
        max_record_seconds=_as_float(raw.get("max_record_seconds"), 30.0),
        min_record_seconds=_as_float(raw.get("min_record_seconds"), 0.25),
        device=_as_str(raw.get("device")),
    )


def _ui(raw: dict) -> UiSettings:
    return UiSettings(
        width=int(_as_float(raw.get("width"), 800)),
        height=int(_as_float(raw.get("height"), 480)),
        fullscreen=_as_bool(raw.get("fullscreen"), False),
        driver=_as_str(raw.get("driver"), "auto") or "auto",
    )


def _as_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_float(value, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _as_bool(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_optional_int(value) -> int | None:
    if value is None or value == "" or value is False:
        return None
    return int(value)
