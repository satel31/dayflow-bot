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
    assert celery_app.conf.beat_schedule["send-morning-digest"]["task"] == "dayflow.send_daily_digest"
    assert celery_app.conf.beat_schedule["send-morning-digest"]["args"] == ("morning",)
    assert celery_app.conf.beat_schedule["send-morning-digest"]["schedule"].hour == {10}
    assert celery_app.conf.beat_schedule["send-morning-digest"]["schedule"].minute == {0}
    assert celery_app.conf.beat_schedule["send-evening-digest"]["task"] == "dayflow.send_daily_digest"
    assert celery_app.conf.beat_schedule["send-evening-digest"]["args"] == ("evening",)
    assert celery_app.conf.beat_schedule["send-evening-digest"]["schedule"].hour == {22}
    assert celery_app.conf.beat_schedule["send-evening-digest"]["schedule"].minute == {0}


def test_celery_health_lists_local_tasks():
    assert "dayflow.smoke" in local_task_names()
    assert "dayflow.send_daily_digest" in local_task_names()
