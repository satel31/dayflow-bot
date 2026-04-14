from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


WEEKDAY_LABELS = {
    0: "пн",
    1: "вт",
    2: "ср",
    3: "чт",
    4: "пт",
    5: "сб",
    6: "вс",
}


@dataclass(frozen=True)
class WorkSchedule:
    weekdays: tuple[int, ...]
    start_minutes: int
    end_minutes: int


class WorkScheduleStore:
    def __init__(self, path: str, default_start_hour: int, default_end_hour: int) -> None:
        self.path = Path(path)
        self.default = WorkSchedule(
            weekdays=(0, 1, 2, 3, 4),
            start_minutes=default_start_hour * 60,
            end_minutes=default_end_hour * 60,
        )

    def load(self) -> WorkSchedule:
        if not self.path.exists():
            return self.default
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        weekdays = tuple(sorted(int(day) for day in payload.get("weekdays", self.default.weekdays)))
        start_minutes = int(payload.get("start_minutes", self.default.start_minutes))
        end_minutes = int(payload.get("end_minutes", self.default.end_minutes))
        return self._validate(WorkSchedule(weekdays=weekdays, start_minutes=start_minutes, end_minutes=end_minutes))

    def save(self, schedule: WorkSchedule) -> WorkSchedule:
        schedule = self._validate(schedule)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "weekdays": list(schedule.weekdays),
                    "start_minutes": schedule.start_minutes,
                    "end_minutes": schedule.end_minutes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return schedule

    def format(self, schedule: WorkSchedule) -> str:
        weekday_text = ", ".join(WEEKDAY_LABELS[day] for day in schedule.weekdays)
        return (
            "Рабочее время:\n"
            f"Дни: {weekday_text}\n"
            f"Часы: {format_minutes(schedule.start_minutes)}-{format_minutes(schedule.end_minutes)}"
        )

    def _validate(self, schedule: WorkSchedule) -> WorkSchedule:
        weekdays = tuple(sorted({day for day in schedule.weekdays if 0 <= day <= 6}))
        if not weekdays:
            raise ValueError("Нужно выбрать хотя бы один рабочий день.")
        if not (0 <= schedule.start_minutes < 24 * 60):
            raise ValueError("Некорректное время начала.")
        if not (0 < schedule.end_minutes <= 24 * 60):
            raise ValueError("Некорректное время окончания.")
        if schedule.start_minutes >= schedule.end_minutes:
            raise ValueError("Время окончания должно быть позже времени начала.")
        return WorkSchedule(
            weekdays=weekdays,
            start_minutes=schedule.start_minutes,
            end_minutes=schedule.end_minutes,
        )


def format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def parse_time_to_minutes(raw_value: str) -> int:
    raw_value = raw_value.strip()
    hours_text, minutes_text = raw_value.split(":", maxsplit=1)
    hours = int(hours_text)
    minutes = int(minutes_text)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError("Время должно быть в формате HH:MM.")
    return hours * 60 + minutes


def parse_weekdays(raw_value: str) -> tuple[int, ...]:
    normalized = raw_value.strip().casefold()
    presets = {
        "пн-пт": (0, 1, 2, 3, 4),
        "будни": (0, 1, 2, 3, 4),
        "пн-сб": (0, 1, 2, 3, 4, 5),
        "ежедневно": (0, 1, 2, 3, 4, 5, 6),
        "каждый день": (0, 1, 2, 3, 4, 5, 6),
    }
    if normalized in presets:
        return presets[normalized]

    mapping = {
        "пн": 0,
        "вт": 1,
        "ср": 2,
        "чт": 3,
        "пт": 4,
        "сб": 5,
        "вс": 6,
    }
    items = [item.strip() for item in normalized.split(",") if item.strip()]
    weekdays = tuple(sorted(mapping[item] for item in items if item in mapping))
    if not weekdays:
        raise ValueError("Дни укажите как пн-пт, пн-сб, ежедневно или список вроде пн,вт,ср.")
    return weekdays
