import pytest
from rest_framework.test import APIClient

from accounts.models import Role
from tests.factories import ExerciseFactory, SegmentFactory, UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def student(db):
    return UserFactory(role=Role.STUDENT)


@pytest.fixture
def creator(db):
    return UserFactory(role=Role.CREATOR)


@pytest.fixture
def admin_user(db):
    return UserFactory(role=Role.ADMIN)


@pytest.fixture
def auth_client(api_client):
    """Returns a factory that authenticates the client as a given user."""

    def _auth(user):
        api_client.force_authenticate(user=user)
        return api_client

    return _auth


@pytest.fixture
def exercise_factory():
    return ExerciseFactory


@pytest.fixture
def segment_factory():
    return SegmentFactory
