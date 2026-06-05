from datetime import date, datetime

import pytest

from dayflow.calendar_service import CalendarEvent
from dayflow.digest_service import build_daily_digest, resolve_digest_date
from dayflow.tasks_service import TaskItem
from dayflow.timezone_utils import get_timezone


TZ = get_timezone("Europe/Moscow")


class FakeCalendar:
    def __init__(self, events=None) -> None:
        self.events = list(events or [])
        self.requested_dates: list[date] = []

    def list_events_for_day(self, target_date: date):
        self.requested_dates.append(target_date)
        return self.events


class FakeTasks:
    def __init__(self, tasks=None) -> None:
        self.tasks = list(tasks or [])
        self.calls: list[dict] = []

    def list_tasks(self, tasklist_name=None, show_completed=True):
        self.calls.append({"tasklist_name": tasklist_name, "show_completed": show_completed})
        if show_completed:
            return self.tasks
        return [item for item in self.tasks if item.status != "completed"]


def make_event(title: str, hour: int) -> CalendarEvent:
    start = datetime(2026, 6, 5, hour, 0, tzinfo=TZ)
    return CalendarEvent(
        event_id=title,
        title=title,
        start=start,
        end=start.replace(hour=hour + 1),
        html_link="",
    )


def make_task(title: str, due: str, status: str = "needsAction") -> TaskItem:
    return TaskItem(
        task_id=title,
        title=title,
        tasklist_id="list-1",
        tasklist_title="Inbox",
        notes="",
        due=due,
        status=status,
    )


def test_resolve_digest_date_uses_today_for_morning_and_tomorrow_for_evening():
    today = date(2026, 6, 5)

    assert resolve_digest_date("morning", today=today) == date(2026, 6, 5)
    assert resolve_digest_date("evening", today=today) == date(2026, 6, 6)


def test_resolve_digest_date_rejects_unknown_kind():
    with pytest.raises(ValueError, match="morning"):
        resolve_digest_date("weekly", today=date(2026, 6, 5))


def test_build_morning_digest_combines_calendar_and_active_tasks_for_today():
    calendar = FakeCalendar([make_event("Созвон", 10)])
    tasks = FakeTasks(
        [
            make_task("Оплатить счет", "2026-06-05T12:00:00+03:00"),
            make_task("Завтрашняя задача", "2026-06-06T12:00:00+03:00"),
            make_task("Готовая задача", "2026-06-05T12:00:00+03:00", status="completed"),
        ]
    )

    digest = build_daily_digest(
        kind="morning",
        calendar=calendar,
        tasks=tasks,
        today=date(2026, 6, 5),
    )

    assert digest.target_date == date(2026, 6, 5)
    assert calendar.requested_dates == [date(2026, 6, 5)]
    assert tasks.calls == [{"tasklist_name": None, "show_completed": False}]
    assert "Утренняя сводка на 2026-06-05" in digest.text
    assert "- 10:00-11:00 Созвон" in digest.text
    assert "- [Inbox] Оплатить счет" in digest.text
    assert "Завтрашняя задача" not in digest.text
    assert "Готовая задача" not in digest.text


def test_build_evening_digest_uses_tomorrow_and_empty_sections():
    digest = build_daily_digest(
        kind="evening",
        calendar=FakeCalendar(),
        tasks=FakeTasks(),
        today=date(2026, 6, 5),
    )

    assert digest.target_date == date(2026, 6, 6)
    assert "Вечерняя сводка на 2026-06-06" in digest.text
    assert "- событий нет" in digest.text
    assert "- задач нет" in digest.text
