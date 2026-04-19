from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError, TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from dayflow.assistant_service import AssistantPlan, AssistantService, AssistantServiceError
from dayflow.auth import (
    GoogleAuthRequiredError,
    GoogleAuthSession,
    build_google_auth_session,
    complete_google_auth,
    disconnect_google_account,
)
from dayflow.calendar_service import CalendarEvent, GoogleCalendarService
from dayflow.config import load_settings
from dayflow.group_store import EventGroupStore
from dayflow.tasks_service import GoogleTasksService, TaskItem
from dayflow.timezone_utils import get_timezone
from dayflow.work_schedule_store import (
    WEEKDAY_LABELS,
    WorkSchedule,
    WorkScheduleStore,
    format_minutes,
    parse_time_to_minutes,
    parse_weekdays,
)


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


@dataclass
class EventCreateDraft:
    kind: Literal["event_create"]
    title: str
    start_at: datetime
    duration_minutes: int
    group_name: str
    conflicts: tuple[CalendarEvent, ...] = ()


@dataclass
class EventUpdateDraft:
    kind: Literal["event_update"]
    event_id: str
    original_title: str
    new_title: str
    start_at: datetime
    duration_minutes: int
    group_name: str
    conflicts: tuple[CalendarEvent, ...] = ()


@dataclass
class EventDeleteDraft:
    kind: Literal["event_delete"]
    event_id: str
    title: str
    start_at: datetime


@dataclass
class TaskCreateDraft:
    kind: Literal["task_create"]
    title: str
    due_date: date | None
    notes: str
    task_list_name: str
    subtasks: tuple[str, ...]


@dataclass
class TaskUpdateDraft:
    kind: Literal["task_update"]
    task_id: str
    task_list_name: str
    original_title: str
    new_title: str
    due_date: date | None
    notes: str


@dataclass
class TaskDeleteDraft:
    kind: Literal["task_delete"]
    task_id: str
    task_list_name: str
    title: str


@dataclass
class TaskCompleteDraft:
    kind: Literal["task_complete"]
    task_id: str
    task_list_name: str
    title: str


@dataclass
class PendingClarification:
    original_text: str


@dataclass
class PendingSelection:
    kind: Literal["event_update", "event_delete", "task_update", "task_delete", "task_complete"]
    plan: AssistantPlan
    candidates: tuple[object, ...]


@dataclass
class PendingSlotSelection:
    title: str
    duration_minutes: int
    event_group: str
    candidates: tuple[tuple[datetime, datetime], ...]


Draft = (
    EventCreateDraft
    | EventUpdateDraft
    | EventDeleteDraft
    | TaskCreateDraft
    | TaskUpdateDraft
    | TaskDeleteDraft
    | TaskCompleteDraft
)


settings = load_settings()
calendar_service = GoogleCalendarService(settings)
assistant_service = AssistantService(settings)
tasks_service = GoogleTasksService(settings)
group_store = EventGroupStore(settings.event_groups_path)
work_schedule_store = WorkScheduleStore(
    settings.work_schedule_path,
    settings.workday_start_hour,
    settings.workday_end_hour,
)
work_schedule = work_schedule_store.load()
pending_drafts: dict[int, Draft] = {}
pending_clarifications: dict[int, PendingClarification] = {}
pending_selections: dict[int, PendingSelection] = {}
pending_slot_selections: dict[int, PendingSlotSelection] = {}
pending_google_auth: dict[int, GoogleAuthSession] = {}
user_calendar_services: dict[int, GoogleCalendarService] = {}
user_tasks_services: dict[int, GoogleTasksService] = {}


async def safe_reply_text(message, text: str, reply_markup=None) -> None:
    try:
        await message.reply_text(text, reply_markup=reply_markup)
    except TimedOut:
        logger.exception("Failed to send Telegram reply due to timeout")
    except TelegramError:
        logger.exception("Failed to send Telegram reply")


def require_token() -> str:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "Не найден TELEGRAM_BOT_TOKEN. Скопируйте .env.example в .env и заполните его."
        )
    return settings.telegram_bot_token


def get_actor_id(update: Update) -> int:
    if getattr(update, "effective_user", None) and update.effective_user:
        return update.effective_user.id
    return update.effective_chat.id


def get_calendar_service(user_id: int | None = None):
    if user_id is None or not isinstance(calendar_service, GoogleCalendarService):
        return calendar_service
    service = user_calendar_services.get(user_id)
    if service is None:
        service = GoogleCalendarService(settings, user_id=user_id)
        user_calendar_services[user_id] = service
    return service


def get_tasks_service(user_id: int | None = None):
    if user_id is None or not isinstance(tasks_service, GoogleTasksService):
        return tasks_service
    service = user_tasks_services.get(user_id)
    if service is None:
        service = GoogleTasksService(settings, user_id=user_id)
        user_tasks_services[user_id] = service
    return service


def reset_user_google_services(user_id: int) -> None:
    user_calendar_services.pop(user_id, None)
    user_tasks_services.pop(user_id, None)


def parse_date(raw_value: str) -> date:
    return datetime.strptime(raw_value.strip(), "%Y-%m-%d").date()


def parse_datetime(raw_date: str, raw_time: str) -> datetime:
    dt = datetime.strptime(f"{raw_date.strip()} {raw_time.strip()}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=get_timezone(settings.timezone))


def text_is_yes(text: str) -> bool:
    return text.strip().casefold() in {"да", "ага", "yes", "y", "ок", "окей", "подтвердить"}


def text_is_no(text: str) -> bool:
    return text.strip().casefold() in {"нет", "no", "n", "отмена", "cancel"}


def resolve_group_name(raw_name: str) -> str:
    if not raw_name:
        return ""
    return group_store.resolve_group_name(raw_name) or ""


def extract_selection_index(text: str, max_items: int) -> int | None:
    tokens = re.findall(r"[0-9]+|[^\W\d_]+", text.casefold(), flags=re.UNICODE)
    ordinal_prefixes = [
        ("перв", 0),
        ("втор", 1),
        ("трет", 2),
        ("треть", 2),
        ("четверт", 3),
        ("пят", 4),
    ]
    for token in tokens:
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < max_items:
                return index
        for prefix, index in ordinal_prefixes:
            if token.startswith(prefix) and index < max_items:
                return index
    return None


def resolve_pending_selection(chat_id: int, text: str, user_id: int | None = None) -> dict | None:
    pending = pending_selections.get(chat_id)
    if not pending:
        return None
    index = extract_selection_index(text, len(pending.candidates))
    if index is None:
        return None
    pending_selections.pop(chat_id, None)
    pending_clarifications.pop(chat_id, None)
    selected = pending.candidates[index]

    if pending.kind == "event_delete":
        assert isinstance(selected, CalendarEvent)
        draft = EventDeleteDraft(
            kind="event_delete",
            event_id=selected.event_id,
            title=selected.title,
            start_at=selected.start,
        )
        pending_drafts[chat_id] = draft
        return {"text": format_event_delete_draft(draft), "draft": draft}

    if pending.kind == "event_update":
        assert isinstance(selected, CalendarEvent)
        plan = pending.plan
        has_changes = bool(plan.date or plan.time or plan.new_title or plan.event_group)
        if not has_changes and plan.duration_minutes == 60:
            pending_selections[chat_id] = PendingSelection(
                kind="event_update",
                plan=plan,
                candidates=(selected,),
            )
            return {"text": plan.reply or f'На когда перенести событие "{selected.title}"?', "needs_clarification": True}
        new_start_at = parse_datetime(plan.date, plan.time) if plan.date and plan.time else selected.start
        new_title = plan.new_title or plan.title or selected.title
        duration = (
            plan.duration_minutes
            if (plan.date or plan.time or plan.duration_minutes != 60)
            else int((selected.end - selected.start).total_seconds() // 60)
        )
        conflicts = tuple(
            get_calendar_service(user_id).find_conflicts(
                new_start_at,
                duration,
                exclude_event_id=selected.event_id,
            )
        )
        draft = EventUpdateDraft(
            kind="event_update",
            event_id=selected.event_id,
            original_title=selected.title,
            new_title=new_title,
            start_at=new_start_at,
            duration_minutes=duration,
            group_name=resolve_group_name(plan.event_group),
            conflicts=conflicts,
        )
        pending_drafts[chat_id] = draft
        return {"text": format_event_update_draft(draft), "draft": draft}

    if pending.kind in {"task_update", "task_delete", "task_complete"}:
        assert isinstance(selected, TaskItem)
        if pending.kind == "task_delete":
            draft = TaskDeleteDraft(
                kind="task_delete",
                task_id=selected.task_id,
                task_list_name=selected.tasklist_title,
                title=selected.title,
            )
            pending_drafts[chat_id] = draft
            return {"text": format_task_delete_draft(draft), "draft": draft}
        if pending.kind == "task_complete":
            draft = TaskCompleteDraft(
                kind="task_complete",
                task_id=selected.task_id,
                task_list_name=selected.tasklist_title,
                title=selected.title,
            )
            pending_drafts[chat_id] = draft
            return {"text": format_task_complete_draft(draft), "draft": draft}
        due_date = (
            parse_date(pending.plan.date)
            if pending.plan.date
            else (date.fromisoformat(selected.due[:10]) if selected.due else None)
        )
        draft = TaskUpdateDraft(
            kind="task_update",
            task_id=selected.task_id,
            task_list_name=selected.tasklist_title,
            original_title=selected.title,
            new_title=pending.plan.new_title or pending.plan.title or selected.title,
            due_date=due_date,
            notes=pending.plan.notes if pending.plan.notes else selected.notes,
        )
        pending_drafts[chat_id] = draft
        return {"text": format_task_update_draft(draft), "draft": draft}

    return None


def infer_event_update_plan(message_text: str, plan: AssistantPlan) -> AssistantPlan | None:
    if plan.action != "chat":
        return None
    raw_text = message_text.strip()
    lowered = raw_text.casefold()
    prefixes = ("перенеси ", "перенести ", "измени ", "изменить ", "переименуй ")
    matched_prefix = next((prefix for prefix in prefixes if lowered.startswith(prefix)), "")
    if not matched_prefix:
        return None
    target = raw_text[len(matched_prefix) :].strip(" .,!?:;")
    target = re.split(r"\s+\b(?:на|в)\b\s+\d", target, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,!?:;")
    if not target:
        return None
    return replace(plan, action="update_event", target_title=plan.target_title or target)


def apply_message_preferences(message_text: str, plan: AssistantPlan) -> AssistantPlan:
    lowered = message_text.casefold()
    outside_work_hours = plan.outside_work_hours or any(
        phrase in lowered
        for phrase in (
            "не в рабочее время",
            "в нерабочее время",
            "вне рабочего времени",
            "не в рабочие часы",
            "вне рабочих часов",
            "в нерабочие часы",
            "не в часы работы",
            "вне часов работы",
            "после работы",
            "до работы",
            "после рабочего дня",
            "после офиса",
            "до офиса",
            "когда закончу работу",
            "когда я после работы",
            "вне работы",
        )
    )
    within_work_hours = plan.within_work_hours or any(
        phrase in lowered
        for phrase in (
            "в рабочее время",
            "по рабочему времени",
            "в рабочие часы",
            "в часы работы",
            "в мои рабочие часы",
            "в мои рабочие дни",
            "когда я работаю",
        )
    )
    preferred_period = plan.preferred_period or infer_preferred_period(lowered)
    excluded_period = plan.excluded_period or infer_excluded_period(lowered)
    weekday_filter = plan.weekday_filter or infer_weekday_filter_value(lowered)
    return replace(
        plan,
        outside_work_hours=outside_work_hours,
        within_work_hours=within_work_hours,
        preferred_period=preferred_period,
        excluded_period=excluded_period,
        weekday_filter=weekday_filter,
    )


def infer_preferred_period(lowered: str) -> str:
    period_synonyms = {
        "morning": ("утром", "на утро", "с утра", "в первой половине дня"),
        "day": ("днем", "днём", "в середине дня"),
        "evening": (
            "вечером",
            "на вечер",
            "ближе к вечеру",
            "во второй половине дня",
            "после обеда",
            "после 18",
            "после 19",
        ),
        "night": ("ночью", "поздно вечером", "поздней ночью"),
    }
    for period, phrases in period_synonyms.items():
        if any(phrase in lowered for phrase in phrases):
            return period
    return ""


def infer_excluded_period(lowered: str) -> str:
    exclusions = {
        "morning": ("не утром", "кроме утра", "только не утром"),
        "day": ("не днем", "не днём", "кроме дня"),
        "evening": ("не вечером", "кроме вечера"),
        "night": ("не ночью", "кроме ночи"),
    }
    for period, phrases in exclusions.items():
        if any(phrase in lowered for phrase in phrases):
            return period
    return ""


def infer_weekday_filter_value(lowered: str) -> str:
    if any(
        phrase in lowered
        for phrase in (
            "в выходные",
            "на выходных",
            "по выходным",
            "в уикенд",
            "на уикенд",
        )
    ):
        return "weekend"
    if any(
        phrase in lowered
        for phrase in (
            "по будням",
            "в будни",
            "по рабочим дням",
            "в рабочие дни",
        )
    ):
        return "workdays"
    return ""


def normalize_plan(plan: AssistantPlan) -> AssistantPlan:
    preferred_period = plan.preferred_period if plan.preferred_period in {"morning", "day", "evening", "night"} else ""
    excluded_period = plan.excluded_period if plan.excluded_period in {"morning", "day", "evening", "night"} else ""
    weekday_filter = plan.weekday_filter if plan.weekday_filter in {"workdays", "weekend"} else ""
    within_work_hours = plan.within_work_hours
    outside_work_hours = plan.outside_work_hours

    if within_work_hours and outside_work_hours:
        outside_work_hours = False

    if preferred_period and preferred_period == excluded_period:
        # Если один и тот же период и выбран, и исключен, оставляем исключение.
        preferred_period = ""

    duration_minutes = int(plan.duration_minutes or 60)
    if duration_minutes <= 0:
        duration_minutes = 60

    if plan.action == "create_event" and not plan.time and (
        plan.date_from
        or plan.date_to
        or preferred_period
        or excluded_period
        or weekday_filter
        or within_work_hours
        or outside_work_hours
    ):
        action = "find_free_slots"
    else:
        action = plan.action

    return replace(
        plan,
        action=action,
        preferred_period=preferred_period,
        excluded_period=excluded_period,
        weekday_filter=weekday_filter,
        within_work_hours=within_work_hours,
        outside_work_hours=outside_work_hours,
        duration_minutes=duration_minutes,
    )


def detect_evening_preference(message_text: str) -> bool:
    return infer_preferred_period(message_text.casefold()) == "evening"


def detect_worktime_preference(message_text: str) -> bool:
    lowered = message_text.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "в рабочее время",
            "по рабочему времени",
            "в рабочие часы",
            "в часы работы",
            "в мои рабочие часы",
            "в мои рабочие дни",
            "когда я работаю",
        )
    )


def detect_weekday_filter(message_text: str) -> tuple[int, ...] | None:
    value = infer_weekday_filter_value(message_text.casefold())
    if value == "weekend":
        return (5, 6)
    if value == "workdays":
        return (0, 1, 2, 3, 4)
    return None


def infer_flexible_event_plan(plan: AssistantPlan) -> AssistantPlan | None:
    if plan.action != "create_event":
        return None
    if plan.date and plan.time:
        return None
    if not (
        plan.date
        or plan.date_from
        or plan.date_to
        or plan.outside_work_hours
        or plan.within_work_hours
        or plan.preferred_period
        or plan.excluded_period
        or plan.weekday_filter
    ):
        return None
    return replace(plan, action="find_free_slots")


def format_slot_suggestions(
    slots: list[tuple[datetime, datetime]],
    *,
    duration_minutes: int,
    title: str = "",
    outside_work_hours: bool = False,
) -> str:
    lead = f'Нашла варианты для "{title}"' if title else "Нашла варианты"
    if outside_work_hours:
        lead += " вне рабочего времени"
    lead += ":"
    return "\n".join([lead] + [f"- {start:%d.%m %H:%M}-{end:%H:%M}" for start, end in slots])


def period_bounds(period: str) -> tuple[int, int] | None:
    mapping = {
        "morning": (6 * 60, 12 * 60),
        "day": (12 * 60, 18 * 60),
        "evening": (18 * 60, 22 * 60),
        "night": (22 * 60, 24 * 60),
    }
    return mapping.get(period)


def apply_period_filters(
    windows: list[tuple[datetime, datetime]],
    *,
    preferred_period: str = "",
    excluded_period: str = "",
) -> list[tuple[datetime, datetime]]:
    filtered = windows
    if preferred_period:
        bounds = period_bounds(preferred_period)
        if bounds:
            filtered = intersect_windows_with_range(filtered, start_minutes=bounds[0], end_minutes=bounds[1])
    if excluded_period:
        bounds = period_bounds(excluded_period)
        if bounds:
            filtered = subtract_range_from_windows(filtered, start_minutes=bounds[0], end_minutes=bounds[1])
    return filtered


def intersect_windows_with_range(
    windows: list[tuple[datetime, datetime]],
    *,
    start_minutes: int,
    end_minutes: int,
) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in windows:
        range_start = start.replace(hour=start_minutes // 60, minute=start_minutes % 60)
        if end_minutes >= 24 * 60:
            range_end = start.replace(hour=0, minute=0) + timedelta(days=1)
        else:
            range_end = start.replace(hour=end_minutes // 60, minute=end_minutes % 60)
        clipped_start = max(start, range_start)
        clipped_end = min(end, range_end)
        if clipped_end > clipped_start:
            result.append((clipped_start, clipped_end))
    return result


def subtract_range_from_windows(
    windows: list[tuple[datetime, datetime]],
    *,
    start_minutes: int,
    end_minutes: int,
) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in windows:
        blocked_start = start.replace(hour=start_minutes // 60, minute=start_minutes % 60)
        if end_minutes >= 24 * 60:
            blocked_end = start.replace(hour=0, minute=0) + timedelta(days=1)
        else:
            blocked_end = start.replace(hour=end_minutes // 60, minute=end_minutes % 60)
        if blocked_end <= start or blocked_start >= end:
            result.append((start, end))
            continue
        if start < blocked_start:
            result.append((start, blocked_start))
        if blocked_end < end:
            result.append((blocked_end, end))
    return result


def filter_windows_by_min_duration(
    windows: list[tuple[datetime, datetime]],
    *,
    duration_minutes: int,
) -> list[tuple[datetime, datetime]]:
    min_duration = timedelta(minutes=duration_minutes)
    return [(start, end) for start, end in windows if end - start >= min_duration]


def select_diverse_windows(
    windows: list[tuple[datetime, datetime]],
    *,
    max_results: int = 8,
    max_per_day: int = 2,
) -> list[tuple[datetime, datetime]]:
    buckets: dict[date, list[tuple[datetime, datetime]]] = {}
    ordered_days: list[date] = []
    for window in sorted(windows, key=lambda item: item[0]):
        day = window[0].date()
        if day not in buckets:
            buckets[day] = []
            ordered_days.append(day)
        if len(buckets[day]) < max_per_day:
            buckets[day].append(window)

    selected: list[tuple[datetime, datetime]] = []
    round_index = 0
    while len(selected) < max_results:
        added_any = False
        for day in ordered_days:
            day_slots = buckets.get(day, [])
            if round_index < len(day_slots):
                selected.append(day_slots[round_index])
                added_any = True
                if len(selected) >= max_results:
                    return selected
        if not added_any:
            break
        round_index += 1
    return selected


def select_diverse_slots(
    slots: list[tuple[datetime, datetime]],
    *,
    max_results: int = 8,
    max_per_day: int = 2,
) -> list[tuple[datetime, datetime]]:
    return select_diverse_windows(slots, max_results=max_results, max_per_day=max_per_day)


def filter_slots_by_start_minutes(
    slots: list[tuple[datetime, datetime]],
    *,
    min_start_minutes: int,
) -> list[tuple[datetime, datetime]]:
    return [
        (start, end)
        for start, end in slots
        if start.hour * 60 + start.minute >= min_start_minutes
    ]


def resolve_weekday_filter(plan: AssistantPlan) -> tuple[int, ...] | None:
    if plan.weekday_filter == "weekend":
        return (5, 6)
    if plan.weekday_filter == "workdays":
        return (0, 1, 2, 3, 4)
    return None


def format_conflicts(conflicts: tuple[CalendarEvent, ...]) -> str:
    if not conflicts:
        return ""
    lines = ["Есть пересечения:"]
    for index, item in enumerate(conflicts, start=1):
        lines.append(f"{index}. {item.start:%d.%m %H:%M}-{item.end:%H:%M} {item.title}")
    return "\n".join(lines)


def format_event_create_draft(draft: EventCreateDraft) -> str:
    group_line = draft.group_name if draft.group_name else "без группы"
    lines = [
        f'Создать событие "{draft.title}" 🗓',
        f"Дата и время: {draft.start_at:%d.%m.%Y %H:%M}",
        f"Длительность: {draft.duration_minutes} мин.",
        f"Группа: {group_line}",
    ]
    if draft.conflicts:
        lines.append(format_conflicts(draft.conflicts))
        lines.append("Оставляем как есть или удалить одно из пересечений?")
    return "\n".join(lines)


def format_event_update_draft(draft: EventUpdateDraft) -> str:
    group_line = draft.group_name if draft.group_name else "без группы"
    lines = [
        f'Обновим событие "{draft.original_title}" ✨',
        f'Новое название: "{draft.new_title}"',
        f"Новое время: {draft.start_at:%d.%m.%Y %H:%M}",
        f"Длительность: {draft.duration_minutes} мин.",
        f"Группа: {group_line}",
    ]
    if draft.conflicts:
        lines.append(format_conflicts(draft.conflicts))
        lines.append("Оставляем как есть или удалить одно из пересечений?")
    return "\n".join(lines)


def format_event_delete_draft(draft: EventDeleteDraft) -> str:
    return f'Удалить событие "{draft.title}" от {draft.start_at:%d.%m.%Y %H:%M}? 🗑'


def format_task_create_draft(draft: TaskCreateDraft) -> str:
    lines = [f'Добавим задачу "{draft.title}" ✅', f"Список: {draft.task_list_name}"]
    if draft.due_date:
        lines.append(f"Срок: {draft.due_date:%d.%m.%Y}")
    if draft.notes:
        lines.append(f"Описание: {draft.notes}")
    if draft.subtasks:
        lines.append("Подзадачи:")
        lines.extend(f"- {item}" for item in draft.subtasks)
    return "\n".join(lines)


def format_task_update_draft(draft: TaskUpdateDraft) -> str:
    lines = [
        f'Обновим задачу "{draft.original_title}" ✍️',
        f'Новое название: "{draft.new_title}"',
        f"Список: {draft.task_list_name}",
    ]
    if draft.due_date:
        lines.append(f"Срок: {draft.due_date:%d.%m.%Y}")
    if draft.notes:
        lines.append(f"Описание: {draft.notes}")
    return "\n".join(lines)


def format_task_delete_draft(draft: TaskDeleteDraft) -> str:
    return f'Удалить задачу "{draft.title}" из списка "{draft.task_list_name}"? 🗑'


def format_task_complete_draft(draft: TaskCompleteDraft) -> str:
    return f'Отметить задачу "{draft.title}" выполненной в списке "{draft.task_list_name}"? 🎉'


def event_markup(draft: EventCreateDraft | EventUpdateDraft) -> InlineKeyboardMarkup:
    rows = []
    if draft.conflicts:
        rows.append([InlineKeyboardButton("Оставить как есть", callback_data="draft:confirm")])
        for index, conflict in enumerate(draft.conflicts[:5], start=1):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Удалить пересечение {index}",
                        callback_data=f"conflict:{index}",
                    )
                ]
            )
    else:
        rows.append([InlineKeyboardButton("Подтвердить", callback_data="draft:confirm")])
    rows.append([InlineKeyboardButton("Без группы", callback_data="group:none")])
    for group_name in list(group_store.list_groups())[:8]:
        rows.append([InlineKeyboardButton(f"Группа: {group_name}", callback_data=f"group:{group_name}")])
    rows.append([InlineKeyboardButton("Отмена", callback_data="draft:cancel")])
    return InlineKeyboardMarkup(rows)


def simple_confirm_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подтвердить", callback_data="draft:confirm")],
            [InlineKeyboardButton("Отмена", callback_data="draft:cancel")],
        ]
    )


def slot_suggestions_markup(slots: list[tuple[datetime, datetime]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{start:%d.%m %H:%M}", callback_data=f"slot:{index}")]
        for index, (start, _end) in enumerate(slots[:8], start=1)
    ]
    return InlineKeyboardMarkup(rows)


def worktime_text() -> str:
    return (
        work_schedule_store.format(work_schedule)
        + "\n\nМожно поменять дни, часы или сбросить на стандартный график."
        + "\nДля ручной настройки тоже остается команда:\n/worktime_set 10:30-19:00 пн-пт"
    )


def worktime_days_label() -> str:
    return ", ".join(WEEKDAY_LABELS[day] for day in work_schedule.weekdays)


def worktime_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Дни: {worktime_days_label()}", callback_data="worktime:menu:days")],
            [
                InlineKeyboardButton(
                    f"Часы: {format_minutes(work_schedule.start_minutes)}-{format_minutes(work_schedule.end_minutes)}",
                    callback_data="worktime:menu:hours",
                )
            ],
            [InlineKeyboardButton("Сбросить на Пн-Пт 09:00-18:00", callback_data="worktime:reset")],
        ]
    )


def worktime_days_markup() -> InlineKeyboardMarkup:
    rows = []
    day_buttons = []
    for day in range(7):
        marker = "✓" if day in work_schedule.weekdays else "·"
        day_buttons.append(
            InlineKeyboardButton(
                f"{marker} {WEEKDAY_LABELS[day]}",
                callback_data=f"worktime:toggle_day:{day}",
            )
        )
    rows.append(day_buttons[:4])
    rows.append(day_buttons[4:])
    rows.append([InlineKeyboardButton("Готово", callback_data="worktime:menu:root")])
    return InlineKeyboardMarkup(rows)


def worktime_hours_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("09:00-18:00", callback_data="worktime:set_hours:09:00-18:00")],
            [InlineKeyboardButton("10:00-19:00", callback_data="worktime:set_hours:10:00-19:00")],
            [InlineKeyboardButton("11:00-20:00", callback_data="worktime:set_hours:11:00-20:00")],
            [InlineKeyboardButton("12:00-21:00", callback_data="worktime:set_hours:12:00-21:00")],
            [InlineKeyboardButton("Назад", callback_data="worktime:menu:root")],
        ]
    )


def worktime_markup() -> InlineKeyboardMarkup:
    return worktime_menu_markup()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply_text(
        update.message,
        "Привет. Я DayFlow: локальный ассистент для календаря и задач.\n\n"
        "Сначала подключите Google командой /connect_google.\n\n"
        "Пишите обычным текстом:\n"
        "Поставь встречу с клиентом 2026-04-01 в 13:00\n"
        "Перенеси встречу с клиентом на 15:00\n"
        "Удали тренировку завтра\n"
        "Отметь задачу отправить отчет выполненной"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply_text(
        update.message,
        "Доступно:\n"
        "/connect_google\n"
        "/disconnect_google\n"
        "/today YYYY-MM-DD\n"
        "/slots YYYY-MM-DD [minutes]\n"
        "/groups\n"
        "/group_add Название | Цвет\n"
        "/group_delete Название\n"
        "/worktime\n"
        "/worktime_set HH:MM-HH:MM дни\n"
        "/tasklists\n\n"
        "Обычным текстом можно создавать, редактировать и удалять события и задачи."
    )


async def connect_google_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_actor_id(update)
    try:
        session = build_google_auth_session(settings, user_id)
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось подготовить подключение Google: {exc}")
        return
    pending_google_auth[user_id] = session
    await safe_reply_text(
        update.message,
        "Откройте ссылку, войдите в нужный Google-аккаунт и после редиректа пришлите сюда полный URL "
        "из адресной строки, который начинается с http://localhost:\n\n"
        f"{session.auth_url}"
    )


async def disconnect_google_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_actor_id(update)
    pending_google_auth.pop(user_id, None)
    reset_user_google_services(user_id)
    if disconnect_google_account(settings, user_id):
        await safe_reply_text(update.message, "Google-аккаунт для этого Telegram-пользователя отключен.")
        return
    await safe_reply_text(update.message, "Для этого пользователя подключенного Google-аккаунта не было.")


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply_text(update.message, "Формат: /today YYYY-MM-DD")
        return
    user_id = get_actor_id(update)
    target_date = parse_date(context.args[0])
    try:
        events = await asyncio.to_thread(get_calendar_service(user_id).list_events_for_day, target_date)
    except GoogleAuthRequiredError as exc:
        await safe_reply_text(update.message, str(exc))
        return
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось получить календарь: {exc}")
        return
    if not events:
        await safe_reply_text(update.message, f"На {target_date.isoformat()} событий нет.")
        return
    lines = [f"{event.start:%H:%M}-{event.end:%H:%M}  {event.title}" for event in events]
    await safe_reply_text(update.message, "\n".join(lines))


async def slots_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply_text(update.message, "Формат: /slots YYYY-MM-DD [minutes]")
        return
    user_id = get_actor_id(update)
    target_date = parse_date(context.args[0])
    duration = int(context.args[1]) if len(context.args) > 1 else 60
    try:
        slots = await asyncio.to_thread(get_calendar_service(user_id).find_free_slots, target_date, duration)
    except GoogleAuthRequiredError as exc:
        await safe_reply_text(update.message, str(exc))
        return
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось проверить слоты: {exc}")
        return
    if not slots:
        await safe_reply_text(update.message, "Свободных окон не найдено.")
        return
    await safe_reply_text(
        update.message,
        f"Свободные окна на {target_date.isoformat()}:\n"
        + "\n".join(f"{start:%H:%M}-{end:%H:%M}" for start, end in slots)
    )


async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    groups = group_store.list_groups()
    if not groups:
        await safe_reply_text(update.message, "Групп пока нет. Пример: /group_add Работа | 9")
        return
    await safe_reply_text(
        update.message,
        "Группы событий:\n" + "\n".join(f"{name}: colorId {color_id}" for name, color_id in groups.items())
    )


async def worktime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await safe_reply_text(
        update.message,
        worktime_text(),
        reply_markup=worktime_markup(),
    )


async def worktime_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global work_schedule
    if len(context.args) < 2:
        await safe_reply_text(update.message, "Формат: /worktime_set HH:MM-HH:MM пн-пт")
        return
    try:
        start_text, end_text = " ".join(context.args[:-1]).split("-", maxsplit=1)
        weekdays = parse_weekdays(context.args[-1])
        work_schedule = work_schedule_store.save(
            WorkSchedule(
                weekdays=weekdays,
                start_minutes=parse_time_to_minutes(start_text),
                end_minutes=parse_time_to_minutes(end_text),
            )
        )
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось сохранить рабочее время: {exc}")
        return
    await safe_reply_text(update.message, worktime_text(), reply_markup=worktime_markup())


async def group_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    payload = update.message.text.removeprefix("/group_add").strip()
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) != 2:
        await safe_reply_text(update.message, "Формат: /group_add Название | Цвет")
        return
    try:
        name, color_id = group_store.add_group(parts[0], parts[1])
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось сохранить группу: {exc}")
        return
    await safe_reply_text(update.message, f'Группа "{name}" сохранена с colorId {color_id}.')


async def group_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply_text(update.message, "Формат: /group_delete Название")
        return
    name = " ".join(context.args)
    if group_store.delete_group(name):
        await safe_reply_text(update.message, f'Группа "{name}" удалена.')
    else:
        await safe_reply_text(update.message, f'Группа "{name}" не найдена.')


async def tasklists_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_actor_id(update)
    try:
        items = await asyncio.to_thread(get_tasks_service(user_id).list_tasklists)
    except GoogleAuthRequiredError as exc:
        await safe_reply_text(update.message, str(exc))
        return
    except Exception as exc:
        await safe_reply_text(update.message, f"Не удалось получить списки задач: {exc}")
        return
    if not items:
        await safe_reply_text(update.message, "В Google Tasks пока нет списков.")
        return
    await safe_reply_text(update.message, "Списки задач:\n" + "\n".join(item["title"] for item in items))


async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await safe_reply_text(update.message, "Формат: /ask ваш вопрос")
        return
    await process_text(update, " ".join(context.args))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_id = get_actor_id(update)
    text = update.message.text.strip()
    if user_id in pending_google_auth and text.startswith("http://localhost:"):
        try:
            complete_google_auth(settings, user_id, pending_google_auth[user_id], text)
            pending_google_auth.pop(user_id, None)
            reset_user_google_services(user_id)
        except Exception as exc:
            await safe_reply_text(update.message, f"Не удалось завершить подключение Google: {exc}")
            return
        await safe_reply_text(update.message, "Google-аккаунт подключен. Теперь бот работает с вашим календарем и задачами.")
        return
    if chat_id in pending_clarifications and text_is_no(text):
        pending_clarifications.pop(chat_id, None)
        await safe_reply_text(update.message, "Уточнение отменено.")
        return
    if chat_id in pending_drafts and text_is_yes(text):
        await finalize_pending_draft(update, confirmed=True)
        return
    if chat_id in pending_drafts and text_is_no(text):
        await finalize_pending_draft(update, confirmed=False)
        return
    selection_result = resolve_pending_selection(chat_id, text, user_id=user_id)
    if selection_result:
        reply_markup = None
        draft = selection_result.get("draft")
        if isinstance(draft, (EventCreateDraft, EventUpdateDraft)):
            reply_markup = event_markup(draft)
        elif draft:
            reply_markup = simple_confirm_markup()
        await safe_reply_text(update.message, selection_result["text"], reply_markup=reply_markup)
        return
    if chat_id in pending_clarifications:
        clarification = pending_clarifications.pop(chat_id)
        text = f"{clarification.original_text}\nУточнение пользователя: {text}"
    await process_text(update, text)


async def process_text(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    user_id = get_actor_id(update)
    try:
        handler_params = inspect.signature(handle_natural_language).parameters
        if len(handler_params) >= 3:
            result = await asyncio.to_thread(handle_natural_language, chat_id, text, user_id)
        else:
            result = await asyncio.to_thread(handle_natural_language, chat_id, text)
    except AssistantServiceError as exc:
        await safe_reply_text(update.message, str(exc))
        return
    except GoogleAuthRequiredError as exc:
        await safe_reply_text(update.message, str(exc))
        return
    except Exception as exc:
        logger.exception("Failed to process natural language request")
        await safe_reply_text(update.message, "Не удалось обработать запрос. Попробуйте позже.")
        return

    if result.get("needs_clarification"):
        pending_clarifications[chat_id] = PendingClarification(original_text=text)
    else:
        pending_clarifications.pop(chat_id, None)
        pending_selections.pop(chat_id, None)
        if not result.get("reply_markup"):
            pending_slot_selections.pop(chat_id, None)

    reply_markup = None
    draft = result.get("draft")
    if isinstance(draft, (EventCreateDraft, EventUpdateDraft)):
        reply_markup = event_markup(draft)
    elif draft:
        reply_markup = simple_confirm_markup()
    elif result.get("reply_markup") is not None:
        reply_markup = result["reply_markup"]
    await safe_reply_text(update.message, result["text"], reply_markup=reply_markup)


def handle_natural_language(chat_id: int, message_text: str, user_id: int | None = None) -> dict:
    calendar = get_calendar_service(user_id)
    tasks = get_tasks_service(user_id)
    plan = assistant_service.plan(message_text)
    plan = apply_message_preferences(message_text, plan)
    plan = normalize_plan(plan)
    fallback_update_plan = infer_event_update_plan(message_text, plan)
    if fallback_update_plan:
        plan = fallback_update_plan
    flexible_event_plan = infer_flexible_event_plan(plan)
    if flexible_event_plan:
        plan = flexible_event_plan

    if plan.action == "list_events":
        if not plan.date:
            return {"text": plan.reply or "Уточните дату.", "needs_clarification": True}
        events = calendar.list_events_for_day(parse_date(plan.date))
        if not events:
            return {"text": f"На {plan.date} событий нет."}
        return {"text": "\n".join(f"{e.start:%H:%M}-{e.end:%H:%M}  {e.title}" for e in events)}

    if plan.action == "find_free_slots":
        pending_slot_selections.pop(chat_id, None)
        active_workdays = resolve_weekday_filter(plan)
        if active_workdays is None and (plan.within_work_hours or plan.outside_work_hours):
            active_workdays = work_schedule.weekdays
        schedule_start_minutes = work_schedule.start_minutes
        schedule_end_minutes = work_schedule.end_minutes
        if plan.date:
            target_date = parse_date(plan.date)
            windows = calendar.find_free_slots_in_range(
                target_date,
                target_date,
                duration_minutes=plan.duration_minutes,
                outside_work_hours=plan.outside_work_hours,
                workdays=active_workdays,
                workday_start_minutes=schedule_start_minutes,
                workday_end_minutes=schedule_end_minutes,
                limit=64,
            )
        elif plan.date_from and plan.date_to:
            windows = calendar.find_free_slots_in_range(
                parse_date(plan.date_from),
                parse_date(plan.date_to),
                duration_minutes=plan.duration_minutes,
                outside_work_hours=plan.outside_work_hours,
                workdays=active_workdays,
                workday_start_minutes=schedule_start_minutes,
                workday_end_minutes=schedule_end_minutes,
                limit=64,
            )
        else:
            return {"text": plan.reply or "Уточните дату или диапазон дат.", "needs_clarification": True}
        windows = apply_period_filters(
            windows,
            preferred_period=plan.preferred_period,
            excluded_period=plan.excluded_period,
        )
        windows = filter_windows_by_min_duration(windows, duration_minutes=plan.duration_minutes)
        if plan.date_from and plan.date_to:
            slots = select_diverse_windows(windows, max_results=8, max_per_day=1)
        else:
            slots = windows[:8]
        if not slots:
            if plan.date:
                suffix = " вне рабочего времени" if plan.outside_work_hours else ""
                return {"text": f"На {plan.date} нет свободных окон{suffix} на {plan.duration_minutes} минут."}
            return {"text": "Свободных вариантов в этом диапазоне не нашлось."}
        if plan.date:
            header = f"Свободные окна на {plan.date}"
            if plan.outside_work_hours:
                header += " вне рабочего времени"
            return {
                "text": "\n".join(
                    [f"{header}:"] + [f"{s:%H:%M}-{e:%H:%M}" for s, e in slots]
                )
            }
        result = {
            "text": format_slot_suggestions(
                slots,
                duration_minutes=plan.duration_minutes,
                title=plan.title,
                outside_work_hours=plan.outside_work_hours,
            )
        }
        if plan.title:
            pending_slot_selections[chat_id] = PendingSlotSelection(
                title=plan.title,
                duration_minutes=plan.duration_minutes,
                event_group=plan.event_group,
                candidates=tuple(slots[:8]),
            )
            result["reply_markup"] = slot_suggestions_markup(slots[:8])
        return result

    if plan.action == "create_event":
        return build_event_create_result(chat_id, plan, user_id=user_id)

    if plan.action == "update_event":
        return build_event_update_result(chat_id, plan, user_id=user_id)

    if plan.action == "delete_event":
        return build_event_delete_result(chat_id, plan, user_id=user_id)

    if plan.action == "create_task":
        return build_task_create_result(chat_id, plan, user_id=user_id)

    if plan.action == "update_task":
        return build_task_update_result(chat_id, plan, user_id=user_id)

    if plan.action == "delete_task":
        return build_task_delete_result(chat_id, plan, user_id=user_id)

    if plan.action == "complete_task":
        return build_task_complete_result(chat_id, plan, user_id=user_id)

    return {
        "text": plan.reply or "Не понял запрос. Сформулируйте точнее.",
        "needs_clarification": True,
    }


def build_event_create_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    if not plan.date or not plan.time or not plan.title:
        return {
            "text": plan.reply or "Для события нужны дата, время и название.",
            "needs_clarification": True,
        }
    start_at = parse_datetime(plan.date, plan.time)
    conflicts = tuple(get_calendar_service(user_id).find_conflicts(start_at, plan.duration_minutes))
    draft = EventCreateDraft(
        kind="event_create",
        title=plan.title,
        start_at=start_at,
        duration_minutes=plan.duration_minutes,
        group_name=resolve_group_name(plan.event_group),
        conflicts=conflicts,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_event_create_draft(draft), "draft": draft}


def build_event_update_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    target = plan.target_title or plan.title
    if not target:
        return {"text": plan.reply or "Уточните, какое событие нужно изменить.", "needs_clarification": True}
    candidates = get_calendar_service(user_id).search_events(target, parse_date(plan.date) if plan.date else None)
    if not candidates:
        return {"text": f'Событие "{target}" не найдено.'}
    if len(candidates) > 1:
        pending_selections[chat_id] = PendingSelection(
            kind="event_update",
            plan=plan,
            candidates=tuple(candidates[:5]),
        )
        options = "\n".join(f"- {e.start:%d.%m %H:%M} {e.title}" for e in candidates[:5])
        return {
            "text": f'Нашлось несколько событий "{target}". Уточните дату или название:\n{options}',
            "needs_clarification": True,
        }
    current = candidates[0]
    has_changes = bool(plan.date or plan.time or plan.new_title or plan.event_group)
    if not has_changes and plan.duration_minutes == 60:
        return {"text": plan.reply or f'На когда перенести событие "{current.title}"?', "needs_clarification": True}
    new_start_at = parse_datetime(plan.date, plan.time) if plan.date and plan.time else current.start
    new_title = plan.new_title or plan.title or current.title
    duration = plan.duration_minutes if (plan.date or plan.time or plan.duration_minutes != 60) else int((current.end - current.start).total_seconds() // 60)
    conflicts = tuple(get_calendar_service(user_id).find_conflicts(new_start_at, duration, exclude_event_id=current.event_id))
    draft = EventUpdateDraft(
        kind="event_update",
        event_id=current.event_id,
        original_title=current.title,
        new_title=new_title,
        start_at=new_start_at,
        duration_minutes=duration,
        group_name=resolve_group_name(plan.event_group),
        conflicts=conflicts,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_event_update_draft(draft), "draft": draft}


def build_event_delete_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    target = plan.target_title or plan.title
    if not target:
        return {"text": plan.reply or "Уточните, какое событие удалить.", "needs_clarification": True}
    candidates = get_calendar_service(user_id).search_events(target, parse_date(plan.date) if plan.date else None)
    if not candidates:
        return {"text": f'Событие "{target}" не найдено.'}
    if len(candidates) > 1:
        pending_selections[chat_id] = PendingSelection(
            kind="event_delete",
            plan=plan,
            candidates=tuple(candidates[:5]),
        )
        options = "\n".join(f"- {e.start:%d.%m %H:%M} {e.title}" for e in candidates[:5])
        return {
            "text": f'Нашлось несколько событий "{target}". Уточните дату:\n{options}',
            "needs_clarification": True,
        }
    event = candidates[0]
    draft = EventDeleteDraft(kind="event_delete", event_id=event.event_id, title=event.title, start_at=event.start)
    pending_drafts[chat_id] = draft
    return {"text": format_event_delete_draft(draft), "draft": draft}


def resolve_task_target(plan: AssistantPlan, user_id: int | None = None) -> tuple[str | None, str | None]:
    target = plan.target_title or plan.title
    if not target:
        return None, "Уточните название задачи."
    try:
        matches = get_tasks_service(user_id).search_tasks(target, tasklist_name=plan.task_list or None)
    except Exception as exc:
        return None, str(exc)
    if not matches:
        return None, f'Задача "{target}" не найдена.'
    if len(matches) > 1:
        options = "\n".join(f'- {item.tasklist_title}: {item.title}' for item in matches[:5])
        return None, f'Нашлось несколько задач "{target}". Уточните список или название:\n{options}'
    return matches[0], None


def build_task_create_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    if not plan.title:
        return {"text": plan.reply or "Для задачи нужно название.", "needs_clarification": True}
    try:
        task_list_name = (
            get_tasks_service(user_id).resolve_tasklist(plan.task_list)["title"]
            if plan.task_list
            else get_tasks_service(user_id).resolve_tasklist(None)["title"]
        )
    except Exception as exc:
        return {"text": f"Не удалось определить список задач: {exc}"}
    draft = TaskCreateDraft(
        kind="task_create",
        title=plan.title,
        due_date=parse_date(plan.date) if plan.date else None,
        notes=plan.notes,
        task_list_name=task_list_name,
        subtasks=plan.subtasks,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_task_create_draft(draft), "draft": draft}


def build_task_update_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    try:
        matches = get_tasks_service(user_id).search_tasks(
            (plan.target_title or plan.title),
            tasklist_name=plan.task_list or None,
            show_completed=False,
        )
    except Exception as exc:
        return {"text": str(exc), "needs_clarification": True}
    matches = [item for item in matches if item.status != "completed"]
    if not matches:
        return {"text": f'Задача "{plan.target_title or plan.title}" не найдена.', "needs_clarification": True}
    if len(matches) > 1:
        pending_selections[chat_id] = PendingSelection(
            kind="task_update",
            plan=plan,
            candidates=tuple(matches[:5]),
        )
        options = "\n".join(f'- {item.tasklist_title}: {item.title}' for item in matches[:5])
        return {"text": f'Нашлось несколько задач "{plan.target_title or plan.title}". Уточните список или название:\n{options}', "needs_clarification": True}
    match = matches[0]
    error = None
    if error:
        return {"text": error, "needs_clarification": True}
    assert isinstance(match, TaskItem)
    draft = TaskUpdateDraft(
        kind="task_update",
        task_id=match.task_id,
        task_list_name=match.tasklist_title,
        original_title=match.title,
        new_title=plan.new_title or plan.title or match.title,
        due_date=parse_date(plan.date) if plan.date else (date.fromisoformat(match.due[:10]) if match.due else None),
        notes=plan.notes if plan.notes else match.notes,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_task_update_draft(draft), "draft": draft}


def build_task_delete_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    target = plan.target_title or plan.title
    if not target:
        return {"text": "Уточните название задачи.", "needs_clarification": True}
    try:
        matches = get_tasks_service(user_id).search_tasks(
            target,
            tasklist_name=plan.task_list or None,
            show_completed=False,
        )
    except Exception as exc:
        return {"text": str(exc), "needs_clarification": True}
    matches = [item for item in matches if item.status != "completed"]
    if not matches:
        return {"text": f'Задача "{target}" не найдена.', "needs_clarification": True}
    if len(matches) > 1:
        pending_selections[chat_id] = PendingSelection(
            kind="task_delete",
            plan=plan,
            candidates=tuple(matches[:5]),
        )
        options = "\n".join(f'- {item.tasklist_title}: {item.title}' for item in matches[:5])
        return {"text": f'Нашлось несколько задач "{target}". Уточните список или название:\n{options}', "needs_clarification": True}
    match = matches[0]
    assert isinstance(match, TaskItem)
    draft = TaskDeleteDraft(
        kind="task_delete",
        task_id=match.task_id,
        task_list_name=match.tasklist_title,
        title=match.title,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_task_delete_draft(draft), "draft": draft}


def build_task_complete_result(chat_id: int, plan: AssistantPlan, user_id: int | None = None) -> dict:
    target = plan.target_title or plan.title
    if not target:
        return {"text": "Уточните название задачи.", "needs_clarification": True}
    try:
        matches = get_tasks_service(user_id).search_tasks(
            target,
            tasklist_name=plan.task_list or None,
            show_completed=False,
        )
    except Exception as exc:
        return {"text": str(exc), "needs_clarification": True}
    matches = [item for item in matches if item.status != "completed"]
    if not matches:
        return {"text": f'Задача "{target}" не найдена.', "needs_clarification": True}
    if len(matches) > 1:
        pending_selections[chat_id] = PendingSelection(
            kind="task_complete",
            plan=plan,
            candidates=tuple(matches[:5]),
        )
        options = "\n".join(f'- {item.tasklist_title}: {item.title}' for item in matches[:5])
        return {"text": f'Нашлось несколько задач "{target}". Уточните список или название:\n{options}', "needs_clarification": True}
    match = matches[0]
    assert isinstance(match, TaskItem)
    draft = TaskCompleteDraft(
        kind="task_complete",
        task_id=match.task_id,
        task_list_name=match.tasklist_title,
        title=match.title,
    )
    pending_drafts[chat_id] = draft
    return {"text": format_task_complete_draft(draft), "draft": draft}


async def finalize_pending_draft(update: Update, confirmed: bool, query_message=None) -> None:
    chat_id = update.effective_chat.id
    user_id = get_actor_id(update)
    calendar = get_calendar_service(user_id)
    tasks = get_tasks_service(user_id)
    draft = pending_drafts.pop(chat_id, None)
    target_message = query_message or update.message
    if not draft:
        await safe_reply_text(target_message, "Черновик не найден.")
        return
    if not confirmed:
        await safe_reply_text(target_message, "Черновик отменен.")
        return
    try:
        if isinstance(draft, EventCreateDraft):
            color_id = group_store.resolve_color_id(draft.group_name) if draft.group_name else None
            created = await asyncio.to_thread(
                calendar.create_event,
                draft.title,
                draft.start_at,
                draft.duration_minutes,
                color_id,
            )
            reply = f'Событие создано: "{created.title}" на {created.start:%d.%m.%Y %H:%M}'
            if created.html_link:
                reply += f"\n{created.html_link}"
            reply += "\nГотово ✨"
            await safe_reply_text(target_message, reply)
            return
        if isinstance(draft, EventUpdateDraft):
            color_id = group_store.resolve_color_id(draft.group_name) if draft.group_name else None
            updated = await asyncio.to_thread(
                calendar.update_event,
                draft.event_id,
                new_title=draft.new_title,
                new_start_at=draft.start_at,
                duration_minutes=draft.duration_minutes,
                color_id=color_id,
            )
            await safe_reply_text(
                target_message,
                f'Событие обновлено: "{updated.title}" на {updated.start:%d.%m.%Y %H:%M}\nГотово ✨'
            )
            return
        if isinstance(draft, EventDeleteDraft):
            await asyncio.to_thread(calendar.delete_event, draft.event_id)
            await safe_reply_text(target_message, f'Событие "{draft.title}" удалено. 🗑')
            return
        if isinstance(draft, TaskCreateDraft):
            task_id, tasklist_title = await asyncio.to_thread(
                tasks.create_task_with_subtasks,
                draft.title,
                draft.due_date,
                draft.notes,
                draft.task_list_name,
                list(draft.subtasks),
            )
            await safe_reply_text(
                target_message,
                f'Задача создана: "{draft.title}"\nСписок: {tasklist_title}\nTask ID: {task_id}\nГотово ✅'
            )
            return
        if isinstance(draft, TaskUpdateDraft):
            updated = await asyncio.to_thread(
                tasks.update_task,
                draft.task_id,
                draft.task_list_name,
                new_title=draft.new_title,
                due_date=draft.due_date,
                notes=draft.notes,
            )
            await safe_reply_text(
                target_message,
                f'Задача обновлена: "{updated.title}"\nСписок: {updated.tasklist_title}\nГотово ✨'
            )
            return
        if isinstance(draft, TaskDeleteDraft):
            await asyncio.to_thread(tasks.delete_task, draft.task_id, draft.task_list_name)
            await safe_reply_text(target_message, f'Задача "{draft.title}" удалена. 🗑')
            return
        if isinstance(draft, TaskCompleteDraft):
            updated = await asyncio.to_thread(tasks.complete_task, draft.task_id, draft.task_list_name)
            await safe_reply_text(
                target_message,
                f'Задача "{updated.title}" отмечена выполненной. 🎉'
            )
            return
    except Exception as exc:
        await safe_reply_text(target_message, f"Не удалось выполнить действие: {exc}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled telegram application error", exc_info=context.error)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global work_schedule
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user_id = get_actor_id(update)
    calendar = get_calendar_service(user_id)
    draft = pending_drafts.get(chat_id)

    if query.data.startswith("slot:"):
        pending_slot = pending_slot_selections.get(chat_id)
        if not pending_slot:
            await query.edit_message_text("Подбор слотов уже неактуален.")
            return
        try:
            slot_index = int(query.data.split(":", maxsplit=1)[1]) - 1
            start_at, _end_at = pending_slot.candidates[slot_index]
        except (ValueError, IndexError):
            await query.edit_message_text("Этот слот уже недоступен.")
            return
        conflicts = tuple(calendar.find_conflicts(start_at, pending_slot.duration_minutes))
        created_draft = EventCreateDraft(
            kind="event_create",
            title=pending_slot.title,
            start_at=start_at,
            duration_minutes=pending_slot.duration_minutes,
            group_name=resolve_group_name(pending_slot.event_group),
            conflicts=conflicts,
        )
        pending_drafts[chat_id] = created_draft
        pending_slot_selections.pop(chat_id, None)
        await query.edit_message_text(
            format_event_create_draft(created_draft),
            reply_markup=event_markup(created_draft),
        )
        return

    if query.data.startswith("worktime:"):
        try:
            parts = query.data.split(":")
            if query.data == "worktime:reset":
                work_schedule = work_schedule_store.save(
                    WorkSchedule(
                        weekdays=(0, 1, 2, 3, 4),
                        start_minutes=9 * 60,
                        end_minutes=18 * 60,
                    )
                )
                await query.edit_message_text(worktime_text(), reply_markup=worktime_menu_markup())
                return
            if len(parts) >= 3 and parts[1] == "menu":
                section = parts[2]
                if section == "root":
                    await query.edit_message_text(worktime_text(), reply_markup=worktime_menu_markup())
                    return
                if section == "days":
                    await query.edit_message_text(
                        worktime_text() + "\n\nВыберите рабочие дни:",
                        reply_markup=worktime_days_markup(),
                    )
                    return
                if section == "hours":
                    await query.edit_message_text(
                        worktime_text() + "\n\nВыберите рабочие часы:",
                        reply_markup=worktime_hours_markup(),
                    )
                    return
            if len(parts) >= 3 and parts[1] == "toggle_day":
                day = int(parts[2])
                current_days = set(work_schedule.weekdays)
                if day in current_days and len(current_days) > 1:
                    current_days.remove(day)
                else:
                    current_days.add(day)
                work_schedule = work_schedule_store.save(
                    WorkSchedule(
                        weekdays=tuple(sorted(current_days)),
                        start_minutes=work_schedule.start_minutes,
                        end_minutes=work_schedule.end_minutes,
                    )
                )
                await query.edit_message_text(
                    worktime_text() + "\n\nВыберите рабочие дни:",
                    reply_markup=worktime_days_markup(),
                )
                return
            if len(parts) >= 3 and parts[1] == "set_hours":
                start_text, end_text = parts[2].split("-", maxsplit=1)
                work_schedule = work_schedule_store.save(
                    WorkSchedule(
                        weekdays=work_schedule.weekdays,
                        start_minutes=parse_time_to_minutes(start_text),
                        end_minutes=parse_time_to_minutes(end_text),
                    )
                )
                await query.edit_message_text(
                    worktime_text() + "\n\nВыберите рабочие часы:",
                    reply_markup=worktime_hours_markup(),
                )
                return
            await query.edit_message_text(
                worktime_text(),
                reply_markup=worktime_menu_markup(),
            )
        except Exception as exc:
            await query.edit_message_text(f"Не удалось сохранить рабочее время: {exc}")
        return

    if query.data == "draft:cancel":
        pending_drafts.pop(chat_id, None)
        await query.edit_message_text("Черновик отменен.")
        return
    if not draft:
        await query.edit_message_text("Черновик уже неактуален.")
        return
    if query.data == "draft:confirm":
        await query.edit_message_text("Подтверждение получено. Выполняю...")
        await finalize_pending_draft(update, confirmed=True, query_message=query.message)
        return
    if query.data == "group:none" and isinstance(draft, (EventCreateDraft, EventUpdateDraft)):
        draft.group_name = ""
        pending_drafts[chat_id] = draft
        text = format_event_create_draft(draft) if isinstance(draft, EventCreateDraft) else format_event_update_draft(draft)
        await query.edit_message_text(text, reply_markup=event_markup(draft))
        return
    if query.data.startswith("group:") and isinstance(draft, (EventCreateDraft, EventUpdateDraft)):
        draft.group_name = query.data.split(":", 1)[1]
        pending_drafts[chat_id] = draft
        text = format_event_create_draft(draft) if isinstance(draft, EventCreateDraft) else format_event_update_draft(draft)
        await query.edit_message_text(text, reply_markup=event_markup(draft))
        return
    if query.data.startswith("conflict:") and isinstance(draft, (EventCreateDraft, EventUpdateDraft)):
        raw_index = query.data.split(":", 1)[1]
        try:
            conflict_index = int(raw_index) - 1
            conflict = draft.conflicts[conflict_index]
        except (ValueError, IndexError):
            await query.edit_message_text("Пересечение уже неактуально.")
            return
        conflict_id = conflict.event_id
        await asyncio.to_thread(calendar.delete_event, conflict_id)
        draft.conflicts = tuple(item for item in draft.conflicts if item.event_id != conflict_id)
        pending_drafts[chat_id] = draft
        text = format_event_create_draft(draft) if isinstance(draft, EventCreateDraft) else format_event_update_draft(draft)
        await query.edit_message_text(text, reply_markup=event_markup(draft))
        return


def main() -> None:
    builder = Application.builder().token(require_token())
    if settings.telegram_proxy_url:
        builder = builder.request(HTTPXRequest(proxy=settings.telegram_proxy_url))
        builder = builder.get_updates_request(
            HTTPXRequest(
                proxy=settings.telegram_proxy_url,
                connection_pool_size=1,
                read_timeout=30.0,
                write_timeout=30.0,
                connect_timeout=30.0,
                pool_timeout=30.0,
            )
        )
    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("connect_google", connect_google_command))
    application.add_handler(CommandHandler("disconnect_google", disconnect_google_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("slots", slots_command))
    application.add_handler(CommandHandler("groups", groups_command))
    application.add_handler(CommandHandler("group_add", group_add_command))
    application.add_handler(CommandHandler("group_delete", group_delete_command))
    application.add_handler(CommandHandler("worktime", worktime_command))
    application.add_handler(CommandHandler("worktime_set", worktime_set_command))
    application.add_handler(CommandHandler("tasklists", tasklists_command))
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    main()
