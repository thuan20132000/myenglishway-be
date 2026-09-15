import pytest

from tests.factories import ReadingExerciseFactory, ReadingSessionFactory


@pytest.fixture
def passage_factory():
    return ReadingExerciseFactory


@pytest.fixture
def session_factory():
    return ReadingSessionFactory
