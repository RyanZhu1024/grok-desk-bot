"""Wire Haro without importing the desk screen."""

from __future__ import annotations

import logging
import os
import select
import sys
import termios
import time
import tty

from desk_assistant.audio import MockTTS, build_recorder, build_stt, build_tts
from desk_assistant.battery import PiSugarBattery
from desk_assistant.config import Config, load_config, redact_key
from desk_assistant.eyes import Apa102Eyes, ConsoleEyes
from desk_assistant.haro import Haro
from desk_assistant.mock_server import MockAssistantServer
from desk_assistant.models import State
from desk_assistant.ntfy import NtfyListener
from desk_assistant.webhook import WebhookClient

logger = logging.getLogger(__name__)


def build_haro(
    config: Config,
    *,
    test_mode: bool = False,
    synchronous: bool = False,
    eyes=None,
    battery=None,
) -> Haro:
    webhook = WebhookClient(
        config.webhook.url,
        config.webhook.key,
        timeout=config.webhook.timeout_seconds,
    )
    listener = NtfyListener(config.ntfy.server, config.ntfy.topic, on_message=lambda _message: None)
    prefer_mock = test_mode or config.stt.engine == "mock"
    stt, stt_name = build_stt(config, prefer_mock=prefer_mock or config.stt.engine == "mock")
    tts, tts_name = build_tts(config, prefer_mock=prefer_mock or config.tts.engine == "mock")
    recorder, rec_name = build_recorder(config, force_mock=test_mode)
    logger.info("Haro audio: %s, %s, %s", rec_name, stt_name, tts_name)
    if eyes is None:
        eyes = ConsoleEyes() if test_mode else Apa102Eyes(
            config.haro.led_count,
            config.haro.spi_bus,
            config.haro.spi_device,
        )
    if battery is None and config.haro.battery and not test_mode:
        battery = PiSugarBattery(
            config.haro.pisugar_socket,
            config.haro.pisugar_host,
            config.haro.pisugar_port,
        )
    return Haro(
        webhook=webhook,
        listener=listener,
        stt=stt,
        tts=tts,
        recorder=recorder,
        eyes=eyes,
        topic=config.ntfy.topic,
        reply_timeout=config.reply_timeout_seconds,
        min_record_seconds=config.audio.min_record_seconds,
        max_record_seconds=config.audio.max_record_seconds,
        battery=battery,
        battery_low_percent=config.haro.battery_low_percent,
        battery_interval_seconds=config.haro.battery_interval_seconds,
        synchronous=synchronous,
    )


def format_haro_check(config: Config) -> str:
    _stt, stt_name = build_stt(config)
    _tts, tts_name = build_tts(config)
    capture = config.audio.device.strip() or "default"
    playback = config.audio.output_device.strip() or "default"
    lines = [
        "Variant: haro",
        f"Config file: {config.config_path or '(defaults, no config.yaml found)'}",
        f"Webhook URL: {config.webhook.url or 'missing'}",
        f"Webhook key: {redact_key(config.webhook.key)}",
        f"ntfy: {config.ntfy.server.rstrip('/')}/{config.ntfy.topic}",
        f"Speech-to-text: {stt_name}",
        f"Text-to-speech: {tts_name}",
        f"Audio input: {capture}",
        f"Audio output: {playback}",
        f"Button: BCM {config.haro.button_gpio} (hold to talk, release to send)",
        (
            f"Eyes: {config.haro.led_count} APA102 LEDs on SPI "
            f"{config.haro.spi_bus}.{config.haro.spi_device}"
        ),
    ]
    if config.haro.battery:
        battery = PiSugarBattery(
            config.haro.pisugar_socket,
            config.haro.pisugar_host,
            config.haro.pisugar_port,
        )
        level = battery.read()
        if level is None:
            lines.append("Battery: PiSugar not available")
        else:
            lines.append(f"Battery: {level:g}%")
    else:
        lines.append("Battery: disabled")
    del _stt, _tts
    return "\n".join(lines)


class HaroButton:
    """Onboard ReSpeaker button. Press holds the mic; release sends."""

    def __init__(self, haro: Haro, gpio: int) -> None:
        self.haro = haro
        self.gpio = gpio
        self._button = None

    def start(self) -> None:
        try:
            from gpiozero import Button
        except ImportError as exc:
            raise RuntimeError(
                "gpiozero is not installed. On Raspberry Pi OS, install python3-gpiozero "
                "and python3-lgpio."
            ) from exc
        button = Button(self.gpio, pull_up=True, bounce_time=0.05)
        button.when_pressed = self.haro.ptt_down
        button.when_released = self.haro.ptt_up
        self._button = button
        logger.info("Haro button on BCM %s", self.gpio)

    def stop(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None


def run_haro_production(config: Config) -> int:
    haro = build_haro(config, test_mode=False, synchronous=False)
    button = HaroButton(haro, config.haro.button_gpio)
    try:
        button.start()
    except Exception as exc:
        logger.error("%s", exc)
        haro.shutdown()
        return 1
    haro.start()
    if not config.webhook.url or not config.webhook.key:
        logger.warning("Webhook URL and key are not set")
    try:
        while True:
            haro.tick()
            time.sleep(0.02)
    except KeyboardInterrupt:
        return 0
    finally:
        button.stop()
        haro.shutdown()


def run_haro_test(*, script: bool = False, transcript: str = "Rune what time is it") -> int:
    """Laptop path: no LEDs, no PiSugar, keyboard instead of GPIO17."""
    server = MockAssistantServer(key="test-key", reply_delay=0.05)
    server.silent = True
    base = server.start()
    try:
        config = load_config(persist_topic=False, generate_topic=False)
        config.webhook.url = f"{base}/webhook"
        config.webhook.key = "test-key"
        config.ntfy.server = base
        config.ntfy.topic = "haro-test"
        config.stt.engine = "mock"
        config.stt.mock_transcript = transcript
        config.tts.engine = "mock"
        config.audio.min_record_seconds = 0.05
        config.haro.battery = False
        eyes = ConsoleEyes()
        haro = build_haro(config, test_mode=True, synchronous=True, eyes=eyes, battery=None)
        haro.start()
        try:
            server.wait_for_subscribers(config.ntfy.topic, 1, timeout=3)
        except TimeoutError:
            logger.warning("Haro test mode did not confirm the ntfy subscription")
        if script:
            return _run_script(server, haro, eyes)
        print("Haro test mode. No screen.")
        print("Press space to start talking, space again to send, q to quit.")
        print("A terminal cannot see a key release, so space toggles the mic.")
        try:
            _keyboard_loop(haro)
        finally:
            haro.shutdown()
        return 0
    finally:
        server.stop()


def _run_script(server: MockAssistantServer, haro: Haro, eyes: ConsoleEyes) -> int:
    failures: list[str] = []
    try:
        haro.ptt_down()
        haro.tick()
        if haro.state is not State.LISTENING:
            failures.append(f"expected listening, saw {haro.state}")
        if eyes.mood != "green":
            failures.append(f"expected green eyes while listening, saw {eyes.mood}")
        time.sleep(0.08)
        haro.ptt_up()
        _wait(haro, lambda: bool(server.requests), 2)
        if not server.requests:
            failures.append("webhook was not called")
        else:
            body = server.requests[0]
            if body.get("source") != "haro":
                failures.append(f"source was {body.get('source')!r}")
            if body.get("target") != "rune":
                failures.append(f"target was {body.get('target')!r}")
            if body.get("text") != "Rune what time is it":
                failures.append(f"text was {body.get('text')!r}")
            if set(body) != {"text", "source", "reply_topic", "target"}:
                failures.append(f"body keys were {sorted(body)}")
        server.publish(haro.topic, "Asking Rune…")
        _wait(haro, lambda: bool(_spoken(haro)), 2)
        server.publish(haro.topic, "It will rain.")
        _wait(haro, lambda: haro.state is State.IDLE and len(_spoken(haro)) >= 2, 2)
        spoken = _spoken(haro)
        if spoken != ["Asking Rune…", "It will rain."]:
            failures.append(f"spoken was {spoken!r}")
        if haro.state is not State.IDLE:
            failures.append(f"expected idle after the reply, saw {haro.state}")
        if "green" not in eyes.history or "pulsing" not in eyes.history or "bright" not in eyes.history:
            failures.append(f"eye history was {eyes.history!r}")
        if eyes.mood != "dim":
            failures.append(f"expected dim eyes when idle, saw {eyes.mood}")
    except Exception as exc:
        failures.append(f"{exc.__class__.__name__}: {exc}")
    finally:
        haro.shutdown()
    if failures:
        for failure in failures:
            print(f"haro demo failed: {failure}")
        return 1
    print("haro demo ok")
    return 0


def _wait(haro: Haro, predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        haro.tick()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError(f"timed out after {timeout:.1f}s in state {haro.state}")


def _spoken(haro: Haro) -> list[str]:
    tts = haro.tts
    if isinstance(tts, MockTTS):
        return list(tts.spoken)
    return list(haro.reply_log)


def _keyboard_loop(haro: Haro) -> None:
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        while True:
            haro.tick()
            time.sleep(0.05)
    old = termios.tcgetattr(fd)
    holding = False
    try:
        tty.setcbreak(fd)
        while True:
            haro.tick()
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            data = os.read(fd, 8)
            if any(key in data for key in (b"q", b"Q", b"\x03", b"\x1b")):
                break
            if b" " not in data:
                continue
            if holding:
                haro.ptt_up()
                holding = False
            else:
                haro.ptt_down()
                holding = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if holding:
            haro.ptt_up()
            haro.tick()
