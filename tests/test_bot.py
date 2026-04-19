from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import bot
from dayflow.assistant_service import AssistantPlan, AssistantService, AssistantServiceError
from dayflow.calendar_service import CalendarEvent
from dayflow.calendar_service import GoogleCalendarService
from dayflow.config import Settings
from dayflow.tasks_service import TaskItem
from dayflow.timezone_utils import get_timezone


TZ = get_timezone(bot.settings.timezone)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[dict] = []

    async def reply_text(self, text: str, reply_markup=None) -> None:
        self.replies.append({"text": text, "reply_markup": reply_markup})


class FakeCallbackQuery:
    def __init__(self, data: str, message: FakeMessage | None = None) -> None:
        self.data = data
        self.message = message or FakeMessage()
        self.edits: list[dict] = []

    async def answer(self) -> None:
        return None

    async def edit_message_text(self, text: str, reply_markup=None) -> None:
        self.edits.append({"text": text, "reply_markup": reply_markup})


class FakeCalendarService:
    def __init__(self) -> None:
        self.conflicts: list[CalendarEvent] = []
        self.search_result: list[CalendarEvent] = []
        self.deleted_ids: list[str] = []
        self.created_calls: list[dict] = []
        self.free_slots: list[tuple[datetime, datetime]] = []
        self.range_slots: list[tuple[datetime, datetime]] = []

    def find_conflicts(self, start_at, duration_minutes, exclude_event_id=None):
        return self.conflicts

    def search_events(self, title, target_date=None):
        return self.search_result

    def create_event(self, title, start_at, duration_minutes, color_id):
        self.created_calls.append(
            {
                "title": title,
                "start_at": start_at,
                "duration_minutes": duration_minutes,
                "color_id": color_id,
            }
        )
        return CalendarEvent(
            event_id="created-1",
            title=title,
            start=start_at,
            end=start_at,
            html_link="https://calendar.google.test/event",
            color_id=color_id,
        )

    def find_free_slots(self, target_date, duration_minutes):
        return self.free_slots

    def find_free_slots_in_range(
        self,
        start_date,
        end_date,
        duration_minutes=60,
        outside_work_hours=False,
        preferred_start_hour=None,
        preferred_end_hour=None,
        preferred_start_minutes=None,
        preferred_end_minutes=None,
        workdays=None,
        workday_start_minutes=None,
        workday_end_minutes=None,
        limit=8,
    ):
        return self.range_slots

    def delete_event(self, event_id):
        self.deleted_ids.append(event_id)


class FakeTasksService:
    def __init__(self) -> None:
        self.matches: list[TaskItem] = []
        self.resolve_result = {"id": "default", "title": "Inbox"}
        self.created_calls: list[dict] = []
        self.search_calls: list[dict] = []

    def search_tasks(self, title, tasklist_name=None, show_completed=True):
        self.search_calls.append(
            {
                "title": title,
                "tasklist_name": tasklist_name,
                "show_completed": show_completed,
            }
        )
        return self.matches

    def resolve_tasklist(self, name):
        if isinstance(self.resolve_result, Exception):
            raise self.resolve_result
        return self.resolve_result

    def create_task_with_subtasks(self, title, due_date, notes, task_list_name, subtasks):
        self.created_calls.append(
            {
                "title": title,
                "due_date": due_date,
                "notes": notes,
                "task_list_name": task_list_name,
                "subtasks": subtasks,
            }
        )
        return "task-1", task_list_name


def make_event(event_id: str, title: str, hour: int) -> CalendarEvent:
    start = datetime(2026, 3, 24, hour, 0, tzinfo=TZ)
    end = datetime(2026, 3, 24, hour + 1, 0, tzinfo=TZ)
    return CalendarEvent(event_id=event_id, title=title, start=start, end=end, html_link="")


def make_task(task_id: str, title: str, tasklist_title: str = "Inbox", status: str = "needsAction") -> TaskItem:
    return TaskItem(
        task_id=task_id,
        title=title,
        tasklist_id="list-1",
        tasklist_title=tasklist_title,
        notes="",
        due="",
        status=status,
    )


def make_update(message=None):
    message = message or FakeMessage()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        message=message,
    )


def test_build_event_create_result_stores_draft_and_resolves_group(monkeypatch):
    calendar = FakeCalendarService()
    calendar.conflicts = [make_event("conflict-1", "Daily sync", 11)]
    monkeypatch.setattr(bot, "calendar_service", calendar)
    bot.group_store.add_group("Работа", "9")

    plan = AssistantPlan(
        action="create_event",
        reply="",
        date="2026-03-24",
        time="10:30",
        title="Созвон с клиентом",
        duration_minutes=45,
        event_group="работа",
    )

    result = bot.build_event_create_result(123, plan)

    assert "Создать событие" in result["text"]
    draft = result["draft"]
    assert isinstance(draft, bot.EventCreateDraft)
    assert draft.group_name == "Работа"
    assert draft.conflicts == tuple(calendar.conflicts)
    assert bot.pending_drafts[123] == draft


def test_build_event_update_result_reports_ambiguous_candidates(monkeypatch):
    calendar = FakeCalendarService()
    calendar.search_result = [
        make_event("1", "Встреча с клиентом", 10),
        make_event("2", "Встреча с клиентом", 15),
    ]
    monkeypatch.setattr(bot, "calendar_service", calendar)

    plan = AssistantPlan(action="update_event", reply="", target_title="Встреча с клиентом")

    result = bot.build_event_update_result(123, plan)

    assert "Нашлось несколько событий" in result["text"]
    assert 123 not in bot.pending_drafts


def test_build_event_update_result_requests_new_time_for_single_match(monkeypatch):
    calendar = FakeCalendarService()
    calendar.search_result = [make_event("1", "Встреча с клиентом", 10)]
    monkeypatch.setattr(bot, "calendar_service", calendar)

    result = bot.build_event_update_result(
        123,
        AssistantPlan(action="update_event", reply="", target_title="Встреча с клиентом"),
    )

    assert result["needs_clarification"] is True
    assert 'На когда перенести событие "Встреча с клиентом"?' == result["text"]
    assert 123 not in bot.pending_drafts


def test_resolve_task_target_reports_multiple_matches(monkeypatch):
    tasks = FakeTasksService()
    tasks.matches = [make_task("1", "Отчет"), make_task("2", "Отчет", "Work")]
    monkeypatch.setattr(bot, "tasks_service", tasks)

    match, error = bot.resolve_task_target(
        AssistantPlan(action="delete_task", reply="", target_title="Отчет")
    )

    assert match is None
    assert 'Нашлось несколько задач "Отчет"' in error


def test_build_task_create_result_uses_default_task_list(monkeypatch):
    tasks = FakeTasksService()
    monkeypatch.setattr(bot, "tasks_service", tasks)

    plan = AssistantPlan(
        action="create_task",
        reply="",
        title="Оплатить интернет",
        date="2026-03-25",
        notes="до обеда",
        subtasks=("проверить сумму", "оплатить"),
    )

    result = bot.build_task_create_result(123, plan)

    draft = result["draft"]
    assert isinstance(draft, bot.TaskCreateDraft)
    assert draft.task_list_name == "Inbox"
    assert draft.due_date == date(2026, 3, 25)
    assert bot.pending_drafts[123] == draft


def test_build_task_complete_result_excludes_completed_tasks(monkeypatch):
    tasks = FakeTasksService()
    tasks.matches = [
        make_task("1", "Оплатить интернет", "Работа"),
        make_task("2", "Оплатить интернет", "Работа"),
        make_task("3", "Оплатить интернет", "Работа"),
        make_task("4", "Оплатить интернет", "Работа", status="completed"),
    ]
    monkeypatch.setattr(bot, "tasks_service", tasks)

    result = bot.build_task_complete_result(
        123,
        AssistantPlan(action="complete_task", reply="", target_title="Оплатить интернет"),
    )

    assert result["needs_clarification"] is True
    assert tasks.search_calls[-1]["show_completed"] is False
    assert "Нашлось несколько задач" in result["text"]
    assert result["text"].count("Оплатить интернет") == 4


def test_build_task_update_result_excludes_completed_tasks(monkeypatch):
    tasks = FakeTasksService()
    tasks.matches = [
        make_task("1", "Оплатить интернет", "Работа"),
        make_task("2", "Оплатить интернет", "Работа"),
        make_task("3", "Оплатить интернет", "Работа"),
        make_task("4", "Оплатить интернет", "Работа", status="completed"),
    ]
    monkeypatch.setattr(bot, "tasks_service", tasks)

    result = bot.build_task_update_result(
        123,
        AssistantPlan(
            action="update_task",
            reply="",
            target_title="Оплатить интернет",
            new_title="Оплатить интернет и мобильную связь",
        ),
    )

    assert result["needs_clarification"] is True
    assert tasks.search_calls[-1]["show_completed"] is False
    assert "Нашлось несколько задач" in result["text"]
    assert result["text"].count("Оплатить интернет") == 4


def test_build_task_delete_result_excludes_completed_tasks(monkeypatch):
    tasks = FakeTasksService()
    tasks.matches = [
        make_task("1", "Оплатить интернет", "Работа"),
        make_task("2", "Оплатить интернет", "Работа"),
        make_task("3", "Оплатить интернет", "Работа"),
        make_task("4", "Оплатить интернет", "Работа", status="completed"),
    ]
    monkeypatch.setattr(bot, "tasks_service", tasks)

    result = bot.build_task_delete_result(
        123,
        AssistantPlan(action="delete_task", reply="", target_title="Оплатить интернет"),
    )

    assert result["needs_clarification"] is True
    assert tasks.search_calls[-1]["show_completed"] is False
    assert "Нашлось несколько задач" in result["text"]
    assert result["text"].count("Оплатить интернет") == 4


def test_handle_natural_language_suggests_slots_for_flexible_event_request(monkeypatch):
    calendar = FakeCalendarService()
    calendar.range_slots = [
        (
            datetime(2026, 4, 15, 19, 0, tzinfo=TZ),
            datetime(2026, 4, 15, 19, 30, tzinfo=TZ),
        ),
        (
            datetime(2026, 4, 18, 10, 0, tzinfo=TZ),
            datetime(2026, 4, 18, 10, 30, tzinfo=TZ),
        ),
    ]
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="create_event",
                reply="",
                title="Запись на брови",
                date_from="2026-04-14",
                date_to="2026-04-21",
                duration_minutes=30,
                outside_work_hours=True,
            )
        ),
    )

    result = bot.handle_natural_language(123, "Создай запись на брови, примерно через 3-4 недели")

    assert 'Нашла варианты для "Запись на брови" вне рабочего времени' in result["text"]
    assert "- 15.04 19:00-19:30" in result["text"]
    assert result["reply_markup"] is not None


def test_callback_handler_turns_slot_into_event_draft(monkeypatch):
    calendar = FakeCalendarService()
    monkeypatch.setattr(bot, "calendar_service", calendar)
    bot.pending_slot_selections[123] = bot.PendingSlotSelection(
        title="Запись на брови",
        duration_minutes=30,
        event_group="",
        candidates=(
            (
                datetime(2026, 4, 15, 19, 0, tzinfo=TZ),
                datetime(2026, 4, 15, 22, 0, tzinfo=TZ),
            ),
        ),
    )
    query = FakeCallbackQuery("slot:1")
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        callback_query=query,
        message=None,
    )

    asyncio.run(bot.callback_handler(update, None))

    assert isinstance(bot.pending_drafts[123], bot.EventCreateDraft)
    assert bot.pending_drafts[123].start_at == datetime(2026, 4, 15, 19, 0, tzinfo=TZ)
    assert 'Создать событие "Запись на брови"' in query.edits[0]["text"]


def test_callback_handler_toggles_workday(monkeypatch):
    original_schedule = bot.work_schedule
    bot.work_schedule = SimpleNamespace(weekdays=(0, 1, 2, 3, 4), start_minutes=540, end_minutes=1080)
    saved = {}

    def fake_save(schedule):
        saved["schedule"] = schedule
        return schedule

    monkeypatch.setattr(bot.work_schedule_store, "save", fake_save)
    query = FakeCallbackQuery("worktime:toggle_day:5")
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        callback_query=query,
        message=None,
    )

    asyncio.run(bot.callback_handler(update, None))

    assert 5 in saved["schedule"].weekdays
    assert "Рабочее время:" in query.edits[0]["text"]
    bot.work_schedule = original_schedule


def test_callback_handler_resets_worktime(monkeypatch):
    original_schedule = bot.work_schedule
    bot.work_schedule = SimpleNamespace(weekdays=(0, 1, 2, 3, 4, 5), start_minutes=600, end_minutes=1140)
    saved = {}

    def fake_save(schedule):
        saved["schedule"] = schedule
        return schedule

    monkeypatch.setattr(bot.work_schedule_store, "save", fake_save)
    query = FakeCallbackQuery("worktime:reset")
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        callback_query=query,
        message=None,
    )

    asyncio.run(bot.callback_handler(update, None))

    assert saved["schedule"].weekdays == (0, 1, 2, 3, 4)
    assert saved["schedule"].start_minutes == 540
    assert saved["schedule"].end_minutes == 1080
    bot.work_schedule = original_schedule


def test_handle_natural_language_detects_outside_work_hours_from_message(monkeypatch):
    calendar = FakeCalendarService()
    calendar.range_slots = [
        (
            datetime(2026, 4, 15, 19, 0, tzinfo=TZ),
            datetime(2026, 4, 15, 19, 30, tzinfo=TZ),
        ),
    ]
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="find_free_slots",
                reply="",
                title="Брови",
                date_from="2026-04-14",
                date_to="2026-04-21",
                duration_minutes=30,
                outside_work_hours=False,
            )
        ),
    )

    result = bot.handle_natural_language(
        123,
        "Создай запись на брови через 3-4 недели, 30мин, не в рабочее время",
    )

    assert "- 15.04 19:00-19:30" in result["text"]
    assert result["reply_markup"] is not None


def test_calendar_window_clips_events_to_requested_hours():
    settings = bot.settings
    service = GoogleCalendarService(settings)
    service._service = object()
    service.list_events_for_day = lambda _: [
        CalendarEvent(
            event_id="1",
            title="Поздний созвон",
            start=datetime(2026, 4, 17, 19, 0, tzinfo=TZ),
            end=datetime(2026, 4, 17, 20, 0, tzinfo=TZ),
            html_link="",
        )
    ]

    slots = service.find_free_slots_in_window(
        date(2026, 4, 17),
        duration_minutes=30,
        start_hour=0,
        end_hour=9,
    )

    assert slots == [
        (
            datetime(2026, 4, 17, 0, 0, tzinfo=TZ),
            datetime(2026, 4, 17, 9, 0, tzinfo=TZ),
        )
    ]


def test_handle_natural_language_prefers_evening_slots(monkeypatch):
    calendar = FakeCalendarService()
    calendar.range_slots = [
        (
            datetime(2026, 4, 17, 19, 0, tzinfo=TZ),
            datetime(2026, 4, 17, 19, 30, tzinfo=TZ),
        ),
        (
            datetime(2026, 4, 18, 20, 0, tzinfo=TZ),
            datetime(2026, 4, 18, 20, 30, tzinfo=TZ),
        ),
    ]
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="find_free_slots",
                reply="",
                title="Брови",
                date_from="2026-04-14",
                date_to="2026-04-21",
                duration_minutes=30,
                outside_work_hours=True,
            )
        ),
    )

    result = bot.handle_natural_language(
        123,
        "Создай запись на брови, примерно через 3-4 недели, длительность 30мин, вечером, в нерабочее время",
    )

    assert "- 17.04 19:00-19:30" in result["text"]
    assert "- 18.04 20:00-20:30" in result["text"]
    assert "20:00-00:00" not in result["text"]


def test_select_diverse_slots_spreads_results_across_days():
    slots = [
        (datetime(2026, 4, 18, 19, 0, tzinfo=TZ), datetime(2026, 4, 18, 19, 30, tzinfo=TZ)),
        (datetime(2026, 4, 18, 19, 30, tzinfo=TZ), datetime(2026, 4, 18, 20, 0, tzinfo=TZ)),
        (datetime(2026, 4, 18, 20, 0, tzinfo=TZ), datetime(2026, 4, 18, 20, 30, tzinfo=TZ)),
        (datetime(2026, 4, 19, 19, 0, tzinfo=TZ), datetime(2026, 4, 19, 19, 30, tzinfo=TZ)),
        (datetime(2026, 4, 20, 19, 0, tzinfo=TZ), datetime(2026, 4, 20, 19, 30, tzinfo=TZ)),
    ]

    selected = bot.select_diverse_slots(slots, max_results=4, max_per_day=2)

    assert [slot[0].date().isoformat() for slot in selected] == [
        "2026-04-18",
        "2026-04-19",
        "2026-04-20",
        "2026-04-18",
    ]


def test_handle_natural_language_uses_workdays_for_worktime_phrase(monkeypatch):
    calendar = FakeCalendarService()
    captured = {}

    def fake_find_free_slots_in_range(*args, **kwargs):
        captured.update(kwargs)
        return [
            (
                datetime(2026, 4, 20, 11, 0, tzinfo=TZ),
                datetime(2026, 4, 20, 11, 30, tzinfo=TZ),
            )
        ]

    monkeypatch.setattr(calendar, "find_free_slots_in_range", fake_find_free_slots_in_range)
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="find_free_slots",
                reply="",
                title="Брови",
                date_from="2026-04-19",
                date_to="2026-04-21",
                duration_minutes=30,
            )
        ),
    )
    monkeypatch.setattr(
        bot,
        "work_schedule",
        SimpleNamespace(weekdays=(0, 1, 2, 3, 4), start_minutes=600, end_minutes=1140),
    )

    result = bot.handle_natural_language(
        123,
        "Найди запись на брови через 3 недели в рабочее время",
    )

    assert captured["workdays"] == (0, 1, 2, 3, 4)
    assert captured["workday_start_minutes"] == 600
    assert captured["workday_end_minutes"] == 1140
    assert "- 20.04 11:00-11:30" in result["text"]


def test_handle_natural_language_uses_schedule_days_for_non_worktime_phrase(monkeypatch):
    calendar = FakeCalendarService()
    captured = {}

    def fake_find_free_slots_in_range(*args, **kwargs):
        captured.update(kwargs)
        return [
            (
                datetime(2026, 4, 19, 12, 0, tzinfo=TZ),
                datetime(2026, 4, 19, 12, 30, tzinfo=TZ),
            )
        ]

    monkeypatch.setattr(calendar, "find_free_slots_in_range", fake_find_free_slots_in_range)
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="find_free_slots",
                reply="",
                title="Брови",
                date_from="2026-04-19",
                date_to="2026-04-21",
                duration_minutes=30,
                outside_work_hours=False,
            )
        ),
    )
    monkeypatch.setattr(
        bot,
        "work_schedule",
        SimpleNamespace(weekdays=(0, 1, 2, 3, 4), start_minutes=540, end_minutes=1080),
    )

    result = bot.handle_natural_language(
        123,
        "Найди запись на брови через 3 недели в нерабочее время",
    )

    assert captured["outside_work_hours"] is True
    assert captured["workdays"] == (0, 1, 2, 3, 4)
    assert "- 19.04 12:00-12:30" in result["text"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Хочу после офиса", True),
        ("Давай в нерабочие часы", True),
        ("Найди слот вне часов работы", True),
        ("Найди слот когда я после работы", True),
        ("Давай просто днем", False),
    ],
)
def test_apply_message_preferences_detects_non_worktime_synonyms(text, expected):
    plan = AssistantPlan(action="find_free_slots", reply="")

    updated = bot.apply_message_preferences(text, plan)

    assert updated.outside_work_hours is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Найди в рабочие часы", True),
        ("Хочу в часы работы", True),
        ("Запиши в мои рабочие дни", True),
        ("Найди когда я работаю", True),
        ("Найди после работы", False),
    ],
)
def test_detect_worktime_preference_understands_synonyms(text, expected):
    assert bot.detect_worktime_preference(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Найди по выходным", (5, 6)),
        ("Можно в уикенд", (5, 6)),
        ("Давай по рабочим дням", (0, 1, 2, 3, 4)),
        ("Хочу в рабочие дни", (0, 1, 2, 3, 4)),
        ("Когда угодно", None),
    ],
)
def test_detect_weekday_filter_understands_synonyms(text, expected):
    assert bot.detect_weekday_filter(text) == expected


def test_apply_period_filters_respects_not_morning():
    windows = [
        (
            datetime(2026, 4, 4, 9, 0, tzinfo=TZ),
            datetime(2026, 4, 4, 16, 0, tzinfo=TZ),
        ),
    ]

    filtered = bot.apply_period_filters(windows, excluded_period="morning")

    assert filtered == [
        (
            datetime(2026, 4, 4, 12, 0, tzinfo=TZ),
            datetime(2026, 4, 4, 16, 0, tzinfo=TZ),
        )
    ]


def test_normalize_plan_resolves_conflicting_worktime_flags():
    normalized = bot.normalize_plan(
        AssistantPlan(
            action="find_free_slots",
            reply="",
            within_work_hours=True,
            outside_work_hours=True,
        )
    )

    assert normalized.within_work_hours is True
    assert normalized.outside_work_hours is False


def test_normalize_plan_clears_conflicting_periods():
    normalized = bot.normalize_plan(
        AssistantPlan(
            action="find_free_slots",
            reply="",
            preferred_period="morning",
            excluded_period="morning",
        )
    )

    assert normalized.preferred_period == ""
    assert normalized.excluded_period == "morning"


def test_handle_natural_language_excludes_morning_when_user_says_not_morning(monkeypatch):
    calendar = FakeCalendarService()
    calendar.range_slots = [
        (
            datetime(2026, 4, 25, 6, 0, tzinfo=TZ),
            datetime(2026, 4, 25, 9, 0, tzinfo=TZ),
        ),
        (
            datetime(2026, 4, 25, 13, 0, tzinfo=TZ),
            datetime(2026, 4, 25, 14, 0, tzinfo=TZ),
        ),
    ]
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(
            plan=lambda _: AssistantPlan(
                action="find_free_slots",
                reply="",
                title="Тренировка",
                date="2026-04-25",
                duration_minutes=60,
                outside_work_hours=True,
            )
        ),
    )

    result = bot.handle_natural_language(
        123,
        "Создай запись на следующую субботу на тренировку на час, не утром",
    )

    assert "06:00-09:00" not in result["text"]
    assert "13:00-14:00" in result["text"]
    assert "Свободные окна на 2026-04-25 вне рабочего времени" in result["text"]


def test_normalize_plan_drops_invalid_filter_values():
    normalized = bot.normalize_plan(
        AssistantPlan(
            action="find_free_slots",
            reply="",
            preferred_period="lunch",
            excluded_period="late",
            weekday_filter="office_days",
        )
    )

    assert normalized.preferred_period == ""
    assert normalized.excluded_period == ""
    assert normalized.weekday_filter == ""


def test_normalize_plan_moves_create_event_to_find_free_slots_when_only_filters_present():
    normalized = bot.normalize_plan(
        AssistantPlan(
            action="create_event",
            reply="",
            date="2026-04-04",
            duration_minutes=60,
            excluded_period="morning",
        )
    )

    assert normalized.action == "find_free_slots"


def test_finalize_pending_event_create_calls_calendar_service(monkeypatch):
    calendar = FakeCalendarService()
    monkeypatch.setattr(bot, "calendar_service", calendar)
    bot.group_store.add_group("Работа", "9")
    bot.pending_drafts[123] = bot.EventCreateDraft(
        kind="event_create",
        title="Созвон",
        start_at=datetime(2026, 3, 24, 10, 0, tzinfo=TZ),
        duration_minutes=30,
        group_name="Работа",
    )
    update = make_update()

    asyncio.run(bot.finalize_pending_draft(update, confirmed=True))

    assert calendar.created_calls == [
        {
            "title": "Созвон",
            "start_at": datetime(2026, 3, 24, 10, 0, tzinfo=TZ),
            "duration_minutes": 30,
            "color_id": "9",
        }
    ]
    assert "Событие создано" in update.message.replies[0]["text"]
    assert "https://calendar.google.test/event" in update.message.replies[0]["text"]


def test_finalize_pending_task_create_calls_tasks_service(monkeypatch):
    tasks = FakeTasksService()
    monkeypatch.setattr(bot, "tasks_service", tasks)
    bot.pending_drafts[123] = bot.TaskCreateDraft(
        kind="task_create",
        title="Купить подарок",
        due_date=date(2026, 3, 29),
        notes="до выходных",
        task_list_name="Inbox",
        subtasks=("выбрать", "заказать"),
    )
    update = make_update()

    asyncio.run(bot.finalize_pending_draft(update, confirmed=True))

    assert tasks.created_calls == [
        {
            "title": "Купить подарок",
            "due_date": date(2026, 3, 29),
            "notes": "до выходных",
            "task_list_name": "Inbox",
            "subtasks": ["выбрать", "заказать"],
        }
    ]
    assert 'Задача создана: "Купить подарок"' in update.message.replies[0]["text"]


def test_finalize_pending_draft_cancel_replies_and_clears_state():
    bot.pending_drafts[123] = bot.TaskDeleteDraft(
        kind="task_delete",
        task_id="task-1",
        task_list_name="Inbox",
        title="Черновик",
    )
    update = make_update()

    asyncio.run(bot.finalize_pending_draft(update, confirmed=False))

    assert update.message.replies[0]["text"] == "Черновик отменен."
    assert 123 not in bot.pending_drafts


def test_event_markup_uses_short_conflict_callback_data():
    draft = bot.EventCreateDraft(
        kind="event_create",
        title="Тренировка",
        start_at=datetime(2026, 3, 24, 19, 0, tzinfo=TZ),
        duration_minutes=60,
        group_name="",
        conflicts=(
            CalendarEvent(
                event_id="very-long-google-event-id-that-should-not-be-sent-to-telegram-directly",
                title="Созвон",
                start=datetime(2026, 3, 24, 19, 0, tzinfo=TZ),
                end=datetime(2026, 3, 24, 20, 0, tzinfo=TZ),
                html_link="",
            ),
        ),
    )

    markup = bot.event_markup(draft)
    callback_data = markup.inline_keyboard[1][0].callback_data

    assert callback_data == "conflict:1"
    assert len(callback_data.encode("utf-8")) <= 64


def test_assistant_extract_json_accepts_wrapped_payload():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)

    payload = service._extract_json('prefix {"action":"chat","reply":"ok"} suffix')

    assert payload == {"action": "chat", "reply": "ok"}


def test_assistant_extract_payload_prefers_parsed_dict():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)

    payload = service._extract_payload(SimpleNamespace(parsed={"action": "chat", "reply": "ok"}, text=""))

    assert payload == {"action": "chat", "reply": "ok"}


def test_assistant_plan_uses_openrouter_when_api_key_present(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
        openrouter_api_key="openrouter-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_proxy_url="",
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_openrouter_client", lambda: object())
    monkeypatch.setattr(
        service,
        "_generate_openrouter_response",
        lambda *args, **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"action":"create_task","reply":"ok","title":"купить подарок","notes":""}'
                    )
                )
            ]
        ),
    )

    plan = service.plan("Создай задачу купить подарок")

    assert plan.action == "create_task"
    assert plan.title == "купить подарок"


def test_assistant_plan_normalizes_null_fields_from_model(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
        openrouter_api_key="openrouter-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_proxy_url="",
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_openrouter_client", lambda: object())
    monkeypatch.setattr(
        service,
        "_generate_openrouter_response",
        lambda *args, **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"action":"create_task","reply":"ok","date":null,"time":null,'
                            '"title":"подготовить отчет","notes":null,"task_list":"Работа","subtasks":null}'
                        )
                    )
                )
            ]
        ),
    )

    plan = service.plan("Создай задачу в списке Работа: подготовить отчет")

    assert plan.action == "create_task"
    assert plan.date == ""
    assert plan.time == ""
    assert plan.notes == ""
    assert plan.task_list == "Работа"
    assert plan.subtasks == ()


def test_assistant_maps_sdk_quota_error_to_friendly_message():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)
    exc = RuntimeError("429 RESOURCE_EXHAUSTED")
    exc.code = 429
    exc.details = {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}

    error = service._map_gemini_error(exc)

    assert str(error) == (
        "Не могу обработать свободный запрос: закончилась квота Gemini API. "
        "Проверьте тариф/биллинг и повторите позже."
    )


def test_assistant_maps_sdk_auth_error_to_friendly_message():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)
    exc = RuntimeError("403 PERMISSION_DENIED")
    exc.code = 403
    exc.details = {
        "message": "API key not valid. Please pass a valid API key.",
        "status": "PERMISSION_DENIED",
    }

    error = service._map_gemini_error(exc)

    assert str(error) == (
        "Не могу обработать свободный запрос: Gemini API не приняла авторизацию. "
        "Проверьте GEMINI_API_KEY и доступ к модели."
    )


def test_assistant_maps_sdk_invalid_argument_error_to_friendly_message():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)
    exc = RuntimeError("400 INVALID_ARGUMENT")
    exc.code = 400
    exc.details = {
        "message": "Request contains an invalid argument.",
        "status": "INVALID_ARGUMENT",
    }

    error = service._map_gemini_error(exc)

    assert str(error) == (
        "Не могу обработать свободный запрос: Gemini API отклонила запрос. "
        "Проверьте GEMINI_MODEL и параметры запроса."
    )


def test_assistant_maps_sdk_location_error_to_friendly_message():
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)
    exc = RuntimeError("400 FAILED_PRECONDITION")
    exc.code = 400
    exc.details = {
        "message": "User location is not supported for the API use.",
        "status": "FAILED_PRECONDITION",
    }

    error = service._map_gemini_error(exc)

    assert str(error) == (
        "Не могу обработать свободный запрос: Gemini API отклоняет запрос по геолокации. "
        "Обычный VPN в браузере часто не помогает: проверьте, что Python-процесс идет через "
        "поддерживаемый прокси/сервер, и перезапустите бота."
    )


def test_assistant_plan_sets_proxy_env_in_both_cases(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="http://127.0.0.1:12334",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.setattr(service, "_get_client", lambda _: object())

    seen_proxy_env = {}

    def fake_structured_response(*args, **kwargs):
        seen_proxy_env["HTTP_PROXY"] = os.environ.get("HTTP_PROXY")
        seen_proxy_env["HTTPS_PROXY"] = os.environ.get("HTTPS_PROXY")
        seen_proxy_env["http_proxy"] = os.environ.get("http_proxy")
        seen_proxy_env["https_proxy"] = os.environ.get("https_proxy")
        return SimpleNamespace(
            parsed={"action": "chat", "reply": "ok"},
            text='{"action":"chat","reply":"ok"}',
        )

    monkeypatch.setattr(service, "_generate_structured_response", fake_structured_response)

    plan = service.plan("Проверь расписание завтра")

    assert plan.reply == "ok"
    assert seen_proxy_env == {
        "HTTP_PROXY": "http://127.0.0.1:12334",
        "HTTPS_PROXY": "http://127.0.0.1:12334",
        "http_proxy": "http://127.0.0.1:12334",
        "https_proxy": "http://127.0.0.1:12334",
    }
    assert os.environ.get("HTTP_PROXY") is None
    assert os.environ.get("HTTPS_PROXY") is None
    assert os.environ.get("http_proxy") is None
    assert os.environ.get("https_proxy") is None


def test_assistant_plan_retries_without_schema_after_invalid_argument(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)
    invalid_argument = RuntimeError("400 INVALID_ARGUMENT")
    invalid_argument.code = 400
    invalid_argument.details = {
        "message": "Request contains an invalid argument.",
        "status": "INVALID_ARGUMENT",
    }

    monkeypatch.setattr(service, "_get_client", lambda _: object())

    def fake_structured_response(*args, **kwargs):
        raise invalid_argument

    monkeypatch.setattr(service, "_generate_structured_response", fake_structured_response)
    monkeypatch.setattr(
        service,
        "_generate_fallback_response",
        lambda *args, **kwargs: SimpleNamespace(
            parsed={"action": "chat", "reply": "ok"},
            text='{"action":"chat","reply":"ok"}',
        ),
    )

    plan = service.plan("Проверь расписание завтра")

    assert plan.action == "chat"
    assert plan.reply == "ok"


def test_assistant_plan_extracts_task_notes_from_message_when_model_misses_them(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_client", lambda _: object())
    monkeypatch.setattr(
        service,
        "_generate_structured_response",
        lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "action": "create_task",
                "reply": "ok",
                "title": "купить подарок",
                "notes": "",
            },
            text='{"action":"create_task","reply":"ok","title":"купить подарок","notes":""}',
        ),
    )

    plan = service.plan("Создай задачу купить подарок и напиши в заметке позвонить в магазин")

    assert plan.action == "create_task"
    assert plan.title == "купить подарок"
    assert plan.notes == "позвонить в магазин"


def test_assistant_plan_extracts_task_list_notes_and_subtasks_from_structured_task_message(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="test-key",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_client", lambda _: object())
    monkeypatch.setattr(
        service,
        "_generate_structured_response",
        lambda *args, **kwargs: SimpleNamespace(
            parsed={
                "action": "create_task",
                "reply": "ok",
                "title": "подготовить отчет, описание: собрать цифры, подзадачи: написать черновик, проверить данные",
                "notes": "",
                "task_list": "",
                "subtasks": [],
            },
            text=(
                '{"action":"create_task","reply":"ok","title":"подготовить отчет, описание: собрать цифры, '
                'подзадачи: написать черновик, проверить данные","notes":"","task_list":"","subtasks":[]}'
            ),
        ),
    )

    plan = service.plan(
        "Создай задачу в списке Работа: подготовить отчет, описание: собрать цифры, "
        "подзадачи: написать черновик, проверить данные"
    )

    assert plan.action == "create_task"
    assert plan.title == "подготовить отчет"
    assert plan.task_list == "Работа"
    assert plan.notes == "собрать цифры"
    assert plan.subtasks == ("написать черновик", "проверить данные")


def test_assistant_plan_does_not_change_task_title_when_only_notes_are_updated(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
        openrouter_api_key="openrouter-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_proxy_url="",
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_openrouter_client", lambda: object())
    monkeypatch.setattr(
        service,
        "_generate_openrouter_response",
        lambda *args, **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"action":"update_task","reply":"ok","title":"Измени задачу отчет","notes":"собрать новые цифры"}'
                    )
                )
            ]
        ),
    )

    plan = service.plan("Измени задачу отчет, описание: собрать новые цифры")

    assert plan.action == "update_task"
    assert plan.target_title == "отчет"
    assert plan.new_title == ""
    assert plan.notes == "собрать новые цифры"


def test_assistant_plan_update_task_with_task_list_does_not_treat_list_as_new_title(monkeypatch):
    settings = Settings(
        telegram_bot_token="",
        timezone="Europe/Moscow",
        google_calendar_id="primary",
        google_task_list_id="@default",
        event_groups_path="data/event_groups.json",
        google_credentials_path="credentials.json",
        google_token_path="token.json",
        google_tokens_dir="data/google_tokens",
        work_schedule_path="data/work_schedule.json",
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        outbound_proxy_url="",
        telegram_proxy_url="",
        gemini_proxy_url="",
        workday_start_hour=9,
        workday_end_hour=18,
        gemini_debug_logging=False,
        openrouter_api_key="openrouter-key",
        openrouter_model="openai/gpt-4o-mini",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_proxy_url="",
    )
    service = AssistantService(settings)

    monkeypatch.setattr(service, "_get_openrouter_client", lambda: object())
    monkeypatch.setattr(
        service,
        "_generate_openrouter_response",
        lambda *args, **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"action":"update_task","reply":"ok","target_title":"отчет","new_title":"списке Работа","notes":"собрать финальные цифры","task_list":"Работа","date":"2026-04-20"}'
                    )
                )
            ]
        ),
    )

    plan = service.plan("Измени задачу отчет в списке Работа, описание: собрать финальные цифры")

    assert plan.action == "update_task"
    assert plan.target_title == "отчет"
    assert plan.new_title == ""
    assert plan.task_list == "Работа"
    assert plan.notes == "собрать финальные цифры"


def test_process_text_returns_friendly_quota_error(monkeypatch):
    def fake_handle_natural_language(chat_id: int, message_text: str) -> dict:
        raise AssistantServiceError(
            "Не могу обработать свободный запрос: закончилась квота Gemini API. "
            "Проверьте тариф/биллинг и повторите позже."
        )

    monkeypatch.setattr(bot, "handle_natural_language", fake_handle_natural_language)
    update = make_update()

    asyncio.run(bot.process_text(update, "Поставь встречу с клиентом 2026-03-24 в 13:00"))

    assert update.message.replies == [
        {
            "text": (
                "Не могу обработать свободный запрос: закончилась квота Gemini API. "
                "Проверьте тариф/биллинг и повторите позже."
            ),
            "reply_markup": None,
        }
    ]


def test_handle_natural_language_falls_back_to_event_lookup_for_missing_move_time(monkeypatch):
    calendar = FakeCalendarService()
    monkeypatch.setattr(bot, "calendar_service", calendar)
    monkeypatch.setattr(
        bot,
        "assistant_service",
        SimpleNamespace(plan=lambda _: AssistantPlan(action="chat", reply="На когда перенести?")),
    )

    result = bot.handle_natural_language(123, "Перенеси несуществующее событие")

    assert result == {"text": 'Событие "несуществующее событие" не найдено.'}


def test_text_message_reuses_pending_clarification(monkeypatch):
    seen_messages = []

    def fake_handle_natural_language(chat_id: int, message_text: str) -> dict:
        seen_messages.append(message_text)
        if len(seen_messages) == 1:
            return {"text": "На какое время назначить встречу?", "needs_clarification": True}
        return {"text": "Создать событие \"Встреча\"\nДата и время: 24.03.2026 11:00"}

    monkeypatch.setattr(bot, "handle_natural_language", fake_handle_natural_language)

    first_update = make_update(FakeMessage())
    first_update.message.text = "Назначь встречу завтра"
    asyncio.run(bot.text_message(first_update, None))

    second_update = make_update(FakeMessage())
    second_update.message.text = "11:00"
    asyncio.run(bot.text_message(second_update, None))

    assert seen_messages == [
        "Назначь встречу завтра",
        "Назначь встречу завтра\nУточнение пользователя: 11:00",
    ]
    assert second_update.message.replies[0]["text"] == 'Создать событие "Встреча"\nДата и время: 24.03.2026 11:00'


def test_text_message_resolves_pending_event_delete_selection(monkeypatch):
    calendar = FakeCalendarService()
    monkeypatch.setattr(bot, "calendar_service", calendar)
    plan = AssistantPlan(action="delete_event", reply="", target_title="Встреча")
    first = make_event("1", "Встреча", 10)
    second = make_event("2", "Встреча", 12)
    bot.pending_selections[123] = bot.PendingSelection(
        kind="event_delete",
        plan=plan,
        candidates=(first, second),
    )
    update = make_update(FakeMessage())
    update.message.text = "первую удали"

    asyncio.run(bot.text_message(update, None))

    assert isinstance(bot.pending_drafts[123], bot.EventDeleteDraft)
    assert bot.pending_drafts[123].event_id == "1"
    assert 'Удалить событие "Встреча"' in update.message.replies[0]["text"]


def test_extract_selection_index_understands_ordinal_words():
    assert bot.extract_selection_index("первую", 5) == 0
    assert bot.extract_selection_index("первую удали", 5) == 0
    assert bot.extract_selection_index("вторую", 5) == 1
    assert bot.extract_selection_index("3)", 5) == 2
