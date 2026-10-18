"""The status screen draws every state and the touch targets match the buttons."""

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

pygame = pytest.importorskip("pygame")

from desk_assistant.models import ButtonView, Snapshot, State
from desk_assistant.ui import DeskDisplay


@pytest.fixture(scope="module", autouse=True)
def _pygame():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield


def _snap(**overrides) -> Snapshot:
    data = dict(
        state=State.IDLE,
        substatus="Ready",
        last_request="",
        last_source="",
        last_reply="",
        notification="",
        error="",
        notice="",
        buttons=(
            ButtonView("briefing", "Briefing", "1"),
            ButtonView("calendar", "Calendar", "2"),
            ButtonView("inbox", "Inbox", "3"),
        ),
        recording_seconds=0.0,
        ntfy_connected=True,
        test_mode=True,
        test_note="",
        topic="desk-test",
        run_uuid="",
        active_control="",
        footer="mock",
    )
    data.update(overrides)
    return Snapshot(**data)


def test_touch_targets_match_the_drawn_buttons():
    display = DeskDisplay()
    surface = pygame.Surface((800, 480))
    display.render(surface, _snap())
    ids = [ident for _rect, ident in display._hits]
    assert ids == ["ptt", "briefing", "calendar", "inbox"]
    briefing = next(rect for rect, ident in display._hits if ident == "briefing")
    assert display.hit(briefing.center) == "briefing"
    assert display.hit((4, 4)) is None
    assert display.hit((briefing.centerx, 80)) is None


@pytest.mark.parametrize(
    "state",
    [State.IDLE, State.LISTENING, State.THINKING, State.SPEAKING, State.ERROR],
)
def test_each_state_renders(state, tmp_path):
    display = DeskDisplay()
    surface = pygame.Surface((800, 480))
    snap = _snap(
        state=state,
        substatus="Waiting for a reply · abcdef12" if state is State.THINKING else "Ready",
        last_request="What's next on my calendar?",
        last_source="button",
        last_reply="" if state is State.ERROR else "Next up is design review at 2:00 PM.",
        error="The webhook key was rejected. Check the key in your config." if state is State.ERROR else "",
        notification="The printer downstairs is free." if state is State.IDLE else "",
        ntfy_connected=state is not State.ERROR,
        active_control="ptt" if state is State.LISTENING else "",
        recording_seconds=2.4 if state is State.LISTENING else 0,
    )
    display.render(surface, snap)
    path = tmp_path / f"{state.value}.png"
    pygame.image.save(surface, str(path))
    assert path.stat().st_size > 1000
