from dayflow.celery_app import celery_app
from dayflow.config import load_settings


def test_celery_app_registers_smoke_task():
    settings = load_settings()
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend
    assert celery_app.conf.timezone == settings.timezone
    assert "dayflow.smoke" in celery_app.tasks
