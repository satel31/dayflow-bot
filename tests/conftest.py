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


class InMemoryDigestSubscriberStore:
    def __init__(self) -> None:
        self.subscribers: dict[int, int] = {}

    def list_subscribers(self):
        return [
            SimpleDigestSubscriber(user_id=user_id, chat_id=chat_id)
            for user_id, chat_id in sorted(self.subscribers.items())
        ]

    def add(self, user_id: int, chat_id: int):
        self.subscribers[int(user_id)] = int(chat_id)
        return SimpleDigestSubscriber(user_id=int(user_id), chat_id=int(chat_id))

    def remove(self, user_id: int) -> bool:
        return self.subscribers.pop(int(user_id), None) is not None

    def get(self, user_id: int):
        chat_id = self.subscribers.get(int(user_id))
        if chat_id is None:
            return None
        return SimpleDigestSubscriber(user_id=int(user_id), chat_id=chat_id)


class SimpleDigestSubscriber:
    def __init__(self, user_id: int, chat_id: int) -> None:
        self.user_id = user_id
        self.chat_id = chat_id


@pytest.fixture(autouse=True)
def isolated_bot_state(monkeypatch):
    monkeypatch.setattr(bot, "group_store", InMemoryGroupStore())
    monkeypatch.setattr(bot, "digest_subscriber_store", InMemoryDigestSubscriberStore())
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
