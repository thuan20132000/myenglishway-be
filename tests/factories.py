import factory
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from listening.models import (
    CollectionMembership,
    ExerciseAnswer,
    ExerciseCollection,
    ExerciseStatus,
    ListeningExercise,
    TranscriptSegment,
)
from practice.models import AttemptKind, PracticeAttempt
from writing.models import WritingExercise, WritingNotebook, WritingPage

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    role = Role.STUDENT

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        if not create:
            return
        obj.set_password(extracted or "test-passphrase-123")
        obj.save()


class CreatorFactory(UserFactory):
    role = Role.CREATOR


class AdminFactory(UserFactory):
    role = Role.ADMIN


class ExerciseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ListeningExercise

    owner = factory.SubFactory(CreatorFactory)
    title = factory.Sequence(lambda n: f"Exercise {n}")
    description = "IELTS Section 1 practice"
    language = "en"
    status = ExerciseStatus.DRAFT

    class Params:
        uploaded = factory.Trait(
            status=ExerciseStatus.UPLOADED,
            audio_file=factory.django.FileField(filename="audio.mp3", data=b"fake-audio"),
        )
        ready = factory.Trait(
            status=ExerciseStatus.READY,
            duration=184.2,
            audio_file=factory.django.FileField(filename="audio.mp3", data=b"fake-audio"),
        )
        published = factory.Trait(
            status=ExerciseStatus.READY,
            duration=184.2,
            is_published=True,
            published_at=factory.LazyFunction(timezone.now),
            audio_file=factory.django.FileField(filename="audio.mp3", data=b"fake-audio"),
        )
        with_pdf = factory.Trait(
            pdf_file=factory.django.FileField(
                filename="questions.pdf", data=b"%PDF-1.4 fake"
            ),
        )


class ExerciseAnswerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExerciseAnswer

    exercise = factory.SubFactory(ExerciseFactory)
    number = factory.Sequence(lambda n: n + 1)
    text = factory.Sequence(lambda n: f"answer {n}")


class SegmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TranscriptSegment

    exercise = factory.SubFactory(ExerciseFactory)
    sequence = factory.Sequence(lambda n: n + 1)
    start_time = factory.LazyAttribute(lambda o: float(o.sequence - 1) * 6.0)
    end_time = factory.LazyAttribute(lambda o: float(o.sequence - 1) * 6.0 + 5.0)
    text = "I would like accommodation near the university."


class AttemptFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PracticeAttempt

    user = factory.SubFactory(UserFactory)
    segment = factory.SubFactory(SegmentFactory)
    exercise = factory.LazyAttribute(lambda o: o.segment.exercise)
    kind = AttemptKind.SUBMISSION
    user_answer = "I would like accommodation near the university."
    normalized_answer = "i would like accommodation near the university"
    expected_snapshot = "I would like accommodation near the university."
    score = 100.0
    correct_count = 7
    scoring_version = "v1"

    class Params:
        reveal = factory.Trait(
            kind=AttemptKind.REVEAL,
            score=None,
            user_answer="",
            normalized_answer="",
            correct_count=0,
        )


class CollectionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExerciseCollection

    owner = factory.SubFactory(CreatorFactory)
    title = factory.Sequence(lambda n: f"Collection {n}")
    description = ""

    class Params:
        published = factory.Trait(
            is_published=True,
            published_at=factory.LazyFunction(timezone.now),
        )


class CollectionMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CollectionMembership

    collection = factory.SubFactory(CollectionFactory)
    # Owned by the collection's owner: the service layer refuses cross-owner
    # membership, so a factory that produced one would build invalid fixtures.
    exercise = factory.SubFactory(
        ExerciseFactory, owner=factory.SelfAttribute("..collection.owner")
    )
    position = factory.Sequence(lambda n: n + 1)


class WritingExerciseFactory(factory.django.DjangoModelFactory):
    """A workbook built straight through the ORM.

    ``page_count`` is set here rather than read from the file: the bytes below
    are not a real PDF, and going through ``services.create_exercise`` is what
    the API tests exercise. Tests that need the count to match the file build a
    real one with ``writing.tests.conftest.pdf_upload``.
    """

    class Meta:
        model = WritingExercise

    owner = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Workbook {n}")
    description = "Grammar in Use, unit 12"
    source = "Cambridge"
    language = "en"
    page_count = 10
    pdf_file = factory.django.FileField(filename="workbook.pdf", data=b"%PDF-1.4 fake")

    class Params:
        published = factory.Trait(
            is_published=True,
            published_at=factory.LazyFunction(timezone.now),
        )


class WritingNotebookFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WritingNotebook

    user = factory.SubFactory(UserFactory)
    exercise = factory.SubFactory(WritingExerciseFactory)


class WritingPageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WritingPage

    notebook = factory.SubFactory(WritingNotebookFactory)
    page_number = factory.Sequence(lambda n: n + 1)
    body = "However, the results were inconclusive."
