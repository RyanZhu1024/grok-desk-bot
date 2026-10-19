"""Fullscreen status screen.

The layout is drawn for a 1280×720 landscape desktop, which is the 7-inch
Touch Display 2 (native 720×1280) after it is rotated. Every margin, row,
and font size scales with the surface, so the same screen still fits an
800×480 window. Touch is a mouse event: on the Pi that event comes from
the touchscreen, and on a laptop it comes from the pointer. Keyboard
handling lives in the runtime; this module only draws and reports which
control was touched.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import pygame

from desk_assistant.models import Snapshot, State

BG = (22, 20, 17)
INK = (243, 236, 223)
MUTED = (176, 166, 150)
DIM = (124, 116, 104)
CARD = (44, 39, 34)
LINE = (72, 64, 54)
SAGE = (156, 186, 134)
AMBER = (224, 162, 74)
TEAL = (111, 191, 180)
CORAL = (224, 138, 114)
RED = (212, 106, 98)
BANNER = (92, 64, 28)

STATE_COLOR = {
    State.IDLE: SAGE,
    State.LISTENING: AMBER,
    State.TRANSCRIBING: AMBER,
    State.THINKING: TEAL,
    State.SPEAKING: CORAL,
    State.ERROR: RED,
}

STATE_LABEL = {
    State.IDLE: "Idle",
    State.LISTENING: "Listening",
    State.TRANSCRIBING: "Transcribing",
    State.THINKING: "Thinking",
    State.SPEAKING: "Speaking",
    State.ERROR: "Problem",
}

# Design size: Touch Display 2 rotated to landscape.
DESIGN_WIDTH = 1280
DESIGN_HEIGHT = 720


@dataclass
class _Layout:
    width: int
    height: int
    scale: float
    banner: pygame.Rect | None
    header: pygame.Rect
    state: pygame.Rect
    request: pygame.Rect
    reply: pygame.Rect
    notification: pygame.Rect | None
    buttons: list[tuple[pygame.Rect, str, str, str]]
    help: pygame.Rect | None


def scale_for(size: tuple[int, int]) -> float:
    """Scale factor from the 1280×720 design to `size`.

    Uses the smaller axis so a 1280×720 surface stays 1:1 and an 800×480
    surface shrinks instead of clipping.
    """
    width, height = size
    if width <= 0 or height <= 0:
        return 1.0
    return min(width / DESIGN_WIDTH, height / DESIGN_HEIGHT)


class DeskDisplay:
    def __init__(self) -> None:
        self.typing = False
        self.buffer = ""
        self._scale = 1.0
        self._hits: list[tuple[pygame.Rect, str]] = []
        self._fonts: dict[tuple[str, int, bool], pygame.font.Font] = {}

    def _px(self, design: int) -> int:
        return max(1, round(design * self._scale))

    def hit(self, pos: tuple[int, int]) -> str | None:
        for rect, ident in self._hits:
            if rect.collidepoint(pos):
                return ident
        return None

    def render(self, surface: pygame.Surface, snap: Snapshot) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
        self._scale = scale_for(surface.get_size())
        layout = self._layout(surface.get_size(), snap)
        self._hits = [(rect, ident) for rect, ident, _label, _hint in layout.buttons]
        surface.fill(BG)
        if layout.banner is not None:
            self._banner(surface, layout.banner, snap)
        self._header(surface, layout.header, snap)
        self._state(surface, layout.state, snap)
        self._card(
            surface,
            layout.request,
            "Last request",
            snap.last_request or "Waiting for the first request.",
            snap.last_source.upper() if snap.last_request else "",
            ink=INK if snap.last_request else DIM,
        )
        reply_text, reply_ink, reply_kicker = self._reply_copy(snap)
        self._card(
            surface,
            layout.reply,
            reply_kicker,
            reply_text,
            "",
            ink=reply_ink,
            accent=STATE_COLOR[snap.state] if snap.state is State.ERROR and snap.error else None,
        )
        if layout.notification is not None and snap.notification:
            self._notification(surface, layout.notification, snap.notification)
        for rect, ident, label, hint in layout.buttons:
            self._button(surface, rect, ident, label, hint, snap)
        if layout.help is not None:
            self._help(surface, layout.help, snap)
        if self.typing:
            self._composer(surface, snap)

    def _layout(self, size: tuple[int, int], snap: Snapshot) -> _Layout:
        width, height = size
        margin = self._px(28)
        gap = self._px(12)
        banner_h = self._px(36) if snap.test_mode else 0
        help_h = self._px(24) if snap.test_mode else 0
        notif_h = self._px(44) if snap.notification else 0
        buttons_h = self._px(92)
        y = banner_h + self._px(6)
        header = pygame.Rect(margin, y, width - 2 * margin, self._px(48))
        state = pygame.Rect(margin, header.bottom + self._px(4), width - 2 * margin, self._px(96))
        request = pygame.Rect(margin, state.bottom + gap, width - 2 * margin, self._px(112))
        reply_top = request.bottom + gap

        bottom = height - margin
        help_rect = None
        if help_h:
            help_rect = pygame.Rect(margin, bottom - help_h, width - 2 * margin, help_h)
            bottom = help_rect.top - self._px(4)
        button_row = pygame.Rect(margin, bottom - buttons_h, width - 2 * margin, buttons_h)
        bottom = button_row.top - gap
        notification = None
        if notif_h:
            notification = pygame.Rect(margin, bottom - notif_h, width - 2 * margin, notif_h)
            bottom = notification.top - gap
        reply = pygame.Rect(margin, reply_top, width - 2 * margin, max(1, bottom - reply_top))
        banner = pygame.Rect(0, 0, width, banner_h) if banner_h else None
        controls = self._button_rects(button_row, snap)
        return _Layout(
            width,
            height,
            self._scale,
            banner,
            header,
            state,
            request,
            reply,
            notification,
            controls,
            help_rect,
        )

    def _button_rects(self, row: pygame.Rect, snap: Snapshot) -> list[tuple[pygame.Rect, str, str, str]]:
        specs = [("ptt", "Talk", "space")]
        for button in snap.buttons:
            specs.append((button.id, button.label, button.key))
        weights = [1.35] + [1.0] * (len(specs) - 1)
        total = sum(weights)
        gap = self._px(12)
        usable = row.width - gap * (len(specs) - 1)
        x = row.x
        rects = []
        for index, (ident, label, hint) in enumerate(specs):
            width = int(usable * weights[index] / total)
            if index == len(specs) - 1:
                width = row.right - x
            rects.append((pygame.Rect(x, row.y, width, row.height), ident, label, hint))
            x += width + gap
        return rects

    def _banner(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        pygame.draw.rect(surface, BANNER, rect)
        font = self._font(20, bold=True)
        label = font.render("TEST MODE", True, AMBER)
        note_text = snap.test_note or "mock assistant on this computer"
        note = self._font(20).render(note_text, True, INK)
        pad = self._px(28)
        surface.blit(label, (pad, rect.centery - label.get_height() // 2))
        surface.blit(note, (pad + label.get_width() + self._px(14), rect.centery - note.get_height() // 2))

    def _header(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        word = self._font(28, bold=True).render("DESK", True, INK)
        sub = self._font(18).render("ASSISTANT", True, DIM)
        surface.blit(word, (rect.x, rect.y + self._px(4)))
        surface.blit(sub, (rect.x + word.get_width() + self._px(12), rect.y + self._px(12)))
        clock = self._font(24).render(time.strftime("%H:%M"), True, MUTED)
        surface.blit(clock, (rect.right - clock.get_width(), rect.y + self._px(8)))
        link = "live" if snap.ntfy_connected else "offline"
        color = SAGE if snap.ntfy_connected else RED
        link_label = self._font(18).render(link, True, color)
        dot_x = rect.right - clock.get_width() - self._px(28) - link_label.get_width()
        pygame.draw.circle(surface, color, (dot_x, rect.y + self._px(22)), self._px(6))
        surface.blit(link_label, (dot_x + self._px(14), rect.y + self._px(10)))

    def _state(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        color = STATE_COLOR[snap.state]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * (4 if snap.state is State.LISTENING else 2))
        radius = self._px(26 if snap.state is State.LISTENING else 22)
        center = (rect.x + self._px(32), rect.y + self._px(40))
        glow = _mix(color, BG, 0.55 if snap.state is State.IDLE else 0.35 + 0.15 * pulse)
        pygame.draw.circle(surface, glow, center, radius + self._px(12))
        pygame.draw.circle(surface, color, center, radius)
        if snap.state is State.THINKING:
            self._dots(surface, (rect.right - self._px(48), rect.centery))
        elif snap.state is State.SPEAKING:
            self._bars(surface, (rect.right - self._px(80), rect.centery), color)
        title = self._font(52, bold=True).render(STATE_LABEL[snap.state], True, INK)
        title_x = rect.x + self._px(78)
        surface.blit(title, (title_x, rect.y + self._px(4)))
        sub = snap.notice or snap.substatus
        sub_color = AMBER if snap.notice else MUTED
        sub_img = self._font(24).render(sub, True, sub_color)
        surface.blit(sub_img, (title_x, rect.y + self._px(58)))

    def _card(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        kicker: str,
        body: str,
        badge: str,
        *,
        ink: tuple[int, int, int],
        accent: tuple[int, int, int] | None = None,
    ) -> None:
        radius = self._px(16)
        pad = self._px(20)
        pygame.draw.rect(surface, CARD, rect, border_radius=radius)
        pygame.draw.rect(surface, accent or LINE, rect, width=max(1, self._px(2)), border_radius=radius)
        label = self._font(16, bold=True).render(kicker.upper(), True, DIM)
        surface.blit(label, (rect.x + pad, rect.y + self._px(12)))
        if badge:
            badge_img = self._font(16, bold=True).render(badge, True, MUTED)
            surface.blit(badge_img, (rect.right - pad - badge_img.get_width(), rect.y + self._px(12)))
        text_rect = pygame.Rect(
            rect.x + pad,
            rect.y + self._px(40),
            rect.width - 2 * pad,
            rect.height - self._px(52),
        )
        self._draw_wrapped(surface, body, self._font(26), ink, text_rect)

    def _notification(self, surface: pygame.Surface, rect: pygame.Rect, text: str) -> None:
        pygame.draw.rect(surface, (36, 40, 38), rect, border_radius=self._px(12))
        kicker = self._font(16, bold=True).render("NOTIFICATION", True, TEAL)
        surface.blit(kicker, (rect.x + self._px(16), rect.centery - kicker.get_height() // 2))
        remain = pygame.Rect(
            rect.x + self._px(24) + kicker.get_width(),
            rect.y,
            rect.width - self._px(40) - kicker.get_width(),
            rect.height,
        )
        self._draw_wrapped(surface, text, self._font(22), INK, remain, max_lines=1, valign=True)

    def _button(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        ident: str,
        label: str,
        hint: str,
        snap: Snapshot,
    ) -> None:
        active = snap.active_control == ident or (ident == "ptt" and snap.state is State.LISTENING)
        fill = _mix(AMBER, CARD, 0.25) if active else CARD
        border = AMBER if active else LINE
        radius = self._px(14)
        pygame.draw.rect(surface, fill, rect, border_radius=radius)
        pygame.draw.rect(surface, border, rect, width=max(1, self._px(2)), border_radius=radius)
        title = self._font(24, bold=True).render(label, True, INK if active else MUTED)
        if snap.test_mode and hint:
            sub = self._font(16).render(hint, True, DIM)
            block = title.get_height() + sub.get_height()
            surface.blit(title, (rect.centerx - title.get_width() // 2, rect.centery - block // 2))
            surface.blit(sub, (rect.centerx - sub.get_width() // 2, rect.centery - block // 2 + title.get_height()))
        else:
            surface.blit(title, (rect.centerx - title.get_width() // 2, rect.centery - title.get_height() // 2))

    def _help(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        keys = [button.key for button in snap.buttons if button.key]
        if len(keys) >= 2 and all(len(key) == 1 and key.isdigit() for key in keys):
            preset = f"{keys[0]}–{keys[-1]} presets"
        elif keys:
            preset = "presets " + ", ".join(keys)
        else:
            preset = "on-screen presets"
        parts = ["Hold Space to talk", preset, "T to type", "Esc quits"]
        if snap.footer:
            parts.append(snap.footer)
        img = self._font(18).render("  ·  ".join(parts), True, DIM)
        surface.blit(img, (rect.x, rect.y))

    def _composer(self, surface: pygame.Surface, snap: Snapshot) -> None:
        width, height = surface.get_size()
        pad = self._px(28)
        rect = pygame.Rect(pad, height - self._px(110), width - 2 * pad, self._px(84))
        radius = self._px(14)
        pygame.draw.rect(surface, (18, 16, 14), rect, border_radius=radius)
        pygame.draw.rect(surface, AMBER, rect, width=max(1, self._px(3)), border_radius=radius)
        hint = self._font(16, bold=True).render("TYPE A REQUEST", True, AMBER)
        surface.blit(hint, (rect.x + self._px(18), rect.y + self._px(10)))
        shown = self.buffer or "What should I ask?"
        color = INK if self.buffer else DIM
        img = self._font(24).render(shown[-80:], True, color)
        surface.blit(img, (rect.x + self._px(18), rect.y + self._px(40)))

    def _dots(self, surface: pygame.Surface, center: tuple[int, int]) -> None:
        phase = int(time.monotonic() * 3) % 3
        step = self._px(16)
        radius = self._px(5)
        for index in range(3):
            color = INK if index == phase else _mix(INK, TEAL, 0.65)
            pygame.draw.circle(surface, color, (center[0] - step + index * step, center[1]), radius)

    def _bars(self, surface: pygame.Surface, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
        for index in range(4):
            height = self._px(10) + int(self._px(22) * abs(math.sin(time.monotonic() * 7 + index * 0.7)))
            rect = pygame.Rect(origin[0] + index * self._px(12), origin[1] - height // 2, self._px(6), height)
            pygame.draw.rect(surface, color, rect, border_radius=self._px(3))

    def _draw_wrapped(
        self,
        surface: pygame.Surface,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        rect: pygame.Rect,
        max_lines: int | None = None,
        valign: bool = False,
    ) -> None:
        line_h = font.get_linesize()
        limit = max_lines or max(1, rect.height // max(line_h, 1))
        lines = _wrap(text, font, rect.width)
        if len(lines) > limit:
            lines = lines[:limit]
            lines[-1] = _ellipsis(lines[-1], font, rect.width)
        y = rect.y
        if valign and lines:
            block = line_h * len(lines)
            y = rect.y + max(0, (rect.height - block) // 2)
        for line in lines:
            img = font.render(line, True, color)
            surface.blit(img, (rect.x, y))
            y += line_h
            if y > rect.bottom:
                break

    def _reply_copy(self, snap: Snapshot) -> tuple[str, tuple[int, int, int], str]:
        if snap.state is State.ERROR and snap.error:
            return snap.error, INK, "What happened"
        if snap.last_reply:
            return snap.last_reply, INK, "Reply"
        if snap.state is State.THINKING:
            return "The request was sent. The reply will show up here.", MUTED, "Reply"
        return "The reply will show up here.", DIM, "Reply"

    def _font(self, design_size: int, bold: bool = False) -> pygame.font.Font:
        size = max(11, round(design_size * self._scale))
        key = ("dejavusans", size, bold)
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        font = pygame.font.SysFont("dejavusans", size, bold=bold)
        self._fonts[key] = font
        return font


def _wrap(text: str, font: pygame.font.Font, width: int) -> list[str]:
    paragraphs = text.split("\n")
    lines: list[str] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if font.size(trial)[0] <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def _ellipsis(line: str, font: pygame.font.Font, width: int) -> str:
    if font.size(line)[0] <= width:
        candidate = line + "…"
        if font.size(candidate)[0] <= width:
            return candidate
    trimmed = line
    while trimmed and font.size(trimmed + "…")[0] > width:
        trimmed = trimmed[:-1]
    return (trimmed or "").rstrip() + "…"


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))  # type: ignore[return-value]
