from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from dayflow.config import Settings
from dayflow.digest_service import DigestKind, build_daily_digest_for_user
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


class FileDeliveryClaimStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(set())

    def claim(self, key: str, user_id: int, kind: DigestKind, local_date: str) -> bool:
        claims = self._read()
        if key in claims:
            return False
        claims.add(key)
        self._write(claims)
        return True

    def release(self, key: str) -> None:
        claims = self._read()
        claims.discard(key)
        self._write(claims)

    def _read(self) -> set[str]:
        return set(json.loads(self.path.read_text(encoding="utf-8")))

    def _write(self, claims: set[str]) -> None:
        self.path.write_text(json.dumps(sorted(claims), indent=2), encoding="utf-8")


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
    claims = claim_store or FileDeliveryClaimStore(settings.digest_deliveries_path)
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
