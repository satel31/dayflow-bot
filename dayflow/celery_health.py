from __future__ import annotations

import sys

from redis import Redis
from redis.exceptions import RedisError

from dayflow.celery_app import celery_app, settings


def local_task_names() -> list[str]:
    return sorted(name for name in celery_app.tasks if name.startswith("dayflow."))


def check_redis(url: str) -> tuple[bool, str]:
    try:
        client = Redis.from_url(url)
        client.ping()
    except RedisError as exc:
        return False, str(exc)
    return True, "OK"


def main() -> int:
    print(f"Celery app: {celery_app.main}")
    print(f"Broker: {settings.celery_broker_url}")
    print(f"Result backend: {settings.celery_result_backend}")
    print(f"Beat schedule file: {settings.celery_beat_schedule_path}")
    print("Local tasks:")
    for task_name in local_task_names():
        print(f"- {task_name}")

    broker_ok, broker_message = check_redis(settings.celery_broker_url)
    print(f"Redis broker: {broker_message}")
    return 0 if broker_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
