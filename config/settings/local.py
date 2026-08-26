from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Opt in with USE_S3=True and AWS_STORAGE_BUCKET_NAME. The Celery worker must
# use the same settings so transcription can open uploaded files.
if env.bool("USE_S3", default=False):  # noqa: F405
    from .storage import s3_storages

    STORAGES = s3_storages(
        bucket_name=env("AWS_STORAGE_BUCKET_NAME"),  # noqa: F405
        region_name=env("AWS_S3_REGION_NAME", default="us-east-1"),  # noqa: F405
        custom_domain=env("AWS_S3_CUSTOM_DOMAIN", default=""),  # noqa: F405
        querystring_auth=env.bool("AWS_S3_QUERYSTRING_AUTH", default=False),  # noqa: F405
    )
