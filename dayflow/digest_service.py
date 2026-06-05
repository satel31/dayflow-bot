from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from dayflow.calendar_service import CalendarEvent, GoogleCalendarService
from dayflow.config import Settings
from dayflow.tasks_service import GoogleTasksService, TaskItem
from dayflow.timezone_utils import get_timezone


DigestKind = Literal["morning", "evening"]


class CalendarReader(Protocol):
    def list_events_for_day(self, target_date: date) -> list[CalendarEvent]:
        ...


class TasksReader(Protocol):
    def list_tasks(self, tasklist_name: str | None = None, show_completed: bool = True) -> list[TaskItem]:
        ...


@dataclass(frozen=True)
class DailyDigest:
    kind: DigestKind
    target_date: date
    text: str


def resolve_digest_date(kind: DigestKind, *, today: date | None = None) -> date:
    resolved_today = today or date.today()
    if kind == "morning":
        return resolved_today
    if kind == "evening":
        return resolved_today + timedelta(days=1)
    raise ValueError('Тип сводки должен быть "morning" или "evening".')


def task_due_date(task: TaskItem) -> date | None:
    if not task.due:
        return None
    try:
        return date.fromisoformat(task.due[:10])
    except ValueError:
        return None


def build_daily_digest(
    *,
    kind: DigestKind,
    calendar: CalendarReader,
    tasks: TasksReader,
    today: date | None = None,
) -> DailyDigest:
    target_date = resolve_digest_date(kind, today=today)
    events = calendar.list_events_for_day(target_date)
    active_tasks = tasks.list_tasks(show_completed=False)
    due_tasks = [task for task in active_tasks if task_due_date(task) == target_date]
    return DailyDigest(
        kind=kind,
        target_date=target_date,
        text=format_daily_digest(kind=kind, target_date=target_date, events=events, tasks=due_tasks),
    )


def build_daily_digest_for_user(settings: Settings, user_id: int, kind: DigestKind) -> DailyDigest:
    tz = get_timezone(settings.timezone)
    today = datetime.now(tz).date()
    return build_daily_digest(
        kind=kind,
        calendar=GoogleCalendarService(settings, user_id=user_id),
        tasks=GoogleTasksService(settings, user_id=user_id),
        today=today,
    )


def format_daily_digest(
    *,
    kind: DigestKind,
    target_date: date,
    events: list[CalendarEvent],
    tasks: list[TaskItem],
) -> str:
    title = "Утренняя сводка" if kind == "morning" else "Вечерняя сводка"
    lines = [f"{title} на {target_date.isoformat()}"]
    lines.append("")
    lines.append("Календарь:")
    if events:
        lines.extend(f"- {event.start:%H:%M}-{event.end:%H:%M} {event.title}" for event in events)
    else:
        lines.append("- событий нет")

    lines.append("")
    lines.append("Задачи:")
    if tasks:
        lines.extend(f"- [{task.tasklist_title}] {task.title}" for task in tasks)
    else:
        lines.append("- задач нет")
    return "\n".join(lines)
