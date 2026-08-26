"""S3 media storage.

Audio and PDF are plain FileFields; the stored value is a path
(``audio/3/….mp3``). Swapping the Django storage backend is enough — no
migration. Local and test keep the filesystem; production uses this.
"""

from django.core.exceptions import ImproperlyConfigured


def s3_storages(
    *,
    bucket_name: str,
    region_name: str = "us-east-1",
    custom_domain: str = "",
    querystring_auth: bool = False,
) -> dict:
    """Return a Django ``STORAGES`` dict pointing the default backend at S3.

    Static files stay on the local collector; only user uploads go to the bucket.
    ``querystring_auth=False`` matches the public ``/media/`` URLs the player
    already uses. Set it true if the bucket is private and URLs should be signed.
    """
    if not bucket_name:
        raise ImproperlyConfigured(
            "AWS_STORAGE_BUCKET_NAME is required when S3 storage is enabled."
        )

    options = {
        "bucket_name": bucket_name,
        "region_name": region_name or "us-east-1",
        "file_overwrite": False,
        "default_acl": None,
        "querystring_auth": querystring_auth,
        "object_parameters": {"CacheControl": "public, max-age=86400"},
    }
    if custom_domain:
        options["custom_domain"] = custom_domain

    return {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": options,
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
