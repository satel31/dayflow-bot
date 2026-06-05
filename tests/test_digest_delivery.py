from dataclasses import dataclass

from dayflow.config import Settings
from dayflow.digest_delivery import send_daily_digest_to_subscribers
from dayflow.digest_subscriber_store import DigestSubscriber


@dataclass(frozen=True)
class FakeDigest:
    text: str


class FakeStore:
    def __init__(self, subscribers):
        self.subscribers = list(subscribers)

    def list_subscribers(self):
        return self.subscribers


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="token",
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


def test_send_daily_digest_to_subscribers_sends_to_each_chat():
    sent = []

    def fake_build_digest(settings, user_id, kind):
        return FakeDigest(text=f"{kind}:{user_id}")

    def fake_send_message(settings, chat_id, text):
        sent.append((chat_id, text))

    result = send_daily_digest_to_subscribers(
        make_settings(),
        "morning",
        store=FakeStore(
            [
                DigestSubscriber(user_id=10, chat_id=100),
                DigestSubscriber(user_id=20, chat_id=200),
            ]
        ),
        build_digest=fake_build_digest,
        send_message=fake_send_message,
    )

    assert result.attempted == 2
    assert result.sent == 2
    assert result.failed == 0
    assert sent == [(100, "morning:10"), (200, "morning:20")]


def test_send_daily_digest_to_subscribers_keeps_going_after_error():
    sent = []

    def fake_build_digest(settings, user_id, kind):
        if user_id == 10:
            raise RuntimeError("broken token")
        return FakeDigest(text=f"{kind}:{user_id}")

    def fake_send_message(settings, chat_id, text):
        sent.append((chat_id, text))

    result = send_daily_digest_to_subscribers(
        make_settings(),
        "evening",
        store=FakeStore(
            [
                DigestSubscriber(user_id=10, chat_id=100),
                DigestSubscriber(user_id=20, chat_id=200),
            ]
        ),
        build_digest=fake_build_digest,
        send_message=fake_send_message,
    )

    assert result.attempted == 2
    assert result.sent == 1
    assert result.failed == 1
    assert "user_id=10 chat_id=100: RuntimeError: broken token" in result.errors
    assert sent == [(200, "evening:20")]
