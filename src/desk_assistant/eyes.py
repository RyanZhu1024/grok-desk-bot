"""Haro's eyes: three APA102 LEDs on the ReSpeaker 2-Mics HAT, or a console stand-in.

The HAT wires the LEDs to SPI bus 0, device 1. Laptop test mode has no SPI
device, so it prints the mood instead of opening a screen.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MOOD_DIM = "dim"
MOOD_GREEN = "green"
MOOD_PULSING = "pulsing"
MOOD_BRIGHT = "bright"
MOOD_RED = "red"


class ConsoleEyes:
    """Prints a mood when it changes. `history` is what the tests read."""

    def __init__(self) -> None:
        self.mood = ""
        self.history: list[str] = []

    def set_mood(self, mood: str) -> None:
        if mood == self.mood:
            return
        self.mood = mood
        self.history.append(mood)
        print(f"[haro] eyes: {mood}", flush=True)

    def pulse(self, phase: float) -> None:
        return None

    def close(self) -> None:
        return None


class Apa102Eyes:
    """Three APA102 RGB LEDs driven as one expression.

    SPI is opened lazily. If the device is missing, the eyes stay quiet and
    the voice loop keeps running.
    """

    def __init__(self, count: int = 3, bus: int = 0, device: int = 1) -> None:
        self.count = max(1, count)
        self._spi = None
        self.mood = ""
        try:
            import spidev

            spi = spidev.SpiDev()
            spi.open(bus, device)
            spi.max_speed_hz = 8_000_000
            self._spi = spi
        except Exception as exc:
            logger.warning("APA102 LEDs are unavailable (%s). Eyes will stay off.", exc)

    def set_mood(self, mood: str) -> None:
        self.mood = mood
        self._paint(1.0)

    def pulse(self, phase: float) -> None:
        if self.mood != MOOD_PULSING or self._spi is None:
            return
        import math

        self._paint(0.35 + 0.65 * abs(math.sin(phase * 3)))

    def close(self) -> None:
        if self._spi is None:
            return
        try:
            self._write(0, 0, 0, 0)
        except Exception:
            logger.debug("failed to blank APA102s", exc_info=True)
        try:
            self._spi.close()
        except Exception:
            logger.debug("failed to close SPI", exc_info=True)
        self._spi = None

    def _paint(self, level: float) -> None:
        amount = max(0.0, min(1.0, level))
        if self.mood == MOOD_GREEN:
            self._write(0, int(140 * amount), int(40 * amount), 14)
        elif self.mood == MOOD_RED:
            self._write(int(160 * amount), 0, 0, 16)
        elif self.mood == MOOD_BRIGHT:
            value = int(200 * amount)
            self._write(value, int(value * 0.95), int(value * 0.8), 28)
        elif self.mood == MOOD_PULSING:
            value = int(40 + 140 * amount)
            self._write(value, int(value * 0.55), 0, 8 + int(16 * amount))
        else:
            self._write(28, 22, 12, 4)

    def _write(self, red: int, green: int, blue: int, brightness: int) -> None:
        if self._spi is None:
            return
        bright = max(0, min(31, brightness))
        start = 0xE0 | bright
        frame = [0, 0, 0, 0]
        pixel = [start, blue & 0xFF, green & 0xFF, red & 0xFF]
        for _ in range(self.count):
            frame.extend(pixel)
        frame.extend([0xFF, 0xFF, 0xFF, 0xFF])
        self._spi.xfer2(frame)
