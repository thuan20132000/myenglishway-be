"""Load a transcript into an exercise from a JSON file.

This command is the manual driver for the same ``ingest_transcript`` service a
future Celery task will call. It exists so a complete exercise can be built and
practised end to end today, with no transcription provider, and so the ingest
contract is exercised by something real rather than only by tests.

    manage.py load_transcript 12 listening/fixtures/sample_transcript.json

Expected file shape::

    {
      "duration": 184.2,
      "segments": [
        {"start_time": 0.5, "end_time": 5.8, "text": "Good morning."},
        {"start_time": 6.0, "end_time": 12.4, "text": "How can I help?"}
      ]
    }

``sequence`` may be given on every segment or on none (then they are numbered
by start time). A ``words`` key is accepted and ignored, so provider output
with word-level timings loads unchanged.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.errors import DomainError
from listening.models import ListeningExercise
from listening.services import ingest_transcript


class Command(BaseCommand):
    help = "Load a transcript JSON file into an exercise."

    def add_arguments(self, parser):
        parser.add_argument("exercise_id", type=int)
        parser.add_argument("path", type=str, help="Path to the transcript JSON file.")
        parser.add_argument(
            "--provider",
            default="manual",
            help="Label for where this transcript came from. Default: manual.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the payload and roll back without saving.",
        )

    def handle(self, *args, **options):
        exercise = self._get_exercise(options["exercise_id"])
        payload = self._read_payload(Path(options["path"]))

        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise CommandError("The file must contain a 'segments' list.")

        try:
            if options["dry_run"]:
                # Run the real ingest, then roll it back: validating through the
                # same code path is the only check worth trusting.
                with transaction.atomic():
                    ingest_transcript(
                        exercise,
                        segments=segments,
                        duration=payload.get("duration"),
                        provider=options["provider"],
                    )
                    transaction.set_rollback(True)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Dry run OK: {len(segments)} segment(s) would be loaded "
                        f"into exercise {exercise.id}. Nothing was saved."
                    )
                )
                return

            ingest_transcript(
                exercise,
                segments=segments,
                duration=payload.get("duration"),
                provider=options["provider"],
            )
        except DomainError as exc:
            raise CommandError(f"{exc.detail.code}: {exc.detail}") from exc

        exercise.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {exercise.segments.count()} segment(s) into "
                f"'{exercise.title}' (id {exercise.id}). "
                f"Status: {exercise.status}, duration: {exercise.duration}."
            )
        )
        if not exercise.is_published:
            self.stdout.write(
                "The exercise is not published yet - "
                f"POST /api/v1/listening/exercises/{exercise.id}/publish/ when ready."
            )

    @staticmethod
    def _get_exercise(exercise_id: int) -> ListeningExercise:
        try:
            return ListeningExercise.objects.get(pk=exercise_id)
        except ListeningExercise.DoesNotExist:
            raise CommandError(f"Exercise {exercise_id} does not exist.") from None

    @staticmethod
    def _read_payload(path: Path) -> dict:
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CommandError("The file must contain a JSON object.")
        return payload
