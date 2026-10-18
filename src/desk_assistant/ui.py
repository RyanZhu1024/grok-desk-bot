"""Fullscreen status screen for the 3.5–5 inch touch panel.

The same drawing path runs in a window on a laptop. Touch is just a mouse
event, so on-screen buttons work on both. Keyboard handling lives in the
runtime; this module only draws and reports which control was touched.
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


@dataclass
class _Layout:
    width: int
    height: int
    banner: pygame.Rect | None
    header: pygame.Rect
    state: pygame.Rect
    request: pygame.Rect
    reply: pygame.Rect
    notification: pygame.Rect | None
    buttons: list[tuple[pygame.Rect, str, str, str]]
    help: pygame.Rect | None


class DeskDisplay:
    def __init__(self) -> None:
        self.typing = False
        self.buffer = ""
        self._hits: list[tuple[pygame.Rect, str]] = []
        self._fonts: dict[tuple[str, int, bool], pygame.font.Font] = {}

    def hit(self, pos: tuple[int, int]) -> str | None:
        for rect, ident in self._hits:
            if rect.collidepoint(pos):
                return ident
        return None

    def render(self, surface: pygame.Surface, snap: Snapshot) -> None:
        if not pygame.font.get_init():
            pygame.font.init()
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
        margin = 14
        gap = 8
        banner_h = 24 if snap.test_mode else 0
        help_h = 16 if snap.test_mode else 0
        notif_h = 30 if snap.notification else 0
        buttons_h = 54
        y = banner_h + 4
        header = pygame.Rect(margin, y, width - 2 * margin, 28)
        state = pygame.Rect(margin, header.bottom + 2, width - 2 * margin, 56)
        request = pygame.Rect(margin, state.bottom + gap, width - 2 * margin, 68)
        reply_top = request.bottom + gap

        bottom = height - margin
        help_rect = None
        if help_h:
            help_rect = pygame.Rect(margin, bottom - help_h, width - 2 * margin, help_h)
            bottom = help_rect.top - 2
        button_row = pygame.Rect(margin, bottom - buttons_h, width - 2 * margin, buttons_h)
        bottom = button_row.top - gap
        notification = None
        if notif_h:
            notification = pygame.Rect(margin, bottom - notif_h, width - 2 * margin, notif_h)
            bottom = notification.top - gap
        reply = pygame.Rect(margin, reply_top, width - 2 * margin, max(64, bottom - reply_top))
        banner = pygame.Rect(0, 0, width, banner_h) if banner_h else None
        controls = self._button_rects(button_row, snap)
        return _Layout(width, height, banner, header, state, request, reply, notification, controls, help_rect)

    def _button_rects(self, row: pygame.Rect, snap: Snapshot) -> list[tuple[pygame.Rect, str, str, str]]:
        specs = [("ptt", "Talk", "space")]
        for button in snap.buttons:
            specs.append((button.id, button.label, button.key))
        weights = [1.35] + [1.0] * (len(specs) - 1)
        total = sum(weights)
        gap = 8
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
        font = self._font(13, bold=True)
        label = font.render("TEST MODE", True, AMBER)
        note_text = snap.test_note or "mock assistant on this computer"
        note = self._font(13).render(note_text, True, INK)
        surface.blit(label, (14, rect.centery - label.get_height() // 2))
        surface.blit(note, (14 + label.get_width() + 10, rect.centery - note.get_height() // 2))

    def _header(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        word = self._font(18, bold=True).render("DESK", True, INK)
        sub = self._font(13).render("ASSISTANT", True, DIM)
        surface.blit(word, (rect.x, rect.y + 2))
        surface.blit(sub, (rect.x + word.get_width() + 8, rect.y + 7))
        clock = self._font(16).render(time.strftime("%H:%M"), True, MUTED)
        surface.blit(clock, (rect.right - clock.get_width(), rect.y + 4))
        link = "live" if snap.ntfy_connected else "offline"
        color = SAGE if snap.ntfy_connected else RED
        link_label = self._font(13).render(link, True, color)
        dot_x = rect.right - clock.get_width() - 18 - link_label.get_width()
        pygame.draw.circle(surface, color, (dot_x, rect.y + 14), 4)
        surface.blit(link_label, (dot_x + 10, rect.y + 6))

    def _state(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        color = STATE_COLOR[snap.state]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * (4 if snap.state is State.LISTENING else 2))
        radius = 16 if snap.state is State.LISTENING else 14
        center = (rect.x + 18, rect.y + 24)
        glow = _mix(color, BG, 0.55 if snap.state is State.IDLE else 0.35 + 0.15 * pulse)
        pygame.draw.circle(surface, glow, center, radius + 7)
        pygame.draw.circle(surface, color, center, radius)
        if snap.state is State.THINKING:
            self._dots(surface, (rect.right - 28, rect.y + 22))
        elif snap.state is State.SPEAKING:
            self._bars(surface, (rect.right - 46, rect.y + 22), color)
        title = self._font(34, bold=True).render(STATE_LABEL[snap.state], True, INK)
        title_x = rect.x + 46
        surface.blit(title, (title_x, rect.y + 2))
        sub = snap.notice or snap.substatus
        sub_color = AMBER if snap.notice else MUTED
        sub_img = self._font(16).render(sub, True, sub_color)
        surface.blit(sub_img, (title_x, rect.y + 38))

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
        pygame.draw.rect(surface, CARD, rect, border_radius=12)
        pygame.draw.rect(surface, accent or LINE, rect, width=1, border_radius=12)
        label = self._font(12, bold=True).render(kicker.upper(), True, DIM)
        surface.blit(label, (rect.x + 14, rect.y + 8))
        if badge:
            badge_img = self._font(12, bold=True).render(badge, True, MUTED)
            surface.blit(badge_img, (rect.right - 14 - badge_img.get_width(), rect.y + 8))
        text_rect = pygame.Rect(rect.x + 14, rect.y + 26, rect.width - 28, rect.height - 34)
        self._draw_wrapped(surface, body, self._font(18), ink, text_rect)

    def _notification(self, surface: pygame.Surface, rect: pygame.Rect, text: str) -> None:
        pygame.draw.rect(surface, (36, 40, 38), rect, border_radius=8)
        kicker = self._font(11, bold=True).render("NOTIFICATION", True, TEAL)
        surface.blit(kicker, (rect.x + 10, rect.centery - kicker.get_height() // 2))
        remain = pygame.Rect(rect.x + 16 + kicker.get_width(), rect.y, rect.width - 28 - kicker.get_width(), rect.height)
        self._draw_wrapped(surface, text, self._font(14), INK, remain, max_lines=1, valign=True)

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
        pygame.draw.rect(surface, fill, rect, border_radius=10)
        pygame.draw.rect(surface, border, rect, width=1, border_radius=10)
        title = self._font(16, bold=True).render(label, True, INK if active else MUTED)
        if snap.test_mode and hint:
            sub = self._font(12).render(hint, True, DIM)
            block = title.get_height() + sub.get_height()
            surface.blit(title, (rect.centerx - title.get_width() // 2, rect.centery - block // 2))
            surface.blit(sub, (rect.centerx - sub.get_width() // 2, rect.centery - block // 2 + title.get_height()))
        else:
            surface.blit(title, (rect.centerx - title.get_width() // 2, rect.centery - title.get_height() // 2))

    def _help(self, surface: pygame.Surface, rect: pygame.Rect, snap: Snapshot) -> None:
        parts = ["Hold Space to talk", "1–3 presets", "T to type", "Esc quits"]
        if snap.footer:
            parts.append(snap.footer)
        img = self._font(12).render("  ·  ".join(parts), True, DIM)
        surface.blit(img, (rect.x, rect.y))

    def _composer(self, surface: pygame.Surface, snap: Snapshot) -> None:
        width, height = surface.get_size()
        rect = pygame.Rect(14, height - 64, width - 28, 50)
        pygame.draw.rect(surface, (18, 16, 14), rect, border_radius=10)
        pygame.draw.rect(surface, AMBER, rect, width=2, border_radius=10)
        hint = self._font(12, bold=True).render("TYPE A REQUEST", True, AMBER)
        surface.blit(hint, (rect.x + 12, rect.y + 6))
        shown = self.buffer or "What should I ask?"
        color = INK if self.buffer else DIM
        img = self._font(16).render(shown[-80:], True, color)
        surface.blit(img, (rect.x + 12, rect.y + 24))

    def _dots(self, surface: pygame.Surface, center: tuple[int, int]) -> None:
        phase = int(time.monotonic() * 3) % 3
        for index in range(3):
            color = INK if index == phase else _mix(INK, TEAL, 0.65)
            pygame.draw.circle(surface, color, (center[0] - 16 + index * 10, center[1]), 3)

    def _bars(self, surface: pygame.Surface, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
        for index in range(4):
            height = 6 + int(12 * abs(math.sin(time.monotonic() * 7 + index * 0.7)))
            rect = pygame.Rect(origin[0] + index * 7, origin[1] - height // 2, 4, height)
            pygame.draw.rect(surface, color, rect, border_radius=2)

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

    def _font(self, size: int, bold: bool = False) -> pygame.font.Font:
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
