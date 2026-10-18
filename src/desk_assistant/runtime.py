"""Wire the config, engines, listener, and screen together."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from desk_assistant.app import Assistant
from desk_assistant.audio import MockTTS, build_recorder, build_stt, build_tts, resolve_input_device, resolve_output_device
from desk_assistant.config import Config, load_config, redact_key
from desk_assistant.gpio_input import GpioInput
from desk_assistant.mock_server import MockAssistantServer
from desk_assistant.models import State
from desk_assistant.ntfy import NtfyListener
from desk_assistant.webhook import WebhookClient

logger = logging.getLogger(__name__)


def build_assistant(
    config: Config,
    *,
    test_mode: bool = False,
    synchronous: bool = False,
    prefer_mock_audio: bool = False,
    footer: str = "",
    test_note: str = "",
) -> Assistant:
    webhook = WebhookClient(
        config.webhook.url,
        config.webhook.key,
        timeout=config.webhook.timeout_seconds,
    )
    listener = NtfyListener(config.ntfy.server, config.ntfy.topic, on_message=lambda _message: None)
    stt, stt_name = build_stt(config, prefer_mock=prefer_mock_audio or config.stt.engine == "mock")
    tts, tts_name = build_tts(config, prefer_mock=prefer_mock_audio or config.tts.engine == "mock")
    recorder, rec_name = build_recorder(config, force_mock=prefer_mock_audio)
    logger.info("Reply topic is %s on %s", config.ntfy.topic, config.ntfy.server)
    logger.info("Audio: %s, %s, %s", rec_name, stt_name, tts_name)
    note = test_note
    if test_mode and not note and stt_name.startswith("mock"):
        note = "Talk sends a sample sentence. Press T to type."
    assistant = Assistant(
        webhook=webhook,
        listener=listener,
        stt=stt,
        tts=tts,
        recorder=recorder,
        buttons=config.buttons,
        reply_timeout=config.reply_timeout_seconds,
        speak_notifications=config.speak_notifications,
        min_record_seconds=config.audio.min_record_seconds,
        max_record_seconds=config.audio.max_record_seconds,
        synchronous=synchronous,
        test_mode=test_mode,
        test_note=note,
        topic=config.ntfy.topic,
        footer=footer,
    )
    return assistant


def resolve_input_backend(config: Config, *, test_mode: bool) -> str:
    if test_mode:
        return "keyboard"
    choice = config.input_backend
    if choice != "auto":
        return choice
    if Path("/dev/gpiochip0").exists() or Path("/dev/gpiomem").exists():
        return "gpio"
    return "keyboard"


def format_check(config: Config) -> str:
    stt, stt_name = build_stt(config)
    _tts, tts_name = build_tts(config)
    backend = resolve_input_backend(config, test_mode=False)
    player = config.tts.player
    if player == "auto":
        from desk_assistant.audio import detect_player

        player = detect_player() or "paplay"
    capture = resolve_input_device(config)
    playback = resolve_output_device(config, player)
    lines = [
        f"Config file: {config.config_path or '(defaults, no config.yaml found)'}",
        f"Webhook URL: {config.webhook.url or 'missing'}",
        f"Webhook key: {redact_key(config.webhook.key)}",
        f"ntfy: {config.ntfy.server.rstrip('/')}/{config.ntfy.topic}",
        f"Topic file: {config.state_dir / 'topic'}",
        f"Speech-to-text: {stt_name}",
        f"Text-to-speech: {tts_name}",
        capture.report(),
        playback.report(),
        f"Screen: {config.ui.width}x{config.ui.height}  fullscreen={'yes' if config.ui.fullscreen else 'no'}",
        f"Input: {backend}",
        f"Push-to-talk: BCM {config.ptt.gpio}  key {config.ptt.key}",
        f"Reply timeout: {config.reply_timeout_seconds:g}s",
        "Buttons:",
    ]
    for button in config.buttons:
        lines.append(
            f"  {button.id}: BCM {button.gpio}  key {button.key or '-'}  {button.text}"
        )
    del stt
    return "\n".join(lines)


def apply_video_driver(driver: str) -> None:
    if driver and driver != "auto":
        os.environ["SDL_VIDEODRIVER"] = driver


def run_ui(assistant: Assistant, config: Config, *, headless: bool = False) -> None:
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    apply_video_driver(config.ui.driver)
    import pygame

    from desk_assistant.ui import DeskDisplay

    pygame.init()
    pygame.display.set_caption("Desk Assistant")
    flags = 0
    size = (config.ui.width, config.ui.height)
    if config.ui.fullscreen and not headless:
        flags |= pygame.FULLSCREEN
    screen = pygame.display.set_mode((1, 1) if headless else size, flags)
    if not headless:
        pygame.mouse.set_visible(True)
    display = DeskDisplay()
    clock = pygame.time.Clock()
    mouse_talking = False
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if display.typing:
                    running = _typing_key(event, display, assistant) and running
                else:
                    running, mouse_talking = _key_down(event, assistant, display, mouse_talking)
            elif event.type == pygame.KEYUP and not display.typing:
                if event.key == pygame.K_SPACE:
                    assistant.ptt_up()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not headless:
                hit = display.hit(event.pos)
                if hit == "ptt":
                    mouse_talking = True
                    assistant.ptt_down()
                elif hit:
                    assistant.press_button(hit, source="touch")
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and mouse_talking:
                mouse_talking = False
                assistant.ptt_up()
        assistant.tick()
        if headless:
            clock.tick(30)
            continue
        display.render(screen, assistant.snapshot())
        pygame.display.flip()
        clock.tick(30)
    pygame.quit()


def _typing_key(event, display, assistant: Assistant) -> bool:
    import pygame

    if event.key == pygame.K_ESCAPE:
        display.typing = False
        display.buffer = ""
        return True
    if event.key == pygame.K_RETURN:
        text = display.buffer
        display.buffer = ""
        display.typing = False
        if text.strip():
            assistant.submit_text(text, "touch")
        return True
    if event.key == pygame.K_BACKSPACE:
        display.buffer = display.buffer[:-1]
        return True
    if event.unicode and event.unicode.isprintable():
        display.buffer += event.unicode
    return True


def _key_down(event, assistant: Assistant, display, mouse_talking: bool) -> tuple[bool, bool]:
    import pygame

    if event.key == pygame.K_ESCAPE:
        return False, mouse_talking
    if event.key == pygame.K_SPACE:
        assistant.ptt_down()
        return True, mouse_talking
    if event.key == pygame.K_t:
        display.typing = True
        display.buffer = ""
        return True, mouse_talking
    name = pygame.key.name(event.key)
    for button in assistant.buttons:
        if button.key and button.key.lower() == name.lower():
            assistant.press_button(button.id, source="button")
            return True, mouse_talking
    return True, mouse_talking


def run_test_mode(
    *,
    reply_delay: float = 0.8,
    windowed: bool = True,
    script: bool = False,
    screenshot_dir: str = "",
) -> int:
    """Laptop path: local mock webhook, keyboard instead of GPIO."""
    server = MockAssistantServer(key="test-key", reply_delay=reply_delay if not script else 0.55)
    base = server.start()
    try:
        config = load_config(persist_topic=False, generate_topic=False)
        config.webhook.url = f"{base}/webhook"
        config.webhook.key = "test-key"
        config.ntfy.server = base
        config.ntfy.topic = "desk-test"
        config.ui.fullscreen = False if windowed else config.ui.fullscreen
        config.ui.driver = "auto"
        config.input_backend = "keyboard"
        config.stt.engine = "mock"
        config.tts.engine = "mock" if script or not _can_speak() else "auto"
        config.audio.min_record_seconds = 0.2
        if script:
            return _run_script(server, config, screenshot_dir)
        print(f"Desk Assistant test mode")
        print(f"Mock webhook  {config.webhook.url}")
        print(f"ntfy topic    {config.ntfy.server}/{config.ntfy.topic}")
        print("Hold Space to talk, press 1/2/3 for presets, T to type, Esc to quit.")
        assistant = build_assistant(
            config,
            test_mode=True,
            prefer_mock_audio=config.tts.engine == "mock",
            footer="mock server",
        )
        if not isinstance(assistant.tts, MockTTS) and config.tts.engine == "mock":
            pass
        assistant.start()
        try:
            server.wait_for_subscribers(config.ntfy.topic, 1, timeout=3)
        except TimeoutError:
            logger.warning("Test mode did not confirm the ntfy subscription before opening the window")
        try:
            run_ui(assistant, config)
        finally:
            assistant.shutdown()
        return 0
    finally:
        server.stop()


def _can_speak() -> bool:
    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


class _PausingTTS:
    """Mock voice with a short pause so the speaking state is visible."""

    def __init__(self, pause: float = 0.35) -> None:
        self.spoken: list[str] = []
        self.pause = pause
        self._stop = False

    def speak(self, text: str) -> None:
        # stop() from the previous utterance must not cancel this one.
        self._stop = False
        self.spoken.append(text)
        end = time.monotonic() + self.pause
        while time.monotonic() < end and not self._stop:
            time.sleep(0.02)

    def stop(self) -> None:
        self._stop = True


def _run_script(server: MockAssistantServer, config: Config, screenshot_dir: str) -> int:
    out = Path(screenshot_dir) if screenshot_dir else Path("/tmp/desk-assistant-demo")
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame

    from desk_assistant.ui import DeskDisplay

    pygame.init()
    pygame.display.set_mode((1, 1))
    display = DeskDisplay()
    voice = _PausingTTS()
    assistant = build_assistant(
        config,
        test_mode=True,
        prefer_mock_audio=True,
        footer="scripted demo",
        test_note="Scripted run against the mock webhook.",
    )
    assistant.tts = voice
    assistant.min_record_seconds = 0.15
    assistant.start()
    failures: list[str] = []
    try:
        server.wait_for_subscribers(config.ntfy.topic, 1)
        _shot(display, assistant, config, out / "01-idle.png")

        assistant.press_button("briefing", source="button")
        _wait_until(
            assistant,
            lambda: assistant.state is State.THINKING and assistant.substatus.startswith("Waiting"),
            2,
        )
        _shot(display, assistant, config, out / "02-thinking.png")
        if assistant.state is not State.THINKING:
            failures.append(f"expected thinking after the briefing button, saw {assistant.state}")
        _wait_until(assistant, lambda: assistant.state is State.SPEAKING and bool(assistant.last_reply), 4)
        _shot(display, assistant, config, out / "03-speaking.png")
        _wait_until(assistant, lambda: assistant.state is State.IDLE and bool(assistant.reply_log), 4)
        _shot(display, assistant, config, out / "04-reply.png")

        server.publish(config.ntfy.topic, "The printer downstairs is free.")
        _wait_until(assistant, lambda: bool(assistant.notification_log), 3)
        _shot(display, assistant, config, out / "05-notification.png")

        assistant.ptt_down()
        if assistant.state is not State.LISTENING:
            failures.append(f"expected listening, saw {assistant.state}")
        _shot(display, assistant, config, out / "06-listening.png")
        time.sleep(0.2)
        assistant.ptt_up()
        _wait_until(
            assistant,
            lambda: any(source == "voice" for source, _text in assistant.request_log) and assistant.state is State.IDLE,
            4,
        )
        _shot(display, assistant, config, out / "07-voice.png")

        server.status_override = 400
        assistant.press_button("calendar", source="button")
        _wait_until(assistant, lambda: assistant.state is State.ERROR, 3)
        _shot(display, assistant, config, out / "08-routine-off.png")
        if "turned off" not in assistant.error:
            failures.append(f"400 error text was {assistant.error!r}")

        server.status_override = 401
        assistant.press_button("inbox", source="button")
        _wait_until(assistant, lambda: "key was rejected" in assistant.error, 3)
        _shot(display, assistant, config, out / "09-bad-key.png")

        briefing = next((text for source, text in assistant.request_log if source == "button"), "")
        if "morning briefing" not in briefing:
            failures.append(f"briefing request was {briefing!r}")
        if not any("Good morning" in reply for reply in assistant.reply_log):
            failures.append(f"missing briefing reply in {assistant.reply_log!r}")
        if not any(source == "voice" for source, _text in assistant.request_log):
            failures.append("voice request was not sent")
        if not any("printer" in note.lower() for note in assistant.notification_log):
            failures.append("notification was not shown")
        if assistant.reply_log and assistant.reply_log[0] == assistant.notification:
            failures.append("notification replaced the reply")
        spoken = " ".join(voice.spoken)
        if "Good morning" not in spoken:
            failures.append("briefing was not spoken")
        if "printer" in spoken.lower():
            failures.append("notification was spoken; only replies should be spoken")
        bodies = [call for call in server.requests]
        if not any(
            call.get("source") == "button"
            and call.get("reply_topic") == config.ntfy.topic
            and set(call) == {"text", "source", "reply_topic"}
            for call in bodies
        ):
            failures.append(f"webhook body was not what the Pi sends: {bodies!r}")
    except Exception as exc:
        failures.append(f"{exc.__class__.__name__}: {exc}")
    finally:
        assistant.shutdown()
        pygame.quit()

    _print_script_log(assistant)
    if failures:
        for failure in failures:
            print(f"demo failed: {failure}")
        return 1
    print(f"demo ok — screenshots in {out}")
    return 0


def _shot(display, assistant: Assistant, config: Config, path: Path) -> None:
    import pygame

    surface = pygame.Surface((config.ui.width, config.ui.height))
    display.render(surface, assistant.snapshot())
    pygame.image.save(surface, str(path))


def _wait_until(assistant: Assistant, predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assistant.tick()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError(f"timed out after {timeout:.1f}s in state {assistant.state}")


def _print_script_log(assistant: Assistant) -> None:
    for source, text in assistant.request_log:
        print(f"request [{source}] {text}")
    for reply in assistant.reply_log:
        print(f"reply {reply}")
    for note in assistant.notification_log:
        print(f"notification {note}")


def run_production(config: Config) -> int:
    backend = resolve_input_backend(config, test_mode=False)
    assistant = build_assistant(config, test_mode=False)
    gpio = None
    assistant.start()
    if not config.webhook.url or not config.webhook.key:
        assistant._enter_error(
            "Webhook URL and key are not set. "
            "Edit config.yaml or set DESK_WEBHOOK_URL and DESK_WEBHOOK_KEY."
        )
    if backend == "gpio":
        gpio = GpioInput(config, assistant)
        try:
            gpio.start()
        except Exception as exc:
            logger.error("%s", exc)
            assistant._enter_error(str(exc))
    elif backend != "keyboard":
        assistant._enter_error(f"Unknown input backend {backend}.")
    try:
        run_ui(assistant, config)
    finally:
        if gpio is not None:
            gpio.stop()
        assistant.shutdown()
    return 0
