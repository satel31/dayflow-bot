from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from googleapiclient.discovery import build

from dayflow.auth import load_google_credentials
from dayflow.config import Settings
from dayflow.timezone_utils import get_timezone


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    html_link: str
    color_id: str | None = None


class GoogleCalendarService:
    def __init__(self, settings: Settings, user_id: int | None = None) -> None:
        self.settings = settings
        self.user_id = user_id
        self.tz = get_timezone(settings.timezone)
        self._service = None

    def list_events_for_day(self, target_date: date) -> list[CalendarEvent]:
        day_start = datetime.combine(target_date, time.min, tzinfo=self.tz)
        day_end = day_start + timedelta(days=1)
        events_result = (
            self._get_service()
            .events()
            .list(
                calendarId=self.settings.google_calendar_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        items = events_result.get("items", [])
        return [self._to_event(item) for item in items if "dateTime" in item.get("start", {})]

    def find_free_slots(
        self, target_date: date, duration_minutes: int = 60
    ) -> list[tuple[datetime, datetime]]:
        return self.find_free_slots_in_window(
            target_date,
            duration_minutes=duration_minutes,
            start_minutes=self.settings.workday_start_hour * 60,
            end_minutes=self.settings.workday_end_hour * 60,
        )

    def find_free_slots_in_window(
        self,
        target_date: date,
        *,
        duration_minutes: int = 60,
        start_minutes: int | None = None,
        end_minutes: int | None = None,
        start_hour: int = 0,
        end_hour: int = 24,
    ) -> list[tuple[datetime, datetime]]:
        events = self.list_events_for_day(target_date)
        resolved_start_minutes = start_minutes if start_minutes is not None else start_hour * 60
        resolved_end_minutes = end_minutes if end_minutes is not None else end_hour * 60
        start_hours, start_minutes_part = divmod(resolved_start_minutes, 60)
        window_start = datetime.combine(target_date, time(start_hours % 24, start_minutes_part), tzinfo=self.tz)
        if resolved_end_minutes >= 24 * 60:
            window_end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=self.tz)
        else:
            end_hours, end_minutes_part = divmod(resolved_end_minutes, 60)
            window_end = datetime.combine(target_date, time(end_hours % 24, end_minutes_part), tzinfo=self.tz)
        slot_start = window_start
        min_duration = timedelta(minutes=duration_minutes)
        free_slots: list[tuple[datetime, datetime]] = []
        relevant_events = sorted(
            (
                event
                for event in events
                if event.end > window_start and event.start < window_end
            ),
            key=lambda item: item.start,
        )

        for event in relevant_events:
            busy_start = max(event.start, window_start)
            busy_end = min(event.end, window_end)
            if busy_start > slot_start and busy_start - slot_start >= min_duration:
                free_slots.append((slot_start, busy_start))
            if busy_end > slot_start:
                slot_start = max(slot_start, busy_end)
            if slot_start >= window_end:
                break

        if window_end - slot_start >= min_duration:
            free_slots.append((slot_start, window_end))

        return free_slots

    def find_free_slots_in_range(
        self,
        start_date: date,
        end_date: date,
        *,
        duration_minutes: int = 60,
        outside_work_hours: bool = False,
        preferred_start_hour: int | None = None,
        preferred_end_hour: int | None = None,
        preferred_start_minutes: int | None = None,
        preferred_end_minutes: int | None = None,
        workdays: tuple[int, ...] | None = None,
        workday_start_minutes: int | None = None,
        workday_end_minutes: int | None = None,
        limit: int = 8,
    ) -> list[tuple[datetime, datetime]]:
        slots: list[tuple[datetime, datetime]] = []
        current_date = start_date
        allowed_workdays = set(workdays or ())
        resolved_workday_start = (
            workday_start_minutes
            if workday_start_minutes is not None
            else self.settings.workday_start_hour * 60
        )
        resolved_workday_end = (
            workday_end_minutes
            if workday_end_minutes is not None
            else self.settings.workday_end_hour * 60
        )
        while current_date <= end_date and len(slots) < limit:
            is_workday = not allowed_workdays or current_date.weekday() in allowed_workdays
            if (
                preferred_start_hour is not None
                or preferred_end_hour is not None
                or preferred_start_minutes is not None
                or preferred_end_minutes is not None
            ):
                if not is_workday:
                    current_date += timedelta(days=1)
                    continue
                slots.extend(
                    self.find_free_slots_in_window(
                        current_date,
                        duration_minutes=duration_minutes,
                        start_minutes=preferred_start_minutes,
                        end_minutes=preferred_end_minutes,
                        start_hour=preferred_start_hour if preferred_start_hour is not None else 0,
                        end_hour=preferred_end_hour if preferred_end_hour is not None else 24,
                    )
                )
            elif outside_work_hours:
                if not is_workday:
                    slots.extend(
                        self.find_free_slots_in_window(
                            current_date,
                            duration_minutes=duration_minutes,
                            start_hour=0,
                            end_hour=24,
                        )
                    )
                    current_date += timedelta(days=1)
                    continue
                slots.extend(
                    self.find_free_slots_in_window(
                        current_date,
                        duration_minutes=duration_minutes,
                        start_hour=0,
                        end_minutes=resolved_workday_start,
                    )
                )
                if len(slots) >= limit:
                    break
                slots.extend(
                    self.find_free_slots_in_window(
                        current_date,
                        duration_minutes=duration_minutes,
                        start_minutes=resolved_workday_end,
                        end_hour=24,
                    )
                )
            else:
                if not is_workday:
                    current_date += timedelta(days=1)
                    continue
                slots.extend(
                    self.find_free_slots_in_window(
                        current_date,
                        duration_minutes=duration_minutes,
                        start_minutes=resolved_workday_start,
                        end_minutes=resolved_workday_end,
                    )
                )
            current_date += timedelta(days=1)
        return slots[:limit]

    def find_conflicts(
        self, start_at: datetime, duration_minutes: int, exclude_event_id: str | None = None
    ) -> list[CalendarEvent]:
        end_at = start_at + timedelta(minutes=duration_minutes)
        same_day_events = self.list_events_for_day(start_at.date())
        conflicts = []
        for event in same_day_events:
            if exclude_event_id and event.event_id == exclude_event_id:
                continue
            if event.start < end_at and event.end > start_at:
                conflicts.append(event)
        return conflicts

    def search_events(
        self,
        title: str,
        target_date: date | None = None,
        days_ahead: int = 30,
    ) -> list[CalendarEvent]:
        if target_date:
            events = self.list_events_for_day(target_date)
        else:
            now = datetime.now(self.tz)
            end = now + timedelta(days=days_ahead)
            response = (
                self._get_service()
                .events()
                .list(
                    calendarId=self.settings.google_calendar_id,
                    timeMin=now.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items = response.get("items", [])
            events = [self._to_event(item) for item in items if "dateTime" in item.get("start", {})]
        title_cf = title.strip().casefold()
        return [event for event in events if title_cf in event.title.casefold()]

    def create_event(
        self,
        title: str,
        start_at: datetime,
        duration_minutes: int = 60,
        color_id: str | None = None,
    ) -> CalendarEvent:
        end_at = start_at + timedelta(minutes=duration_minutes)
        body = {
            "summary": title,
            "start": {"dateTime": start_at.isoformat(), "timeZone": self.settings.timezone},
            "end": {"dateTime": end_at.isoformat(), "timeZone": self.settings.timezone},
        }
        if color_id:
            body["colorId"] = color_id
        created = (
            self._get_service()
            .events()
            .insert(calendarId=self.settings.google_calendar_id, body=body)
            .execute()
        )
        return self._to_event(created)

    def update_event(
        self,
        event_id: str,
        *,
        new_title: str | None = None,
        new_start_at: datetime | None = None,
        duration_minutes: int | None = None,
        color_id: str | None = None,
    ) -> CalendarEvent:
        existing = (
            self._get_service()
            .events()
            .get(calendarId=self.settings.google_calendar_id, eventId=event_id)
            .execute()
        )
        start_at = (
            new_start_at
            if new_start_at
            else datetime.fromisoformat(existing["start"]["dateTime"])
        )
        current_end = datetime.fromisoformat(existing["end"]["dateTime"])
        resolved_duration = duration_minutes or int((current_end - datetime.fromisoformat(existing["start"]["dateTime"])).total_seconds() // 60)
        end_at = start_at + timedelta(minutes=resolved_duration)
        body = {
            "summary": new_title or existing.get("summary", "Без названия"),
            "start": {"dateTime": start_at.isoformat(), "timeZone": self.settings.timezone},
            "end": {"dateTime": end_at.isoformat(), "timeZone": self.settings.timezone},
        }
        if color_id:
            body["colorId"] = color_id
        elif existing.get("colorId"):
            body["colorId"] = existing["colorId"]
        updated = (
            self._get_service()
            .events()
            .update(calendarId=self.settings.google_calendar_id, eventId=event_id, body=body)
            .execute()
        )
        return self._to_event(updated)

    def delete_event(self, event_id: str) -> None:
        (
            self._get_service()
            .events()
            .delete(calendarId=self.settings.google_calendar_id, eventId=event_id)
            .execute()
        )

    def _get_service(self):
        if self._service is not None:
            return self._service

        creds = load_google_credentials(self.settings, self.user_id)
        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def _to_event(self, item: dict) -> CalendarEvent:
        return CalendarEvent(
            event_id=item["id"],
            title=item.get("summary", "Без названия"),
            start=datetime.fromisoformat(item["start"]["dateTime"]),
            end=datetime.fromisoformat(item["end"]["dateTime"]),
            html_link=item.get("htmlLink", ""),
            color_id=item.get("colorId"),
        )
