from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dayflow.config import load_settings
from dayflow.crypto import decrypt_text, encrypt_text
from dayflow.digest_subscriber_store import DigestSubscriberStore
from dayflow.group_store import EventGroupStore
from dayflow.user_profile_store import UserProfileStore
from dayflow.work_schedule_store import WorkScheduleStore
from dayflow.ydb_state_store import build_ydb_state_store


def main() -> None:
    settings = load_settings()
    state = build_ydb_state_store(settings)

    token_count = 0
    for token_path in sorted(Path(settings.google_tokens_dir).glob("*.json")):
        token_json = decrypt_text(token_path.read_text(encoding="utf-8"), settings.data_encryption_key)
        state.set(
            "google_tokens",
            token_path.stem,
            encrypt_text(token_json, settings.data_encryption_key),
        )
        token_count += 1

    subscribers = DigestSubscriberStore(settings.digest_subscribers_path).list_subscribers()
    for subscriber in subscribers:
        state.set("digest_subscribers", str(subscriber.user_id), subscriber.chat_id)

    profiles = UserProfileStore(settings.user_profiles_path).list_profiles()
    for profile in profiles:
        state.set("user_profiles", str(profile.user_id), profile.__dict__)

    groups = EventGroupStore(settings.event_groups_path).list_groups()
    state.set("app_state", "event_groups", groups)

    schedule = WorkScheduleStore(
        settings.work_schedule_path,
        settings.workday_start_hour,
        settings.workday_end_hour,
    ).load()
    state.set(
        "app_state",
        "work_schedule",
        {
            "weekdays": list(schedule.weekdays),
            "start_minutes": schedule.start_minutes,
            "end_minutes": schedule.end_minutes,
        },
    )

    delivery_path = Path(settings.digest_deliveries_path)
    if delivery_path.exists():
        for key in json.loads(delivery_path.read_text(encoding="utf-8")):
            state.set("digest_deliveries", key, True)

    print(
        f"Migrated {token_count} tokens, {len(subscribers)} subscribers, "
        f"{len(profiles)} profiles, {len(groups)} groups, and the work schedule."
    )


if __name__ == "__main__":
    main()
