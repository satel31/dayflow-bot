from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dayflow.auth import GoogleAuthRequiredError, GoogleAuthSession, build_google_auth_session, complete_google_auth
from dayflow.assistant_service import AssistantService
from dayflow.calendar_service import GoogleCalendarService
from dayflow.config import Settings, load_settings
from dayflow.group_store import build_event_group_store
from dayflow.tasks_service import GoogleTasksService
from dayflow.timezone_utils import get_timezone


STATIC_DIR = Path(__file__).with_name("static")


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_date: date | None = None
    notes: str = ""
    tasklist_name: str | None = None


class EventCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    event_date: date
    start_time: time
    duration_minutes: int = Field(default=60, ge=15, le=1440)
    group_name: str = ""


class TaskUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_date: date | None = None
    notes: str = ""


class EventGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(min_length=1, max_length=30)


class AssistantRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AssistantConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=100)


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GoogleAuthRequiredError):
        return HTTPException(status_code=401, detail="Подключите Google-аккаунт.")
    return HTTPException(status_code=502, detail=str(exc))


def _event_colors(service: GoogleCalendarService) -> dict[str, str]:
    loader = getattr(service, "get_event_colors", None)
    return loader() if loader else {}


def _calendar_color(service: GoogleCalendarService) -> str | None:
    loader = getattr(service, "get_calendar_color", None)
    return loader() if loader else None


def _tasks_for_date(items: list, selected: date) -> list:
    """Keep exact-date tasks plus their ancestors and descendants."""
    target = selected.isoformat()
    by_id = {item.task_id: item for item in items}
    children: dict[str, list] = {}
    for item in items:
        if item.parent:
            children.setdefault(item.parent, []).append(item)

    included = {
        item.task_id
        for item in items
        if (item.due and item.due[:10] == target)
        or (not item.due and not item.parent)
    }
    pending = list(included)
    while pending:
        task_id = pending.pop()
        item = by_id.get(task_id)
        if item and item.parent and item.parent not in included:
            included.add(item.parent)
            pending.append(item.parent)
        for child in children.get(task_id, []):
            child_matches = not child.due or child.due[:10] == target
            if child_matches and child.task_id not in included:
                included.add(child.task_id)
                pending.append(child.task_id)
    return [item for item in items if item.task_id in included]


def create_spa_app(*, settings: Settings | None = None) -> FastAPI:
    web_settings = settings or load_settings()
    app = FastAPI(title="DayFlow", version="0.1.0")
    app.state.settings = web_settings
    app.state.google_auth_sessions: dict[str, GoogleAuthSession] = {}
    app.state.assistant_drafts: dict[str, object] = {}

    def event_groups() -> dict[str, str]:
        return build_event_group_store(web_settings).list_groups()

    def group_for_color(color_id: str | None) -> str:
        if not color_id:
            return ""
        return next((name for name, value in event_groups().items() if value == color_id), "")

    @app.middleware("http")
    async def disable_frontend_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "app": "dayflow-web"}

    @app.get("/api/google/connect")
    async def connect_google(request: Request) -> dict[str, str]:
        session = build_google_auth_session(
            web_settings,
            None,
            redirect_uri=str(request.url_for("google_oauth_callback")),
        )
        app.state.google_auth_sessions[session.state] = session
        return {"auth_url": session.auth_url}

    @app.post("/api/assistant/plan")
    async def plan_assistant_action(payload: AssistantRequest) -> dict:
        import bot as bot_module

        try:
            result = await asyncio.to_thread(
                bot_module.handle_natural_language,
                0,
                payload.text.strip(),
                None,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        draft = bot_module.pending_drafts.pop(0, None)
        token = ""
        if draft is not None:
            token = uuid4().hex
            app.state.assistant_drafts[token] = draft
        return {
            "text": result.get("text", "Не удалось разобрать запрос."),
            "requires_confirmation": bool(token),
            "token": token,
            "needs_clarification": bool(result.get("needs_clarification")),
        }

    @app.post("/api/assistant/cancel")
    async def cancel_assistant_action(payload: AssistantConfirmRequest) -> dict[str, bool]:
        app.state.assistant_drafts.pop(payload.token, None)
        return {"cancelled": True}

    @app.post("/api/assistant/confirm")
    async def confirm_assistant_action(payload: AssistantConfirmRequest) -> dict[str, str]:
        draft = app.state.assistant_drafts.pop(payload.token, None)
        if draft is None:
            raise HTTPException(status_code=404, detail="Черновик устарел. Повторите запрос.")
        calendar = GoogleCalendarService(web_settings)
        tasks = GoogleTasksService(web_settings)
        kind = getattr(draft, "kind", "")
        try:
            if kind == "event_create":
                color_id = build_event_group_store(web_settings).resolve_color_id(draft.group_name) if draft.group_name else None
                created = await asyncio.to_thread(calendar.create_event, draft.title, draft.start_at, draft.duration_minutes, color_id)
                return {"status": "completed", "text": f'Событие «{created.title}» создано.'}
            if kind == "event_update":
                color_id = build_event_group_store(web_settings).resolve_color_id(draft.group_name) if draft.group_name else None
                updated = await asyncio.to_thread(calendar.update_event, draft.event_id, new_title=draft.new_title, new_start_at=draft.start_at, duration_minutes=draft.duration_minutes, color_id=color_id)
                return {"status": "completed", "text": f'Событие «{updated.title}» обновлено.'}
            if kind == "event_delete":
                for event_id in draft.event_ids:
                    await asyncio.to_thread(calendar.delete_event, event_id)
                return {"status": "completed", "text": f'Событие «{draft.title}» удалено.'}
            if kind == "task_create":
                await asyncio.to_thread(tasks.create_task_with_subtasks, draft.title, draft.due_date, draft.notes, draft.task_list_name, list(draft.subtasks))
                return {"status": "completed", "text": f'Задача «{draft.title}» создана.'}
            if kind == "task_update":
                updated = await asyncio.to_thread(tasks.update_task, draft.task_id, draft.task_list_name, new_title=draft.new_title, due_date=draft.due_date, notes=draft.notes)
                if draft.subtasks:
                    await asyncio.to_thread(tasks.replace_subtasks, draft.task_id, draft.task_list_name, list(draft.subtasks))
                return {"status": "completed", "text": f'Задача «{updated.title}» обновлена.'}
            if kind == "task_delete":
                await asyncio.to_thread(tasks.delete_task, draft.task_id, draft.task_list_name)
                return {"status": "completed", "text": f'Задача «{draft.title}» удалена.'}
            if kind == "task_complete":
                updated = await asyncio.to_thread(tasks.complete_task, draft.task_id, draft.task_list_name)
                return {"status": "completed", "text": f'Задача «{updated.title}» выполнена.'}
        except Exception as exc:
            raise _api_error(exc) from exc
        raise HTTPException(status_code=422, detail="Этот тип действия пока не поддерживается.")

    @app.get("/api/event-groups")
    async def list_event_groups() -> dict:
        return {
            "groups": [
                {"name": name, "color": color_id}
                for name, color_id in event_groups().items()
            ]
        }

    @app.post("/api/event-groups", status_code=201)
    async def create_event_group(payload: EventGroupCreateRequest) -> dict[str, str]:
        try:
            name, color_id = build_event_group_store(web_settings).add_group(
                payload.name.strip(),
                payload.color.strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"name": name, "color": color_id}

    @app.delete("/api/event-groups/{group_name}")
    async def delete_event_group(group_name: str) -> dict[str, str]:
        deleted = build_event_group_store(web_settings).delete_group(group_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Группа не найдена.")
        return {"name": group_name, "status": "deleted"}

    @app.put("/api/event-groups/{group_name}")
    async def update_event_group(group_name: str, payload: EventGroupCreateRequest) -> dict[str, str]:
        try:
            name, color_id = build_event_group_store(web_settings).update_group(
                group_name,
                payload.name.strip(),
                payload.color.strip(),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Группа не найдена.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"name": name, "color": color_id, "status": "updated"}

    @app.get("/api/tasklists")
    async def list_tasklists() -> dict:
        try:
            items = await asyncio.to_thread(GoogleTasksService(web_settings).list_tasklists)
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"tasklists": items}

    @app.get("/api/tasks")
    async def list_tasks(target: date | None = Query(default=None)) -> dict:
        selected = target or datetime.now(get_timezone(web_settings.timezone)).date()
        try:
            items = await asyncio.to_thread(
                GoogleTasksService(web_settings).list_tasks,
                show_completed=False,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        selected_items = _tasks_for_date(items, selected)
        return {
            "date": selected.isoformat(),
            "tasks": [
                {
                    "id": item.task_id,
                    "title": item.title,
                    "due": item.due,
                    "status": item.status,
                    "list": item.tasklist_title,
                    "notes": item.notes,
                    "parent": item.parent,
                    "position": getattr(item, "position", ""),
                }
                for item in selected_items
            ],
        }

    @app.get("/api/event-groups/suggest")
    async def suggest_event_group(title: str = Query(min_length=2, max_length=300)) -> dict[str, str]:
        groups = event_groups()
        if not groups:
            return {"group": ""}
        folded = title.casefold()
        exact = next((name for name in groups if name.casefold() in folded), "")
        if exact:
            return {"group": exact}
        assistant = AssistantService(web_settings)
        if not assistant.enabled:
            return {"group": ""}
        try:
            plan = await asyncio.to_thread(
                assistant.plan,
                (
                    f"Создай событие «{title}» сегодня в 12:00. "
                    "Твоя главная задача — классифицировать название: обязательно выбери "
                    "наиболее подходящую event_group из доступного списка. "
                    "Оставь event_group пустой, только если ни одна группа действительно не подходит."
                ),
                groups.keys(),
            )
        except Exception:
            return {"group": ""}
        return {"group": plan.event_group if plan.event_group in groups else ""}

    @app.get("/google/oauth/callback", response_class=HTMLResponse)
    async def google_oauth_callback(request: Request, state: str = "") -> str:
        session = app.state.google_auth_sessions.pop(state, None)
        if session is None:
            raise HTTPException(status_code=400, detail="OAuth-сессия устарела. Начните подключение заново.")
        try:
            complete_google_auth(web_settings, None, session, str(request.url))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Не удалось подключить Google: {exc}") from exc
        return """<!doctype html><html><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"1;url=/\"></head><body style=\"font-family:system-ui;padding:40px\"><h1>Google подключён</h1><p>Возвращаемся в DayFlow…</p></body></html>"""

    @app.get("/api/today")
    async def today(target: date | None = Query(default=None)) -> dict:
        selected = target or datetime.now(get_timezone(web_settings.timezone)).date()
        try:
            calendar = GoogleCalendarService(web_settings)
            events = calendar.list_events_for_day(selected)
            colors = _event_colors(calendar)
            default_color = _calendar_color(calendar)
            tasks = GoogleTasksService(web_settings).list_tasks(show_completed=False)
        except Exception as exc:
            raise _api_error(exc) from exc
        return {
            "date": selected.isoformat(),
            "colors": colors,
            "default_color": default_color,
            "events": [
                {
                    "id": item.event_id,
                    "title": item.title,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "link": item.html_link,
                    "color": getattr(item, "color_id", None),
                    "group": group_for_color(getattr(item, "color_id", None)),
                }
                for item in events
            ],
            "tasks": [
                {
                    "id": item.task_id,
                    "title": item.title,
                    "due": item.due,
                    "status": item.status,
                    "list": item.tasklist_title,
                    "notes": item.notes,
                    "parent": item.parent,
                }
                for item in tasks
                if not item.due or item.due[:10] <= selected.isoformat()
            ],
        }

    @app.get("/api/week")
    async def week(start: date | None = Query(default=None)) -> dict:
        selected = start or datetime.now(get_timezone(web_settings.timezone)).date()
        week_start = selected - timedelta(days=selected.weekday())
        week_end = week_start + timedelta(days=6)
        try:
            dates = [week_start + timedelta(days=offset) for offset in range(7)]
            event_days = await asyncio.gather(*[
                asyncio.to_thread(
                    GoogleCalendarService(web_settings).list_events_for_day,
                    current,
                )
                for current in dates
            ])
            days = []
            for current, events in zip(dates, event_days):
                days.append({
                    "date": current.isoformat(),
                    "events": [
                        {
                            "id": item.event_id,
                            "title": item.title,
                            "start": item.start.isoformat(),
                            "end": item.end.isoformat(),
                            "link": item.html_link,
                            "color": getattr(item, "color_id", None),
                            "group": group_for_color(getattr(item, "color_id", None)),
                        }
                        for item in events
                    ],
                })
            tasks = await asyncio.to_thread(
                GoogleTasksService(web_settings).list_tasks,
                show_completed=False,
            )
            colors = await asyncio.to_thread(
                _event_colors,
                GoogleCalendarService(web_settings),
            )
            default_color = await asyncio.to_thread(
                _calendar_color,
                GoogleCalendarService(web_settings),
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return {
            "start": week_start.isoformat(),
            "end": week_end.isoformat(),
            "colors": colors,
            "default_color": default_color,
            "days": days,
            "tasks": [
                {
                    "id": item.task_id,
                    "title": item.title,
                    "due": item.due,
                    "status": item.status,
                    "list": item.tasklist_title,
                    "notes": item.notes,
                    "parent": item.parent,
                }
                for item in tasks
                if not item.due or item.due[:10] <= week_end.isoformat()
            ],
        }

    @app.get("/api/slots")
    async def slots(
        target: date | None = Query(default=None),
        end_date: date | None = Query(default=None),
        start_time: time | None = Query(default=None),
        end_time: time | None = Query(default=None),
        duration: int = Query(default=60, ge=15, le=480),
    ) -> dict:
        selected = target or datetime.now(get_timezone(web_settings.timezone)).date()
        selected_end = end_date or selected
        if selected_end < selected:
            raise HTTPException(status_code=422, detail="Конечная дата не может быть раньше начальной.")
        if (selected_end - selected).days > 31:
            raise HTTPException(status_code=422, detail="Диапазон поиска — не больше 31 дня.")
        start_minutes = start_time.hour * 60 + start_time.minute if start_time else None
        end_minutes = end_time.hour * 60 + end_time.minute if end_time else None
        if start_minutes is not None and end_minutes is not None and end_minutes <= start_minutes:
            raise HTTPException(status_code=422, detail="Конец временного окна должен быть позже начала.")
        try:
            items = GoogleCalendarService(web_settings).find_free_slots_in_range(
                selected,
                selected_end,
                duration_minutes=duration,
                preferred_start_minutes=start_minutes,
                preferred_end_minutes=end_minutes,
                limit=20,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        now = datetime.now(get_timezone(web_settings.timezone))
        rounded_now = now.replace(second=0, microsecond=0)
        remainder = rounded_now.minute % 15
        if remainder or now.second or now.microsecond:
            rounded_now += timedelta(minutes=15 - remainder if remainder else 15)
        future_items = []
        for slot_start, slot_end in items:
            if slot_end <= now:
                continue
            adjusted_start = max(slot_start, rounded_now) if slot_start.date() == now.date() else slot_start
            if slot_end - adjusted_start >= timedelta(minutes=duration):
                future_items.append((adjusted_start, slot_end))
        return {
            "date": selected.isoformat(),
            "end_date": selected_end.isoformat(),
            "slots": [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in future_items
            ],
        }

    @app.post("/api/tasks", status_code=201)
    async def create_task(payload: TaskCreateRequest) -> dict[str, str]:
        try:
            task_id = GoogleTasksService(web_settings).create_task(
                payload.title.strip(),
                due_date=payload.due_date,
                notes=payload.notes.strip(),
                tasklist_name=payload.tasklist_name,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": task_id, "status": "created"}

    @app.post("/api/events", status_code=201)
    async def create_event(payload: EventCreateRequest) -> dict[str, str]:
        start_at = datetime.combine(
            payload.event_date,
            payload.start_time,
            tzinfo=get_timezone(web_settings.timezone),
        )
        try:
            color_id = build_event_group_store(web_settings).resolve_color_id(payload.group_name)
            event = GoogleCalendarService(web_settings).create_event(
                payload.title.strip(),
                start_at,
                duration_minutes=payload.duration_minutes,
                color_id=color_id,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": event.event_id, "status": "created"}

    @app.put("/api/events/{event_id}")
    async def update_event(event_id: str, payload: EventCreateRequest) -> dict[str, str]:
        start_at = datetime.combine(
            payload.event_date,
            payload.start_time,
            tzinfo=get_timezone(web_settings.timezone),
        )
        try:
            color_id = build_event_group_store(web_settings).resolve_color_id(payload.group_name) or ""
            event = GoogleCalendarService(web_settings).update_event(
                event_id,
                new_title=payload.title.strip(),
                new_start_at=start_at,
                duration_minutes=payload.duration_minutes,
                color_id=color_id,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": event.event_id, "status": "updated"}

    @app.delete("/api/events/{event_id}")
    async def delete_event(event_id: str) -> dict[str, str]:
        try:
            GoogleCalendarService(web_settings).delete_event(event_id)
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": event_id, "status": "deleted"}

    @app.post("/api/tasks/{tasklist_name}/{task_id}/complete")
    async def complete_task(tasklist_name: str, task_id: str) -> dict[str, str]:
        try:
            GoogleTasksService(web_settings).complete_task(task_id, tasklist_name)
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": task_id, "status": "completed"}

    @app.put("/api/tasks/{tasklist_name}/{task_id}")
    async def update_task(tasklist_name: str, task_id: str, payload: TaskUpdateRequest) -> dict[str, str]:
        try:
            task = GoogleTasksService(web_settings).update_task(
                task_id,
                tasklist_name,
                new_title=payload.title.strip(),
                due_date=payload.due_date,
                notes=payload.notes.strip(),
                clear_due=payload.due_date is None,
            )
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": task.task_id, "status": "updated"}

    @app.delete("/api/tasks/{tasklist_name}/{task_id}")
    async def delete_task(tasklist_name: str, task_id: str) -> dict[str, str]:
        try:
            GoogleTasksService(web_settings).delete_task(task_id, tasklist_name)
        except Exception as exc:
            raise _api_error(exc) from exc
        return {"id": task_id, "status": "deleted"}

    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_spa_app()
