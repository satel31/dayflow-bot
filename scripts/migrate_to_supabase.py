from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dayflow.config import load_settings
from dayflow.crypto import encrypt_text
from dayflow.digest_subscriber_store import DigestSubscriberStore
from dayflow.group_store import EventGroupStore
from dayflow.supabase_client import build_supabase_client
from dayflow.work_schedule_store import WorkScheduleStore


def main() -> None:
    settings = load_settings()
    client = build_supabase_client(settings)

    token_count = 0
    token_dir = Path(settings.google_tokens_dir)
    for token_path in sorted(token_dir.glob("*.json")):
        user_id = int(token_path.stem)
        client.upsert(
            "google_tokens",
            {
                "user_id": user_id,
                "token_json": encrypt_text(
                    token_path.read_text(encoding="utf-8"),
                    settings.data_encryption_key,
                ),
            },
            on_conflict="user_id",
        )
        token_count += 1

    subscribers = DigestSubscriberStore(settings.digest_subscribers_path).list_subscribers()
    for subscriber in subscribers:
        client.upsert(
            "digest_subscribers",
            {"user_id": subscriber.user_id, "chat_id": subscriber.chat_id},
            on_conflict="user_id",
        )
        client.upsert(
            "user_profiles",
            {
                "user_id": subscriber.user_id,
                "chat_id": subscriber.chat_id,
                "timezone": settings.timezone,
                "digest_morning_hour": settings.digest_morning_hour,
                "digest_evening_hour": settings.digest_evening_hour,
            },
            on_conflict="user_id",
        )

    groups = EventGroupStore(settings.event_groups_path).list_groups()
    client.set_app_state("event_groups", groups)

    schedule = WorkScheduleStore(
        settings.work_schedule_path,
        settings.workday_start_hour,
        settings.workday_end_hour,
    ).load()
    client.set_app_state(
        "work_schedule",
        {
            "weekdays": list(schedule.weekdays),
            "start_minutes": schedule.start_minutes,
            "end_minutes": schedule.end_minutes,
        },
    )

    print(
        f"Migrated {token_count} Google tokens, {len(subscribers)} digest subscribers, "
        f"{len(groups)} event groups, {len(subscribers)} user profiles, and the work schedule."
    )


if __name__ == "__main__":
    main()
