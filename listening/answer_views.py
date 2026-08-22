"""Creator-facing answer key endpoint.

One view rather than a viewset: the key is a single document owned by its
exercise, addressed only through it, and always written as a whole. There is
no per-answer resource to route to.

The payload is the solution to the question sheet, so access is owner-or-admin
for reads as well as writes - the practice app serves the same answers to
learners through its own endpoint, which is gated on the exercise being
published instead.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.openapi import error_responses

from .answer_key import format_answer_key
from .models import ListeningExercise
from .permissions import IsAnswerKeyOwnerOrAdmin
from .serializers import AnswerKeySerializer, ExerciseAnswerSerializer
from .services import replace_answer_key


class ExerciseAnswerKeyView(APIView):
    """Read or replace the whole answer key for one exercise."""

    permission_classes = [IsAuthenticated, IsAnswerKeyOwnerOrAdmin]
    serializer_class = AnswerKeySerializer

    def get_exercise(self) -> ListeningExercise:
        exercise = get_object_or_404(
            ListeningExercise.objects.select_related("owner"),
            pk=self.kwargs["exercise_id"],
        )
        self.check_object_permissions(self.request, exercise)
        return exercise

    def _payload(self, exercise) -> dict:
        answers = list(exercise.answers.all())
        return {
            "answer_key": format_answer_key(answers),
            "answers": ExerciseAnswerSerializer(answers, many=True).data,
        }

    @extend_schema(
        tags=["listening"],
        summary="Read the answer key",
        description=(
            "Owner or admin only, for reads as well as writes: this is the "
            "solution to the question sheet. Learners read the same answers "
            "through the practice API once the exercise is published."
        ),
        responses={200: AnswerKeySerializer, **error_responses(403, 404)},
        examples=[
            OpenApiExample(
                "A ten-question key",
                response_only=True,
                value={
                    "answer_key": "11. library\n12. 9.30\n13. blue",
                    "answers": [
                        {"number": 11, "text": "library"},
                        {"number": 12, "text": "9.30"},
                        {"number": 13, "text": "blue"},
                    ],
                },
            )
        ],
    )
    def get(self, request, exercise_id: int):
        return Response(self._payload(self.get_exercise()))

    @extend_schema(
        tags=["listening"],
        summary="Replace the answer key",
        description=(
            "Replaces the entire key. Number every line or none of them; an "
            "unnumbered list is numbered from 1 by position. Question numbers "
            "need not start at 1 - a Section 2 sheet is questions 11-20. Send "
            "an empty string to clear the key. Permitted while published: no "
            "segment timing depends on the key."
        ),
        request=AnswerKeySerializer,
        responses={200: AnswerKeySerializer, **error_responses(400, 403, 404)},
        examples=[
            OpenApiExample(
                "Pasted from a printed key",
                request_only=True,
                value={"answer_key": "11. library\n12. 9.30\n13. blue"},
            ),
            OpenApiExample(
                "Unreadable key",
                response_only=True,
                status_codes=["400"],
                value={
                    "code": "INVALID_ANSWER_KEY",
                    "detail": "Number every line or none of them. "
                    "This line has no number: 'blue'.",
                    "extra": {"line": "blue"},
                },
            ),
        ],
    )
    def put(self, request, exercise_id: int):
        exercise = self.get_exercise()
        serializer = AnswerKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        replace_answer_key(exercise, serializer.validated_data["answer_key"])
        return Response(self._payload(exercise))
