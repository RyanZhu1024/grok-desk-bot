"""Haro's lights: three APA102s on the ReSpeaker HAT, plus two eye LEDs.

The HAT LEDs mirror the voice state (dim, green, pulsing, bright, red).
The two LEDs behind the shell run the glow animations. They are either
WS2812B pixels on a GPIO pin, driven by rpi_ws281x, or APA102s chained
after the HAT on the same SPI device.

Laptop test mode has no SPI device, so it prints the state instead.
"""

from __future__ import annotations

import logging

from desk_assistant.glow import REPLY_FLASH_SECONDS, eye_frame

logger = logging.getLogger(__name__)

MOOD_DIM = "dim"
MOOD_GREEN = "green"
MOOD_PULSING = "pulsing"
MOOD_BRIGHT = "bright"
MOOD_RED = "red"


def ws281x_channel(pin: int) -> int:
    """PWM channel for rpi_ws281x. GPIO 13 and 19 are channel 1."""
    if pin in {13, 19, 41, 45, 53}:
        return 1
    return 0


def apa102_frame(
    hat: list[tuple[int, int, int, int]],
    eyes: list[tuple[int, int, int]],
) -> list[int]:
    """SPI bytes for onboard APA102s followed by chained eye pixels.

    Each pixel is brightness, then blue, green, red. The eyes use full
    LED brightness because the animation already scales the color.
    """
    frame = [0, 0, 0, 0]
    for red, green, blue, brightness in hat:
        bright = max(0, min(31, brightness))
        frame.extend([0xE0 | bright, blue & 0xFF, green & 0xFF, red & 0xFF])
    for red, green, blue in eyes:
        frame.extend([0xE0 | 31, blue & 0xFF, green & 0xFF, red & 0xFF])
    count = len(hat) + len(eyes)
    frame.extend([0xFF] * max(4, (count + 15) // 16))
    return frame


class ConsoleEyes:
    """Prints a HAT mood when it changes. `history` is what the tests read."""

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


class ConsoleGlow:
    """Remembers the latest eye pixels. The rig prints when the state changes."""

    def __init__(self) -> None:
        self.left = (0, 0, 0)
        self.right = (0, 0, 0)

    def show(self, left: tuple[int, int, int], right: tuple[int, int, int]) -> None:
        self.left = left
        self.right = right

    def close(self) -> None:
        return None


class Apa102Eyes:
    """APA102s on the ReSpeaker SPI bus.

    ``eye_count`` is 0 when the shell eyes are WS2812s. When they are
    APA102s, those pixels are written after the HAT pixels on this same
    device.
    """

    def __init__(self, count: int = 3, bus: int = 0, device: int = 1, eye_count: int = 0) -> None:
        self.count = max(1, count)
        self.eye_count = max(0, eye_count)
        self.mood = ""
        self._hat = (28, 22, 12, 4)
        self._eye_pixels: list[tuple[int, int, int]] = [(0, 0, 0)] * self.eye_count
        self._spi = _open_spi(bus, device)

    def set_mood(self, mood: str) -> None:
        self.mood = mood
        self._paint(1.0)

    def pulse(self, phase: float) -> None:
        if self.mood != MOOD_PULSING or self._spi is None:
            return
        import math

        self._paint(0.35 + 0.65 * abs(math.sin(phase * 3)))

    def show(self, left: tuple[int, int, int], right: tuple[int, int, int]) -> None:
        if self.eye_count <= 0:
            return
        pixels = [left, right]
        self._eye_pixels = [pixels[index] if index < len(pixels) else (0, 0, 0) for index in range(self.eye_count)]
        self._flush()

    def close(self) -> None:
        if self._spi is None:
            return
        self._hat = (0, 0, 0, 0)
        self._eye_pixels = [(0, 0, 0)] * self.eye_count
        try:
            self._flush()
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
            self._hat = (0, int(140 * amount), int(40 * amount), 14)
        elif self.mood == MOOD_RED:
            self._hat = (int(160 * amount), 0, 0, 16)
        elif self.mood == MOOD_BRIGHT:
            value = int(200 * amount)
            self._hat = (value, int(value * 0.95), int(value * 0.8), 28)
        elif self.mood == MOOD_PULSING:
            value = int(40 + 140 * amount)
            self._hat = (value, int(value * 0.55), 0, 8 + int(16 * amount))
        else:
            self._hat = (28, 22, 12, 4)
        self._flush()

    def _flush(self) -> None:
        if self._spi is None:
            return
        frame = apa102_frame([self._hat] * self.count, self._eye_pixels)
        try:
            self._spi.xfer2(frame)
        except Exception:
            logger.debug("APA102 write failed", exc_info=True)


class Ws2812Eyes:
    """Two WS2812B LEDs on a GPIO pin, timed by rpi_ws281x hardware PWM.

    GPIO 12 is PWM channel 0, which is free on the ReSpeaker HAT and does
    not bit-bang the Pi Zero 2 W. A missing library or permission leaves
    the eyes off and the voice loop running.
    """

    def __init__(self, pin: int = 12, count: int = 2) -> None:
        self.pin = pin
        self.count = max(1, count)
        self._strip = None
        self._color = None
        try:
            from rpi_ws281x import Color, PixelStrip

            strip = PixelStrip(
                self.count,
                pin,
                800_000,
                10,
                False,
                255,
                ws281x_channel(pin),
            )
            strip.begin()
            self._strip = strip
            self._color = Color
        except Exception as exc:
            logger.warning(
                "WS2812 eyes on GPIO%s are unavailable (%s). External eyes stay off.",
                pin,
                exc,
            )

    def show(self, left: tuple[int, int, int], right: tuple[int, int, int]) -> None:
        if self._strip is None or self._color is None:
            return
        pixels = (left, right)
        for index in range(self._strip.numPixels()):
            red, green, blue = pixels[index] if index < len(pixels) else (0, 0, 0)
            self._strip.setPixelColor(index, self._color(red, green, blue))
        self._strip.show()

    def close(self) -> None:
        if self._strip is None or self._color is None:
            return
        try:
            for index in range(self._strip.numPixels()):
                self._strip.setPixelColor(index, self._color(0, 0, 0))
            self._strip.show()
        except Exception:
            logger.debug("failed to blank WS2812s", exc_info=True)
        self._strip = None
        self._color = None


class HaroLights:
    """HAT mirror plus the two glowing eyes.

    ``hat`` and ``glow`` are the same object when the eyes are chained
    APA102s. ``brightness`` scales the eye pixels only.
    """

    def __init__(self, hat, glow, brightness: float = 1.0) -> None:
        self.hat = hat
        self.glow = glow
        self.brightness = max(0.0, min(1.0, brightness))
        self.glow_state = "idle"
        self.color_mood = "happy"
        self.glow_history: list[str] = []
        self._flash_started: float | None = None
        self._shown: tuple[str, str] | None = None

    @property
    def mood(self) -> str:
        return self.hat.mood

    @property
    def history(self) -> list[str]:
        return self.hat.history

    def set_mood(self, mood: str) -> None:
        self.hat.set_mood(mood)

    def pulse(self, phase: float) -> None:
        self.hat.pulse(phase)

    def set_glow(self, state: str, color_mood: str) -> None:
        self.glow_state = state
        self.color_mood = color_mood or self.color_mood
        self._note(state)

    def flash_reply(self) -> None:
        import time

        self._flash_started = time.monotonic()
        self._note("reply-arrived")

    def render(self, now: float, amplitude: float | None = None) -> None:
        if getattr(self.hat, "mood", "") == MOOD_PULSING:
            self.hat.pulse(now)
        state = self._visible(now)
        if state == "reply-arrived" and self._flash_started is not None:
            moment = now - self._flash_started
        else:
            moment = now
        left, right = eye_frame(state, self.color_mood, moment, amplitude)
        if self.brightness != 1.0:
            left = _dim(left, self.brightness)
            right = _dim(right, self.brightness)
        self.glow.show(left, right)

    def close(self) -> None:
        try:
            self.glow.close()
        finally:
            if self.glow is not self.hat:
                self.hat.close()

    def _visible(self, now: float) -> str:
        if self._flash_started is not None and 0 <= now - self._flash_started < REPLY_FLASH_SECONDS:
            return "reply-arrived"
        return self.glow_state

    def _note(self, state: str) -> None:
        signature = (state, self.color_mood)
        if signature == self._shown:
            return
        self._shown = signature
        self.glow_history.append(state)
        sample_t = 0.05 if state == "reply-arrived" else 0.0
        left, right = eye_frame(state, self.color_mood, sample_t, None)
        print(
            f"[haro] glow: {state} ({self.color_mood}) "
            f"L={left[0]},{left[1]},{left[2]} R={right[0]},{right[1]},{right[2]}",
            flush=True,
        )


def _dim(color: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(channel * brightness)))) for channel in color)


def _open_spi(bus: int, device: int):
    try:
        import spidev

        spi = spidev.SpiDev()
        spi.open(bus, device)
        spi.max_speed_hz = 8_000_000
        return spi
    except Exception as exc:
        logger.warning("APA102 LEDs are unavailable (%s). Eyes will stay off.", exc)
        return None
