from __future__ import annotations

import sys
from pathlib import Path

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


def check_filesystem_broker() -> tuple[bool, str]:
    queue_dir = Path(settings.celery_broker_data_dir) / "queue"
    processed_dir = Path(settings.celery_broker_data_dir) / "processed"
    missing = [str(path) for path in (queue_dir, processed_dir) if not path.exists()]
    if missing:
        return False, f"missing directories: {', '.join(missing)}"
    return True, "OK"


def check_broker() -> tuple[str, bool, str]:
    if settings.celery_broker_url.startswith("redis://"):
        ok, message = check_redis(settings.celery_broker_url)
        return "Redis broker", ok, message
    if settings.celery_broker_url == "filesystem://":
        ok, message = check_filesystem_broker()
        return "Filesystem broker", ok, message
    return "Broker", True, "check skipped"


def main() -> int:
    print(f"Celery app: {celery_app.main}")
    print(f"Broker: {settings.celery_broker_url}")
    print(f"Result backend: {settings.celery_result_backend}")
    if settings.celery_broker_url == "filesystem://":
        print(f"Broker data dir: {settings.celery_broker_data_dir}")
    print(f"Beat schedule file: {settings.celery_beat_schedule_path}")
    print("Local tasks:")
    for task_name in local_task_names():
        print(f"- {task_name}")
    print("Beat schedule:")
    for schedule_name, entry in celery_app.conf.beat_schedule.items():
        print(f"- {schedule_name}: {entry['task']} {entry.get('args', ())}")

    broker_label, broker_ok, broker_message = check_broker()
    print(f"{broker_label}: {broker_message}")
    return 0 if broker_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
