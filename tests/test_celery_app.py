from dayflow.celery_app import celery_app
from dayflow.celery_health import local_task_names
from dayflow.config import load_settings


def test_celery_app_registers_smoke_task():
    settings = load_settings()
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert celery_app.conf.timezone == settings.timezone
    assert celery_app.conf.beat_schedule_filename == settings.celery_beat_schedule_path
    assert "dayflow.smoke" in celery_app.tasks
    assert "dayflow.build_daily_digest" in celery_app.tasks
    assert "dayflow.send_daily_digest" in celery_app.tasks


def test_celery_health_lists_local_tasks():
    assert "dayflow.smoke" in local_task_names()
    assert "dayflow.send_daily_digest" in local_task_names()
