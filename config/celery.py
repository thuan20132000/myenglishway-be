"""Celery application.

Kept deliberately thin: configuration comes from Django settings under the
CELERY_ namespace, and tasks live in their own app's ``tasks.py``.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("english_practice")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):  # pragma: no cover - operational helper
    print(f"Request: {self.request!r}")
