from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import bot


class InMemoryGroupStore:
    def __init__(self) -> None:
        self.groups: dict[str, str] = {}

    def list_groups(self) -> dict[str, str]:
        return dict(self.groups)

    def add_group(self, name: str, color: str) -> tuple[str, str]:
        normalized_name = name.strip()
        self.groups[normalized_name] = color.strip()
        return normalized_name, self.groups[normalized_name]

    def delete_group(self, name: str) -> bool:
        for key in list(self.groups):
            if key.casefold() == name.strip().casefold():
                del self.groups[key]
                return True
        return False

    def resolve_group_name(self, name: str) -> str | None:
        for group_name in self.groups:
            if group_name.casefold() == name.strip().casefold():
                return group_name
        return None

    def resolve_color_id(self, name: str) -> str | None:
        resolved_name = self.resolve_group_name(name)
        if not resolved_name:
            return None
        return self.groups[resolved_name]


@pytest.fixture(autouse=True)
def isolated_bot_state(monkeypatch):
    monkeypatch.setattr(bot, "group_store", InMemoryGroupStore())
    bot.pending_drafts.clear()
    bot.pending_clarifications.clear()
    bot.pending_selections.clear()
    bot.pending_slot_selections.clear()
    bot.pending_google_auth.clear()
    bot.user_calendar_services.clear()
    bot.user_tasks_services.clear()
    yield
    bot.pending_drafts.clear()
    bot.pending_clarifications.clear()
    bot.pending_selections.clear()
    bot.pending_slot_selections.clear()
    bot.pending_google_auth.clear()
    bot.user_calendar_services.clear()
    bot.user_tasks_services.clear()
