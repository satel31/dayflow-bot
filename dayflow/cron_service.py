from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging

from dayflow.config import Settings
from dayflow.digest_service import DigestKind, build_daily_digest_for_user
from dayflow.supabase_client import build_supabase_client
from dayflow.telegram_sender import send_telegram_message
from dayflow.timezone_utils import get_timezone
from dayflow.user_profile_store import UserProfile, build_user_profile_store


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CronDigestResult:
    due: int
    sent: int
    skipped: int
    failed: int


class MemoryDeliveryClaimStore:
    def __init__(self) -> None:
        self.claims: set[str] = set()

    def claim(self, key: str, user_id: int, kind: DigestKind, local_date: str) -> bool:
        if key in self.claims:
            return False
        self.claims.add(key)
        return True

    def release(self, key: str) -> None:
        self.claims.discard(key)


class SupabaseDeliveryClaimStore:
    def __init__(self, settings: Settings) -> None:
        self.client = build_supabase_client(settings)

    def claim(self, key: str, user_id: int, kind: DigestKind, local_date: str) -> bool:
        return self.client.insert_ignore(
            "digest_deliveries",
            {"delivery_key": key, "user_id": user_id, "kind": kind, "local_date": local_date},
            on_conflict="delivery_key",
        )

    def release(self, key: str) -> None:
        self.client.delete("digest_deliveries", params={"delivery_key": f"eq.{key}"})


def send_due_digests(
    settings: Settings,
    kind: DigestKind,
    *,
    now: datetime | None = None,
    profile_store=None,
    claim_store=None,
    build_digest=build_daily_digest_for_user,
    send_message=send_telegram_message,
) -> CronDigestResult:
    now_utc = now or datetime.now(timezone.utc)
    profiles = (profile_store or build_user_profile_store(settings)).list_profiles()
    claims = claim_store or (
        SupabaseDeliveryClaimStore(settings)
        if settings.persistent_backend == "supabase"
        else MemoryDeliveryClaimStore()
    )
    due = sent = skipped = failed = 0
    for profile in profiles:
        local_now = now_utc.astimezone(get_timezone(profile.timezone))
        target_hour = profile.digest_morning_hour if kind == "morning" else profile.digest_evening_hour
        if local_now.hour != target_hour:
            continue
        due += 1
        local_date = local_now.date().isoformat()
        key = f"{profile.user_id}:{kind}:{local_date}"
        if not claims.claim(key, profile.user_id, kind, local_date):
            skipped += 1
            continue
        try:
            user_settings = replace(settings, timezone=profile.timezone)
            digest = build_digest(user_settings, profile.user_id, kind)
            send_message(settings, profile.chat_id, digest.text)
            sent += 1
        except Exception:
            logger.exception("Failed to send %s digest to user_id=%s", kind, profile.user_id)
            claims.release(key)
            failed += 1
    return CronDigestResult(due=due, sent=sent, skipped=skipped, failed=failed)
