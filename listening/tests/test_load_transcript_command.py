import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from listening.models import ExerciseStatus
from tests.factories import ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_transcript.json"


def write_json(tmp_path, payload, name="transcript.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return str(path)


def run(*args, **kwargs):
    out = StringIO()
    call_command("load_transcript", *args, stdout=out, **kwargs)
    return out.getvalue()


def test_loads_the_bundled_sample_fixture():
    exercise = ExerciseFactory(uploaded=True)

    output = run(exercise.id, str(FIXTURE))

    exercise.refresh_from_db()
    assert exercise.segments.count() == 4
    assert exercise.duration == 62.0
    assert exercise.status == ExerciseStatus.READY
    assert "Loaded 4 segment(s)" in output
    assert "not published yet" in output


def test_sample_fixture_segments_are_ordered_and_counted():
    exercise = ExerciseFactory(uploaded=True)
    run(exercise.id, str(FIXTURE))

    segments = list(exercise.segments.all())
    assert [s.sequence for s in segments] == [1, 2, 3, 4]
    assert segments[0].text == "Good morning, how can I help you?"
    assert segments[0].word_count == 7


def test_dry_run_validates_without_saving(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    path = write_json(
        tmp_path,
        {"duration": 30.0, "segments": [{"start_time": 0.0, "end_time": 5.0, "text": "a b"}]},
    )

    output = run(exercise.id, path, dry_run=True)

    exercise.refresh_from_db()
    assert exercise.segments.count() == 0
    assert exercise.status == ExerciseStatus.UPLOADED
    assert exercise.duration is None
    assert "Dry run OK" in output


def test_dry_run_reports_an_invalid_payload(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    path = write_json(
        tmp_path,
        {"duration": 30.0, "segments": [{"start_time": 5.0, "end_time": 1.0, "text": "a b"}]},
    )

    with pytest.raises(CommandError):
        run(exercise.id, path, dry_run=True)


def test_missing_exercise_is_reported(tmp_path):
    path = write_json(tmp_path, {"segments": []})
    with pytest.raises(CommandError, match="does not exist"):
        run(999999, path)


def test_missing_file_is_reported():
    exercise = ExerciseFactory(uploaded=True)
    with pytest.raises(CommandError, match="File not found"):
        run(exercise.id, "/tmp/definitely-not-here.json")


def test_malformed_json_is_reported(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    path = tmp_path / "bad.json"
    path.write_text("{not json")

    with pytest.raises(CommandError, match="not valid JSON"):
        run(exercise.id, str(path))


def test_missing_segments_key_is_reported(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    path = write_json(tmp_path, {"duration": 30.0})

    with pytest.raises(CommandError, match="segments"):
        run(exercise.id, path)


def test_domain_errors_surface_with_their_code(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    path = write_json(
        tmp_path,
        {"duration": 10.0, "segments": [{"start_time": 0.0, "end_time": 50.0, "text": "a b"}]},
    )

    with pytest.raises(CommandError, match="SEGMENT_OUTSIDE_AUDIO"):
        run(exercise.id, path)


def test_loading_replaces_an_existing_transcript(tmp_path):
    exercise = ExerciseFactory(uploaded=True)
    SegmentFactory(exercise=exercise, sequence=1, text="stale")
    path = write_json(
        tmp_path,
        {"duration": 30.0, "segments": [{"start_time": 0.0, "end_time": 5.0, "text": "fresh text"}]},
    )

    run(exercise.id, path)

    assert exercise.segments.count() == 1
    assert exercise.segments.first().text == "fresh text"


def test_published_exercise_is_refused(tmp_path):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1)
    path = write_json(
        tmp_path,
        {"duration": 30.0, "segments": [{"start_time": 0.0, "end_time": 5.0, "text": "a b"}]},
    )

    with pytest.raises(CommandError):
        run(exercise.id, path)
