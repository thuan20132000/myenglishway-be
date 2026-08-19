"""Project package.

The Celery app is imported here so ``@shared_task`` decorators bind to it as
soon as Django starts, whether the process is a web server or a worker.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
