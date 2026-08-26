"""S3 storage settings. No AWS calls — this only builds the STORAGES dict."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.storage import s3_storages
from listening.serializers import ExerciseMediaUrlMixin


def test_s3_storages_points_uploads_at_the_bucket():
    storages = s3_storages(bucket_name="practice-media")
    default = storages["default"]
    assert default["BACKEND"] == "storages.backends.s3.S3Storage"
    assert default["OPTIONS"]["bucket_name"] == "practice-media"
    assert default["OPTIONS"]["region_name"] == "us-east-1"
    assert default["OPTIONS"]["file_overwrite"] is False
    assert default["OPTIONS"]["default_acl"] is None
    assert default["OPTIONS"]["querystring_auth"] is False
    assert "custom_domain" not in default["OPTIONS"]
    assert storages["staticfiles"]["BACKEND"].endswith("StaticFilesStorage")


def test_s3_storages_includes_a_cdn_domain_when_given():
    storages = s3_storages(
        bucket_name="practice-media",
        custom_domain="media.example.com",
        region_name="eu-west-1",
        querystring_auth=True,
    )
    options = storages["default"]["OPTIONS"]
    assert options["custom_domain"] == "media.example.com"
    assert options["region_name"] == "eu-west-1"
    assert options["querystring_auth"] is True


def test_s3_storages_rejects_an_empty_bucket():
    with pytest.raises(ImproperlyConfigured, match="AWS_STORAGE_BUCKET_NAME"):
        s3_storages(bucket_name="")


def test_media_urls_do_not_prefix_an_s3_url_with_the_api_origin():
    mixin = ExerciseMediaUrlMixin()
    s3 = "https://cdn.example.com/audio/1/abc.mp3"
    assert mixin._absolute_media_url(SimpleNamespace(url=s3)) == s3
    assert mixin._absolute_media_url(None) is None
