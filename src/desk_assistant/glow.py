"""Eye animations for the two LEDs behind Haro's shell.

The frames are plain colors. Hardware and the laptop console paint whatever
this module returns, so the patterns can be tested without a Pi.
"""

from __future__ import annotations

import math
import re

Color = tuple[int, int, int]

DEFAULT_MOOD = "happy"

# Slytherin emerald is the resting green. The other moods only tint the
# animation; error and low-battery keep their own colors.
MOOD_COLORS: dict[str, Color] = {
    "happy": (30, 170, 55),
    "calm": (0, 150, 155),
    "unsure": (210, 120, 0),
    "excited": (210, 255, 190),
    "sad": (15, 30, 70),
}

ERROR_COLOR: Color = (210, 0, 0)
LOW_BATTERY_COLOR: Color = (210, 120, 0)

IDLE_PERIOD = 4.8
LISTEN_PERIOD = 1.5
THINKING_PERIOD = 2.4
REPLY_FLASH_SECONDS = 0.64
LOW_BATTERY_PERIOD = 1.2
LOW_BATTERY_ON = 0.16

_MOOD_LINE = re.compile(r"^\[mood:([a-zA-Z]+)\]$")
_MOOD_PREFIX = re.compile(r"^\[mood:([a-zA-Z]+)\]\s*")
_FLASH_ON = ((0.00, 0.10), (0.20, 0.32))


def split_reply(text: str) -> tuple[str | None, str]:
    """Split an optional leading ``[mood:name]`` from the words to speak.

    The tag is either the whole first line or a prefix of a one-line reply.
    A known mood is returned so the eyes can tint. An unknown name is still
    removed, and the previous mood stays in place.
    """
    raw = (text or "").strip()
    if not raw:
        return None, ""
    lines = raw.splitlines()
    first = lines[0].strip()
    match = _MOOD_LINE.fullmatch(first)
    if match:
        rest = "\n".join(line.strip() for line in lines[1:]).strip()
        return _known(match.group(1)), rest
    match = _MOOD_PREFIX.match(raw)
    if match:
        return _known(match.group(1)), raw[match.end() :].strip()
    return None, raw


def normalize_mood(name: str | None) -> str:
    if name and name.strip().lower() in MOOD_COLORS:
        return name.strip().lower()
    return DEFAULT_MOOD


def eye_frame(
    state: str,
    mood: str,
    t: float,
    amplitude: float | None = None,
) -> tuple[Color, Color]:
    """One moment of the two eyes, ``(left, right)``.

    ``t`` is seconds. For ``reply-arrived`` it is seconds since the flash
    started. ``amplitude`` is 0..1 from the TTS wav while speaking; without
    it, speaking uses a rhythmic flicker.
    """
    color = MOOD_COLORS.get(normalize_mood(mood), MOOD_COLORS[DEFAULT_MOOD])
    if state == "error":
        return ERROR_COLOR, ERROR_COLOR
    if state == "low-battery":
        level = 1.0 if (t % LOW_BATTERY_PERIOD) < LOW_BATTERY_ON else 0.0
        pixel = _scale(LOW_BATTERY_COLOR, level)
        return pixel, pixel
    if state == "reply-arrived":
        pixel = _scale(color, _flash_level(t))
        return pixel, pixel
    if state == "thinking":
        left, right = _alternate(t)
        return _scale(color, left), _scale(color, right)
    if state == "speaking":
        pixel = _scale(color, _speak_level(t, amplitude))
        return pixel, pixel
    if state == "listening":
        pixel = _scale(color, 0.48 + 0.16 * math.sin(2 * math.pi * t / LISTEN_PERIOD))
        return pixel, pixel
    # idle, and any unknown name, breathes dimly.
    phase = math.cos(2 * math.pi * t / IDLE_PERIOD)
    level = 0.06 + 0.16 * (0.5 - 0.5 * phase)
    pixel = _scale(color, level)
    return pixel, pixel


def _known(name: str) -> str | None:
    key = name.strip().lower()
    if key in MOOD_COLORS:
        return key
    return None


def _scale(color: Color, level: float) -> Color:
    amount = max(0.0, min(1.0, level))
    return tuple(max(0, min(255, int(round(channel * amount)))) for channel in color)


def _flash_level(t: float) -> float:
    if t < 0:
        return 0.0
    for start, end in _FLASH_ON:
        if start <= t < end:
            return 1.0
    return 0.0


def _alternate(t: float) -> tuple[float, float]:
    phase = 2 * math.pi * t / THINKING_PERIOD
    left = 0.12 + 0.88 * (0.5 + 0.5 * math.sin(phase))
    right = 0.12 + 0.88 * (0.5 + 0.5 * math.sin(phase + math.pi))
    return left, right


def _speak_level(t: float, amplitude: float | None) -> float:
    if amplitude is not None:
        return 0.12 + 0.88 * max(0.0, min(1.0, amplitude))
    primary = abs(math.sin(t * 17.0))
    secondary = abs(math.sin(t * 7.3 + 0.6))
    return 0.25 + 0.75 * (0.65 * primary + 0.35 * secondary)
