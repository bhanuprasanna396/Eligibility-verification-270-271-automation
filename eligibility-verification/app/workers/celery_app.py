"""
Celery application instance.

All workers import `celery_app` from here.
Do not put task definitions in this file — keep them in worker modules.

To start a worker locally (after docker-compose up):
    celery -A app.workers.celery_app worker --loglevel=info

To start the beat scheduler (for nightly watcher):
    celery -A app.workers.celery_app beat --loglevel=info
"""
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "eligibility",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.eligibility_worker",
        "app.workers.appointment_watcher",
    ],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Time
    timezone="UTC",
    enable_utc=True,

    # Reliability
    # Only acknowledge the task AFTER it finishes — prevents silent loss
    # if the worker crashes mid-task
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # One task at a time per worker process — prevents DB connection pool exhaustion
    worker_prefetch_multiplier=1,

    # How long to keep task results in Redis (1 day is enough for monitoring)
    result_expires=86400,
)

# Nightly schedule — runs the appointment watcher at 11 PM UTC every day
celery_app.conf.beat_schedule = {
    "nightly-eligibility-check": {
        "task": "app.workers.appointment_watcher.queue_upcoming_appointments",
        "schedule": crontab(hour=23, minute=0),
    },
}
