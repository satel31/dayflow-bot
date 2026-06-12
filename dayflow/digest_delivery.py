from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from dayflow.config import Settings
from dayflow.digest_service import DigestKind, build_daily_digest_for_user
from dayflow.digest_subscriber_store import DigestSubscriber, DigestSubscriberStore, build_digest_subscriber_store
from dayflow.telegram_sender import send_telegram_message


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DigestDeliveryResult:
    kind: DigestKind
    attempted: int
    sent: int
    failed: int
    errors: tuple[str, ...] = ()


DigestBuilder = Callable[[Settings, int, DigestKind], object]
MessageSender = Callable[[Settings, int, str], None]


def send_daily_digest_to_subscribers(
    settings: Settings,
    kind: DigestKind,
    *,
    store: DigestSubscriberStore | None = None,
    build_digest: DigestBuilder = build_daily_digest_for_user,
    send_message: MessageSender = send_telegram_message,
) -> DigestDeliveryResult:
    subscriber_store = store or build_digest_subscriber_store(settings)
    subscribers = subscriber_store.list_subscribers()
    sent = 0
    errors: list[str] = []

    for subscriber in subscribers:
        try:
            digest = build_digest(settings, subscriber.user_id, kind)
            send_message(settings, subscriber.chat_id, digest.text)
        except Exception as exc:
            message = format_delivery_error(subscriber, exc)
            logger.exception(message)
            errors.append(message)
            continue
        sent += 1

    return DigestDeliveryResult(
        kind=kind,
        attempted=len(subscribers),
        sent=sent,
        failed=len(errors),
        errors=tuple(errors),
    )


def format_delivery_error(subscriber: DigestSubscriber, exc: Exception) -> str:
    return f"user_id={subscriber.user_id} chat_id={subscriber.chat_id}: {type(exc).__name__}: {exc}"
