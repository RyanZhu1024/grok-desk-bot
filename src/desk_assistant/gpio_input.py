"""Arcade buttons on the Raspberry Pi GPIO header.

Buttons are wired from a BCM pin to ground. The internal pull-up makes a
press read low. gpiozero is imported only when this backend starts, so a
laptop can import the rest of the package without it.
"""

from __future__ import annotations

import logging

from desk_assistant.app import Assistant
from desk_assistant.config import Config

logger = logging.getLogger(__name__)


class GpioInput:
    def __init__(self, config: Config, assistant: Assistant) -> None:
        self.config = config
        self.assistant = assistant
        self._buttons: list = []

    def start(self) -> None:
        try:
            from gpiozero import Button
        except ImportError as exc:
            raise RuntimeError(
                "gpiozero is not installed. On Raspberry Pi OS, install python3-gpiozero "
                "and python3-lgpio, or set input_backend to keyboard."
            ) from exc

        if self.config.ptt.gpio is not None:
            talk = Button(self.config.ptt.gpio, pull_up=True, bounce_time=0.05)
            talk.when_pressed = self.assistant.enqueue_ptt_down
            talk.when_released = self.assistant.enqueue_ptt_up
            self._buttons.append(talk)
            logger.info("Push-to-talk on BCM %s", self.config.ptt.gpio)

        for spec in self.config.buttons:
            if spec.gpio is None:
                continue
            button = Button(spec.gpio, pull_up=True, bounce_time=0.05)
            button.when_pressed = _preset(self.assistant, spec.id)
            self._buttons.append(button)
            logger.info("Button %s on BCM %s", spec.id, spec.gpio)

    def stop(self) -> None:
        for button in self._buttons:
            button.close()
        self._buttons.clear()


def _preset(assistant: Assistant, button_id: str):
    def pressed() -> None:
        assistant.enqueue_button(button_id, "button")

    return pressed
